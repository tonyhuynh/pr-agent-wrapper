import copy
from contextlib import redirect_stderr
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from support import ROOT, artifact, event, expected, inputs, publisher, review, source


class InputTests(unittest.TestCase):
    def test_defaults_and_fixed_configuration(self):
        config = review.settings(inputs())
        self.assertEqual(config["config.model"], "xai/grok-4.7")
        self.assertEqual(config["config.fallback_models"], [])
        self.assertEqual(config["ignore.glob"], ["vendor/**"])
        self.assertEqual(config["pr_reviewer.num_max_findings"], 3)
        self.assertEqual(config | review.FIXED, config)
        self.assertEqual(review.settings(inputs(ignore_globs='["generated/**"]'))["ignore.glob"], ["vendor/**", "generated/**"])

    def test_invalid_model_and_json_types(self):
        for value in ["grok-4.7", "openrouter/x/y", "xai/a/b", "xai/a:gateway", "xai/../x", "XAI/model", "xai/a?url=x", "xai/a\n", " xai/a", None]:
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                review.settings(inputs(model=value))
        for value in ['{}', 'null', 'false', '[3]', '[true]', '[[]]', '[""]', '["openai/a","openai/a"]', '["xai/grok-4.7"]', '["openai/a"]' * 2]:
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                review.settings(inputs(fallback_models=value))
        with self.assertRaises(ValueError):
            review.settings(inputs(fallback_models=json.dumps(["openai/m" + str(i) for i in range(5)])))
        with self.assertRaises(ValueError):
            review.settings(inputs(ignore_globs=json.dumps([str(i) for i in range(33)])))

    def test_strict_integer_and_bounds(self):
        for value in [0, -1, 11, 3.0, 1.5, True, None, "3"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                review.settings(inputs(num_max_findings=value))
        for value in [1, 10]:
            self.assertEqual(review.settings(inputs(num_max_findings=value))["pr_reviewer.num_max_findings"], value)

    def test_directives_and_bounds(self):
        for value in ["@jinja {{1+1}}", "@str @format {env[HOME]}", '@json "@jinja {{1}}"', "text @FORMAT x", "@get x", "\x00", "\r", "x" * 8193]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                review.settings(inputs(extra_instructions=value))
        for value in ['["@str @jinja x"]', '["\\u0040jinja x"]']:
            with self.assertRaises(ValueError):
                review.settings(inputs(ignore_globs=value))
        text = 'Focus on "the workflow", package.json and docs.\n$(touch /tmp/never) `uname` ${{ hostile }}'
        self.assertEqual(review.settings(inputs(extra_instructions=text))["pr_reviewer.extra_instructions"], text)
        with self.assertRaises(ValueError):
            review.settings({**inputs(), "config.enable_auto_approval": True})

    def test_every_provider_key_required_before_any_call(self):
        config = review.settings(inputs(fallback_models='["openai/a","anthropic/b","gemini/c"]'))
        keys = {public: "test-value-" + provider for provider, (public, native) in review.KEYS.items()}
        result = review.provider_keys(config, keys)
        self.assertEqual(set(result), {native for _, native in review.KEYS.values()})
        self.assertEqual(json.loads(result["GOOGLE_AI_STUDIO__GEMINI_API_KEY"][6:]), keys["GEMINI_API_KEY"])
        for missing in keys:
            for value in ["", "   "]:
                with self.subTest(missing=missing), self.assertRaises(ValueError):
                    review.provider_keys(config, {**keys, missing: value})
        self.assertNotIn("OPENAI__KEY", review.provider_keys(review.settings(inputs()), keys))
        with self.assertRaises(ValueError):
            review.provider_keys(config, {**keys, "XAI_API_KEY": "@jinja x"})

    def test_missing_configured_secret_is_named_without_starting_container(self):
        for provider, (public, _) in review.KEYS.items():
            for missing_value in [None, "", "   "]:
                with self.subTest(provider=provider, value=missing_value), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    (root / "artifact").mkdir()
                    fallbacks = [] if provider == "xai" else [provider + "/test-model"]
                    (root / "config.json").write_text(json.dumps(review.settings(inputs(fallback_models=json.dumps(fallbacks)))))
                    artifact(root / "artifact")
                    environment = {} if provider == "xai" else {"XAI_API_KEY": "stub-key"}
                    if missing_value is not None:
                        environment[public] = missing_value
                    stderr = io.StringIO()
                    with patch.dict(os.environ, environment, clear=True), patch.object(review.subprocess, "run") as run, redirect_stderr(stderr):
                        with self.assertRaises(ValueError):
                            review.infer(root)
                        run.assert_not_called()
                    self.assertEqual(stderr.getvalue(), review.SECRET_ERROR_MESSAGES[public] + "\n")
                    self.assertIn("Missing required workflow secret: " + public, stderr.getvalue())
                    self.assertNotIn("stub-key", stderr.getvalue())
                    self.assertEqual(json.loads((root / "artifact/manifest.json").read_text())["status"], "key_validation_failed")
                    self.assertEqual((root / "artifact/review.md").read_bytes(), b"")

    def test_missing_secret_message_reaches_the_workflow_entrypoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "prw"
            root.mkdir()
            (root / "artifact").mkdir()
            (root / "config.json").write_text(json.dumps(review.settings(inputs(fallback_models='["openai/a"]'))))
            artifact(root / "artifact")
            stderr = io.StringIO()
            with patch.dict(os.environ, {"XAI_API_KEY": "stub-key", "RUNNER_TEMP": directory}, clear=True), patch.object(review.subprocess, "run") as run, patch("sys.argv", ["runtime.py", "infer"]), redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as stopped:
                    exec(compile(source("review"), "workflow-review-runtime", "exec"), {"__name__": "__main__"})
                self.assertEqual(stopped.exception.code, 1)
                run.assert_not_called()
            self.assertEqual(stderr.getvalue(), review.SECRET_ERROR_MESSAGES["OPENAI_API_KEY"] + "\nWrapper step failed; no provider details are logged.\n")

    def test_default_primary_only_succeeds_with_only_xai_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "artifact").mkdir()
            (root / "output").mkdir()
            config = review.settings(inputs())
            self.assertEqual(config["config.fallback_models"], [])
            (root / "config.json").write_text(json.dumps(config))
            artifact(root / "artifact")
            native_markdown = b"Native review\nAgent run details\n- Model: xai/grok-4.7\n"
            starts = []
            def runner(cmd, **kwargs):
                if cmd[1] == "run":
                    starts.append(cmd)
                    self.assertEqual({name for name in kwargs["env"] if name in {key for _, key in review.KEYS.values()}}, {"XAI__KEY"})
                    self.assertEqual(json.loads(kwargs["env"]["CONFIG__FALLBACK_MODELS"][6:]), [])
                    self.assertEqual(cmd[-1], "review")
                    (root / "output/review.md").write_bytes(native_markdown)
                return subprocess.CompletedProcess(cmd, 0)
            stderr = io.StringIO()
            with patch.dict(os.environ, {"XAI_API_KEY": "stub-key"}, clear=True), patch.object(review.subprocess, "run", side_effect=runner), redirect_stderr(stderr):
                review.infer(root)
            self.assertEqual(len(starts), 1)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual((root / "artifact/review.md").read_bytes(), native_markdown)
            self.assertEqual(json.loads((root / "artifact/manifest.json").read_text())["status"], "success")

    def test_unknown_errors_and_response_details_stay_out_of_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "artifact").mkdir()
            (root / "config.json").write_text(json.dumps(review.settings(inputs())))
            artifact(root / "artifact")
            stderr = io.StringIO()
            with patch.dict(os.environ, {"XAI_API_KEY": "stub-key"}, clear=True), patch.object(review.subprocess, "run", side_effect=RuntimeError("stub-key provider response details")) as run, redirect_stderr(stderr):
                with self.assertRaises(ValueError):
                    review.infer(root)
            self.assertTrue(run.called)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual((root / "artifact/review.md").read_bytes(), b"")
            self.assertEqual(json.loads((root / "artifact/manifest.json").read_text())["status"], "inference_failed")

    def test_secret_diagnostic_accepts_only_known_names(self):
        self.assertEqual(set(review.SECRET_ERROR_MESSAGES), {public for public, _ in review.KEYS.values()})
        with self.assertRaises(ValueError):
            review.MissingProviderSecret("untrusted text\n::error::hostile")

    def test_mocked_container_output_never_uploads_a_secret(self):
        footer = b"\nAgent run details\n- Model: xai/grok-4.7\n"
        for payload, expected_status in [(b"review" + footer, "success"), (b"", "empty_output"),
                                         (b"x" * 60001 + footer, "oversized_output"),
                                         (b"stub-key" + footer, "inference_failed"),
                                         (b"\xff" + footer, "inference_failed")]:
            with self.subTest(status=expected_status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "artifact").mkdir()
                (root / "output").mkdir()
                (root / "config.json").write_text(json.dumps(review.settings(inputs())))
                artifact(root / "artifact")
                def runner(cmd, **kwargs):
                    self.assertNotIn("stub-key", " ".join(cmd))
                    self.assertNotIn("GITHUB_TOKEN", kwargs["env"])
                    self.assertNotIn("HTTPS_PROXY", kwargs["env"])
                    if cmd[1] == "run":
                        (root / "output/review.md").write_bytes(payload)
                        self.assertIn("XAI__KEY", kwargs["env"])
                        self.assertNotIn("OPENAI__KEY", kwargs["env"])
                    return subprocess.CompletedProcess(cmd, 0)
                with patch.dict(os.environ, {"XAI_API_KEY": "stub-key", "GITHUB_TOKEN": "host-token", "HTTPS_PROXY": "host-proxy"}, clear=True), patch.object(review.subprocess, "run", side_effect=runner):
                    if expected_status == "success": review.infer(root)
                    else:
                        with self.assertRaises(ValueError): review.infer(root)
                result = json.loads((root / "artifact/manifest.json").read_text())
                self.assertEqual(result["status"], expected_status)
                output = (root / "artifact/review.md").read_bytes()
                self.assertNotIn(b"stub-key", output)
                if expected_status == "oversized_output": self.assertEqual(output, payload)


class ProvenanceTests(unittest.TestCase):
    def test_capture_to_review_to_publish_handoff(self):
        import shutil
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            event_path = temp / "event.json"
            event_path.write_text(json.dumps(event()))
            output = temp / "job-output"
            capture_root = temp / "capture"
            environment = {"INPUTS_JSON": json.dumps(inputs()), "GITHUB_EVENT_PATH": str(event_path),
                           "GITHUB_REPOSITORY": "example/project", "GITHUB_EVENT_NAME": "pull_request_target",
                           "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_OUTPUT": str(output)}
            with patch.dict(os.environ, environment, clear=True), patch.object(review, "git_diff", return_value=("c" * 40, b"patch\n")) as capture:
                review.prepare(capture_root)
            capture.assert_called_once()
            self.assertEqual(capture.call_args.args[0]["head"], "b" * 40)
            outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
            self.assertEqual(json.loads(outputs["providers"]), ["xai"])
            keyed_root = temp / "review"
            shutil.copytree(capture_root, keyed_root)
            native = b"Native review\nAgent run details\n- Model: xai/grok-4.7\n"
            def runner(cmd, **kwargs):
                if cmd[1] == "run":
                    (keyed_root / "output/review.md").write_bytes(native)
                return subprocess.CompletedProcess(cmd, 0)
            with patch.dict(os.environ, {"XAI_API_KEY": "stub-key"}, clear=True), patch.object(review.subprocess, "run", side_effect=runner):
                review.infer(keyed_root)
            expected_meta = publisher.expected_identity(event(), {**environment, "CAPTURED_JSON": json.dumps(outputs)})
            client = PublishTests.Client()
            publisher.publish(keyed_root / "artifact", expected_meta, client)
            self.assertEqual(len(client.comments), 1)
            self.assertTrue(client.comments[0][2].endswith(native.decode()))
            self.assertIn(review.digest(b"patch\n"), client.comments[0][2])

    def test_guard_matrix(self):
        for event_name in ["pull_request_target", "pull_request"]:
            for action in ["opened", "reopened", "synchronize", "ready_for_review"]:
                value = event()
                value["action"] = action
                self.assertEqual(review.identity(value, "example/project", event_name)["head"], "b" * 40)
        for change in ["fork", "draft", "closed", "foreign", "bad_sha", "bad_number", "comment"]:
            value = event()
            pr = value["pull_request"]
            if change == "fork": pr["head"]["repo"]["fork"] = True
            if change == "draft": pr["draft"] = True
            if change == "closed": pr["state"] = "closed"
            if change == "foreign": pr["head"]["repo"]["full_name"] = "outside/project"
            if change == "bad_sha": pr["head"]["sha"] = "--upload-pack=evil"
            if change == "bad_number": value["number"] = True
            if change == "comment": value["action"] = "created"
            with self.subTest(change=change), self.assertRaises(ValueError):
                review.identity(value, "example/project", "pull_request")
        with self.assertRaises(ValueError):
            review.identity(event(), "example/project", "issue_comment")
        value = event()
        value["pull_request"]["head"]["repo"]["clone_url"] = "file:///host"
        self.assertEqual(review.identity(value, "example/project", "pull_request")["repository"], "example/project")

    def test_fixed_git_objects_without_checkout_or_hook_changes(self):
        calls = []
        def runner(cmd, **kwargs):
            calls.append((cmd, kwargs))
            output = b""
            if "cat-file" in cmd: output = b"commit\n"
            if "merge-base" in cmd: output = b"c" * 40 + b"\n"
            if "diff" in cmd: output = b"patch\n"
            if "init" in cmd: Path(cmd[-1]).mkdir()
            return subprocess.CompletedProcess(cmd, 0, output, b"")
        with tempfile.TemporaryDirectory() as directory:
            result = review.git_diff(review.identity(event(), "example/project", "pull_request"), Path(directory), "", runner)
        self.assertEqual(result, ("c" * 40, b"patch\n"))
        flat = " ".join(" ".join(cmd) for cmd, _ in calls)
        for forbidden in ["checkout", "hooksPath", "textconv=true", "refs/pull", "submodule update", "--depth"]:
            self.assertNotIn(forbidden, flat)
        self.assertIn("--no-ext-diff --no-textconv", flat)
        self.assertIn("fetch --no-tags --no-recurse-submodules https://github.com/example/project.git " + "a" * 40 + " " + "b" * 40, flat)
        for cmd, kwargs in calls:
            self.assertEqual(kwargs["env"]["GIT_CONFIG_GLOBAL"], "/dev/null")
            self.assertNotIn("GITHUB_TOKEN", kwargs["env"])

    def test_ambiguous_merge_base_fails(self):
        def runner(cmd, **kwargs):
            if "init" in cmd: Path(cmd[-1]).mkdir()
            output = b"commit\n" if "cat-file" in cmd else b"c" * 40 + b"\n" + b"d" * 40 + b"\n"
            return subprocess.CompletedProcess(cmd, 0, output, b"")
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            review.git_diff(review.identity(event(), "example/project", "pull_request"), Path(directory), "", runner)

    def test_container_boundary(self):
        cmd = review.docker_command(Path("/tmp/example"), {"XAI__KEY", "CONFIG__MODEL"})
        text = " ".join(cmd)
        for expected_text in ["--read-only", "65532:65532", "--cap-drop=ALL", "no-new-privileges", "--pids-limit=128", "--memory=4g", "--cpus=2", "dst=/input,readonly", "dst=/output", "--workdir /scratch"]:
            self.assertIn(expected_text, text)
        for forbidden in [".git", "GITHUB", "--env-file", "docker.sock", "--privileged"]:
            self.assertNotIn(forbidden, text)
        self.assertEqual(cmd[-5:], ["--diff-file", "/input/review.diff", "--output", "/output/review.md", "review"])


class PublishTests(unittest.TestCase):
    class Client:
        def __init__(self):
            self.current = event()["pull_request"]
            self.comments = []
        def get_pr(self, repository, number):
            return self.current
        def create_comment(self, repository, number, body):
            self.comments.append((repository, number, body))
            return {"id": 1}

    def test_new_comment_with_exact_native_markdown(self):
        native = 'Review `$(anything)`\n<script>never evaluated</script>\n\nAgent run details\n- Model: openai/a (fallback)\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            meta = artifact(root, native)
            client = self.Client()
            publisher.publish(root, meta, client)
            self.assertEqual(len(client.comments), 1)
            body = client.comments[0][2]
            self.assertTrue(body.endswith(native))
            for field in ["base", "head", "merge_base", "diff_sha256"]:
                self.assertIn(meta[field], body)

    def test_target_event_identity_uses_payload_shas(self):
        env = {"GITHUB_REPOSITORY": "example/project", "GITHUB_EVENT_NAME": "pull_request_target",
               "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_SHA": "d" * 40,
               "CAPTURED_JSON": json.dumps({"captured": "true", "merge_base": "c" * 40,
                                            "diff_sha256": hashlib.sha256(b"patch\n").hexdigest()})}
        self.assertEqual(publisher.expected_identity(event(), env), expected())
        env["GITHUB_EVENT_NAME"] = "issue_comment"
        with self.assertRaises(ValueError):
            publisher.expected_identity(event(), env)

    def test_advanced_pr_is_reported_without_replacing_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            meta = artifact(root)
            client = self.Client()
            client.current["head"]["sha"] = "d" * 40
            publisher.publish(root, meta, client)
            self.assertIn("PR advanced", client.comments[0][2])
            self.assertIn("b" * 40, client.comments[0][2])

    def test_closed_draft_and_foreign_races_fail_without_comment(self):
        for change in ["draft", "closed", "foreign", "fork"]:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                meta = artifact(root)
                client = self.Client()
                if change == "draft": client.current["draft"] = True
                if change == "closed": client.current["state"] = "closed"
                if change == "foreign": client.current["head"]["repo"]["full_name"] = "outside/project"
                if change == "fork": client.current["head"]["repo"]["fork"] = True
                with self.assertRaises(ValueError):
                    publisher.publish(root, meta, client)
                self.assertFalse(client.comments)

    def test_tampered_artifacts_never_post(self):
        for attack in ["base", "head", "merge_base", "run_id", "run_attempt", "repository", "pr_number", "upstream", "status", "diff", "review", "extra", "symlink", "schema"]:
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                meta = artifact(root)
                manifest = json.loads((root / "manifest.json").read_text())
                if attack in manifest: manifest[attack] = "hostile"
                if attack == "diff": (root / "review.diff").write_bytes(b"changed")
                if attack == "review": (root / "review.md").write_bytes(b"changed")
                if attack == "extra": (root / "extra.py").write_text("raise Exception()")
                if attack == "symlink":
                    (root / "review.md").unlink()
                    (root / "review.md").symlink_to(root / "review.diff")
                (root / "manifest.json").write_text(json.dumps(manifest))
                client = self.Client()
                with self.assertRaises(ValueError): publisher.publish(root, meta, client)
                self.assertFalse(client.comments)

    def test_self_consistent_forged_diff_still_fails_capture_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            meta = artifact(root)
            changed = b"forged patch\n"
            (root / "review.diff").write_bytes(changed)
            manifest = json.loads((root / "manifest.json").read_text())
            manifest["diff_sha256"] = hashlib.sha256(changed).hexdigest()
            (root / "manifest.json").write_text(json.dumps(manifest))
            client = self.Client()
            with self.assertRaises(ValueError): publisher.publish(root, meta, client)
            self.assertFalse(client.comments)

    def test_failures_and_limits_are_explicit(self):
        config = review.settings(inputs())
        footer = "\nAgent run details\n- Model: xai/grok-4.7\n"
        self.assertEqual(review.classify(b"", config), "empty_output")
        self.assertEqual(review.classify(b"no footer", config), "missing_model_details")
        self.assertEqual(review.classify(("x" * 60001 + footer).encode(), config), "oversized_output")
        self.assertEqual(review.classify(footer.encode(), config), "success")
        for status in publisher.STATUSES - {"success"}:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                meta = artifact(root, "", status)
                client = self.Client()
                publisher.publish(root, meta, client)
                self.assertIn("Status: `" + status + "`", client.comments[0][2])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            meta = artifact(root, "x" * 60001 + footer)
            with self.assertRaises(ValueError): publisher.validate(root, meta)

    def test_post_failure_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            meta = artifact(root)
            client = self.Client()
            with patch.object(client, "create_comment", side_effect=OSError("network")) as post:
                with self.assertRaises(OSError): publisher.publish(root, meta, client)
                self.assertEqual(post.call_count, 1)


class WorkflowTests(unittest.TestCase):
    def test_same_runtime_and_permission_separation(self):
        import yaml
        data = yaml.load((ROOT / ".github/workflows/review.yml").read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(set(data["jobs"]), {"capture", "review", "publish"})
        capture = data["jobs"]["capture"]
        reviewer = data["jobs"]["review"]
        publisher_job = data["jobs"]["publish"]
        self.assertEqual(capture["permissions"], {"contents": "read"})
        self.assertEqual(reviewer["permissions"], {})
        self.assertEqual(reviewer["needs"], "capture")
        self.assertEqual(publisher_job["needs"], ["capture", "review"])
        self.assertEqual(publisher_job["permissions"]["pull-requests"], "write")
        self.assertIn("pull_request_target", capture["if"])
        self.assertIn("pull_request", capture["if"])
        for job in [capture, reviewer, publisher_job]:
            self.assertIn("timeout-minutes", job)
            for step in job["steps"]:
                self.assertNotEqual(step.get("uses", "").partition("@")[0].lower(), "actions/checkout")
                self.assertNotIn("${{", step.get("run", ""))
        build = reviewer["steps"][1]
        keyed = reviewer["steps"][2]
        self.assertNotIn("env", build)
        self.assertEqual(set(keyed["env"]), {public for public, _ in review.KEYS.values()})
        for provider, (public, _) in review.KEYS.items():
            self.assertIn("contains(fromJSON(needs.capture.outputs.providers), '" + provider + "')", keyed["env"][public])
            self.assertIn("secrets." + public, keyed["env"][public])
        self.assertNotIn("${{ secrets.", str(capture))
        self.assertNotIn("${{ secrets.", str(publisher_job))
        self.assertNotIn("github.token", str(reviewer))
        self.assertIn("toJSON(needs.capture.outputs)", str(publisher_job))
        for value in (source("review"), source("publish")):
            self.assertNotIn("${{", value)
        import check_tree
        check_tree.check()
