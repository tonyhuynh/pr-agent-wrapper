import copy
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib
import types
import unittest
from unittest.mock import patch

from support import ROOT, artifact, chains, event, expected, inputs, publisher, review, source


class InputTests(unittest.TestCase):
    def test_defaults_and_fixed_configuration(self):
        config = review.settings(inputs())
        self.assertEqual(config["config.model"], "xai/grok-4.7")
        self.assertEqual(config["config.fallback_models"], [])
        self.assertEqual(config["ignore.glob"], review.ALWAYS_IGNORED)
        self.assertEqual(review.ALWAYS_IGNORED[0], "vendor/**")
        self.assertIn("**/package-lock.json", review.ALWAYS_IGNORED)
        self.assertEqual(config["pr_reviewer.num_max_findings"], 3)
        self.assertEqual(config | review.FIXED, config)
        self.assertEqual(review.settings(inputs(ignore_globs='["generated/**"]'))["ignore.glob"], [*review.ALWAYS_IGNORED, "generated/**"])
        # A pattern the caller repeats is kept once.
        self.assertEqual(review.settings(inputs(ignore_globs='["**/go.sum"]'))["ignore.glob"], review.ALWAYS_IGNORED)

    def test_invalid_model_and_json_types(self):
        for value in ["grok-4.7", "bedrock/x", "xai/a/b", "xai/a:gateway", "xai/../x", "XAI/model", "xai/a?url=x", "xai/a\n", " xai/a", None, "",
                      "xai/", "openrouter/", "openrouter/x/../y", "openrouter//y", "openrouter/x/y:", "openrouter/x:a/y", "openrouter/x/y?z", "xai/" + "a" * 129]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                review.settings(inputs(model=value))
        for value in ["openrouter/x-ai/grok-4.6", "openrouter/meta/llama-4:free", "groq/llama-4", "groq/openai/gpt-oss-120b", "deepseek/deepseek-chat", "mistral/mistral-large-latest", "xai/" + "a" * 128]:
            with self.subTest(value=value):
                self.assertEqual(review.settings(inputs(model=value))["config.model"], value)
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
        for value in ["@jinja {{1+1}}", "@str @format {env[HOME]}", '@json "@jinja {{1}}"', "text @FORMAT x", "@get x", "\x00", "\r", "x" * 8193,
                      "@Override first", "  \n@Override after whitespace", "see @read_file /etc/passwd", "a @merge_unique b", "x@json"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                review.settings(inputs(extra_instructions=value))
        for value in ["Do not flag a missing @Override.", "Types come from @types/node; ask user@example.com.",
                      "Ignore @pytest.fixture and @formatter:off markers.", "Use @getter and @internal tags."]:
            with self.subTest(value=value):
                self.assertEqual(review.settings(inputs(extra_instructions=value))["pr_reviewer.extra_instructions"], review.instructions(value, review.ALWAYS_IGNORED))
        for value in ['["@str @jinja x"]', '["\\u0040jinja x"]']:
            with self.assertRaises(ValueError):
                review.settings(inputs(ignore_globs=value))
        text = 'Focus on "the workflow", package.json and docs.\n$(touch /tmp/never) `uname` ${{ hostile }}'
        self.assertEqual(review.settings(inputs(extra_instructions=text))["pr_reviewer.extra_instructions"], review.instructions(text, review.ALWAYS_IGNORED))
        # The model is told which files it cannot see, after the caller's own text.
        note = 'Files matching these patterns were deliberately left out of this diff: ' + json.dumps([*review.ALWAYS_IGNORED, "package-lock.json"]) + '. Do not report them as missing, and do not report a missing change to them.'
        self.assertEqual(review.settings(inputs(ignore_globs='["package-lock.json"]'))["pr_reviewer.extra_instructions"], note)
        self.assertEqual(review.settings(inputs(ignore_globs='["package-lock.json"]', extra_instructions="Be brief."))["pr_reviewer.extra_instructions"], "Be brief.\n\n" + note)
        with self.assertRaises(ValueError):
            review.settings({**inputs(), "config.enable_auto_approval": True})

    def test_optional_tuning_inputs(self):
        config = review.settings(inputs())
        self.assertEqual((config["config.ai_timeout"], config["config.max_model_tokens"], config["config.response_language"]), (300, 64000, "en-US"))
        for absent in ["config.custom_model_max_tokens", "config.reasoning_effort", "openai.api_base"]:
            self.assertNotIn(absent, config)
        config = review.settings(inputs(model="openai/local-model", api_base="https://llm.example.com:8443/v1", custom_model_max_tokens=128000,
                                        max_model_tokens=64000, reasoning_effort="high", response_language="pt-BR", ai_timeout=300, comment_mode="new"))
        self.assertEqual(config["openai.api_base"], "https://llm.example.com:8443/v1")
        self.assertEqual(review.serialize(config)["OPENAI__API_BASE"], '"https://llm.example.com:8443/v1"')
        self.assertEqual((config["config.custom_model_max_tokens"], config["config.max_model_tokens"], config["config.reasoning_effort"],
                          config["config.response_language"], config["config.ai_timeout"]), (128000, 64000, "high", "pt-BR", 300))
        self.assertNotIn("comment_mode", " ".join(config))
        bad = {"api_base": ["http://llm.example.com/v1", "https://user:pass@llm.example.com", "https://llm.example.com/v1?key=x", "https://llm.example.com/#x",
                            "file:///etc/passwd", "https://", "https://-bad.example.com", "https://llm.example.com/a b", 7,
                            "https://127.0.0.1/v1", "https://169.254.169.254/latest", "https://2130706433/v1", "https://localhost/v1", "https://intranet/v1",
                            "https://api.localhost/v1", "https://llm.corp.internal/v1", "https://printer.local", "https://a..example.com", "https://a-.example.com",
                            "https://llm.example.com:0/v1", "https://llm.example.com:65536/v1", "https://llm.example.com:99999"],
               "custom_model_max_tokens": [-1, 999, 2000001, 1.5, True, "128000", None],
               "max_model_tokens": [0, 3999, 2000001, True, "32000"],
               "reasoning_effort": ["extreme", "High", " high", 3, None],
               "response_language": ["english", "en_US", "en-us", "", None],
               "ai_timeout": [0, 29, 541, 120.0, True],
               "comment_mode": ["", "replace", "UPDATE", None],
               "large_pr_chunking": ["true", 1, 0, None, ""]}
        for name, values in bad.items():
            for value in values:
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    review.settings(inputs(model="openai/local-model", **{name: value}))
        # api_base redirects only openai/ models, so it is rejected when none is configured.
        with self.assertRaises(ValueError):
            review.settings(inputs(api_base="https://llm.example.com/v1"))
        review.settings(inputs(fallback_models='["openai/a"]', api_base="https://llm.example.com/v1"))

    def test_input_errors_name_the_input_without_echoing_the_value(self):
        cases = {"model": "not-a-model-SENTINEL", "fallback_models": '["SENTINEL"', "extra_instructions": "@jinja SENTINEL",
                 "num_max_findings": 99, "ignore_globs": '{"SENTINEL": 1}', "api_base": "http://SENTINEL", "custom_model_max_tokens": 5,
                 "max_model_tokens": 1, "reasoning_effort": "SENTINEL", "response_language": "SENTINEL", "ai_timeout": 1, "comment_mode": "SENTINEL", "large_pr_chunking": "SENTINEL"}
        self.assertEqual(set(cases), review.INPUT_NAMES)
        for name, value in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                stdout, stderr = io.StringIO(), io.StringIO()
                environment = {"RUNNER_TEMP": directory, "INPUTS_JSON": json.dumps(inputs(**{name: value}))}
                with patch.dict(os.environ, environment, clear=True), patch("sys.argv", ["runtime.py", "prepare"]), redirect_stdout(stdout), redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as stopped:
                        exec(compile(source("review"), "workflow-review-runtime", "exec"), {"__name__": "__main__"})
                self.assertEqual(stopped.exception.code, 1)
                self.assertTrue(stdout.getvalue().startswith("::error title=PR-Agent wrapper::Invalid input `" + name + "`: "), stdout.getvalue())
                self.assertEqual(stdout.getvalue().count("\n"), 1)
                self.assertNotIn("SENTINEL", stdout.getvalue() + stderr.getvalue())

    def test_every_provider_key_required_before_any_call(self):
        keys = {public: "test-value-" + provider for provider, (public, native) in review.KEYS.items()}
        covered = set()
        for group, chain in chains():
            config = review.settings(chain)
            result = review.provider_keys(config, keys)
            # Only the providers in this chain get a key, and each of those is required.
            self.assertEqual(set(result), {review.KEYS[provider][1] for provider in group})
            covered.update(result)
            for provider in group:
                for value in ["", "   "]:
                    with self.subTest(missing=provider), self.assertRaises(ValueError):
                        review.provider_keys(config, {**keys, review.KEYS[provider][0]: value})
        self.assertEqual(covered, {native for _, native in review.KEYS.values()})
        config = review.settings(inputs(fallback_models='["openai/a","anthropic/b","gemini/c"]'))
        result = review.provider_keys(config, keys)
        self.assertEqual(tomllib.loads("key=" + result["GOOGLE_AI_STUDIO__GEMINI_API_KEY"])["key"], keys["GEMINI_API_KEY"])
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
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, {"XAI_API_KEY": "stub-key", "RUNNER_TEMP": directory}, clear=True), patch.object(review.subprocess, "run") as run, patch("sys.argv", ["runtime.py", "infer"]), redirect_stdout(stdout), redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as stopped:
                    exec(compile(source("review"), "workflow-review-runtime", "exec"), {"__name__": "__main__"})
                self.assertEqual(stopped.exception.code, 1)
                run.assert_not_called()
            self.assertEqual(stderr.getvalue(), review.SECRET_ERROR_MESSAGES["OPENAI_API_KEY"] + "\n")
            self.assertEqual(stdout.getvalue(), "::error title=PR-Agent wrapper::Review failed: key_validation_failed\n")

    def test_unexpected_errors_keep_the_generic_message(self):
        with tempfile.TemporaryDirectory() as directory:
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, {"RUNNER_TEMP": directory, "INPUTS_JSON": "{not json SENTINEL"}, clear=True), patch("sys.argv", ["runtime.py", "prepare"]), redirect_stdout(stdout), redirect_stderr(stderr):
                with self.assertRaises(SystemExit):
                    exec(compile(source("review"), "workflow-review-runtime", "exec"), {"__name__": "__main__"})
            self.assertEqual((stdout.getvalue(), stderr.getvalue()), ("", "Wrapper step failed; no provider details are logged.\n"))

    def test_primary_only_chain_succeeds_with_only_its_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "artifact").mkdir()
            (root / "output").mkdir()
            config = review.settings(inputs())
            self.assertEqual(config["config.fallback_models"], [])
            (root / "config.json").write_text(json.dumps(config))
            artifact(root / "artifact")
            native_markdown = b"Native review\n\xe2\x9a\x99\xef\xb8\x8f Agent run details\n- Model: xai/grok-4.7\n"
            starts = []
            run_timeouts = []
            def runner(cmd, **kwargs):
                if cmd[1] == "run":
                    run_timeouts.append(kwargs["timeout"])
                    starts.append(cmd)
                    self.assertEqual({name for name in kwargs["env"] if name in {key for _, key in review.KEYS.values()}}, {"XAI__KEY"})
                    self.assertEqual(tomllib.loads("key=" + kwargs["env"]["CONFIG__FALLBACK_MODELS"])["key"], [])
                    self.assertEqual(cmd[-1], "review")
                    (root / "output/review.md").write_bytes(native_markdown)
                return subprocess.CompletedProcess(cmd, 0)
            stderr = io.StringIO()
            with patch.dict(os.environ, {"XAI_API_KEY": "stub-key"}, clear=True), patch.object(review.subprocess, "run", side_effect=runner), redirect_stderr(stderr):
                review.infer(root)
            self.assertEqual(run_timeouts, [600])
            self.assertEqual(len(starts), 1)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual((root / "artifact/review.md").read_bytes(), native_markdown)
            self.assertEqual(json.loads((root / "artifact/manifest.json").read_text())["status"], "success")

    def test_container_timeout_covers_the_whole_chain(self):
        for changes, expected_limit in [({"ai_timeout": 540}, 600), ({"ai_timeout": 540, "fallback_models": '["openai/a"]'}, 1140),
                                        ({"ai_timeout": 540, "fallback_models": '["openai/a","openai/b","openai/c"]'}, 1500),
                                        ({"ai_timeout": 120, "large_pr_chunking": True}, 600), ({"ai_timeout": 300, "large_pr_chunking": True}, 960),
                                        ({"ai_timeout": 300, "large_pr_chunking": True, "fallback_models": '["openai/a"]'}, 1500)]:
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "artifact").mkdir()
                (root / "config.json").write_text(json.dumps(review.settings(inputs(**changes))))
                artifact(root / "artifact")
                limits = []
                def runner(cmd, **kwargs):
                    if cmd[1] == "run": limits.append(kwargs["timeout"])
                    return subprocess.CompletedProcess(cmd, 1)
                with patch.dict(os.environ, {"XAI_API_KEY": "stub-key", "OPENAI_API_KEY": "stub-key-2"}, clear=True), patch.object(review.subprocess, "run", side_effect=runner), redirect_stderr(io.StringIO()):
                    with self.assertRaises(ValueError): review.infer(root)
                self.assertEqual(limits, [expected_limit])

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

    def test_diff_summary_lists_the_largest_files_and_hides_unsafe_paths(self):
        def entry(name, lines):
            return ("diff --git a/" + name + " b/" + name + "\n--- a/" + name + "\n+++ b/" + name + "\n@@ -0,0 +1 @@\n" + "+x\n" * lines).encode()
        hostile = ["x|y.txt", "a`b.txt", "<img src=x>.md", "new\\nline.txt", "$(id).sh", "\u00e9.txt"]
        patch_bytes = b"".join([entry("src/data/big.json", 4000), entry("src/app/[id]/(group)/page two.tsx", 2000), entry("small.txt", 1),
                                *[entry(name, 1000 + index) for index, name in enumerate(hostile)],
                                b'diff --git "a/q\\303\\251.txt" "b/q\\303\\251.txt"\n' + b"+x\n" * 1500])
        text = review.diff_summary(patch_bytes, [])
        rows = [line for line in text.splitlines() if line.startswith("| ") and "KiB" in line]
        self.assertIn("Captured diff: 10 file(s), ", text)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0], "| `src/data/big.json` | 12 KiB |")
        self.assertEqual(rows[1], "| `src/app/[id]/(group)/page two.tsx` | 6 KiB |")
        self.assertEqual({row.split(" | ")[0] for row in rows[2:]}, {"| (path not shown)"})
        self.assertNotIn("left out", text)
        for name in hostile:
            self.assertNotIn(name, text)
        # A diff line that only looks like a header is content, not a file.
        self.assertIn("Captured diff: 1 file(s), ", review.diff_summary(entry("a.txt", 1) + b"+diff --git a/x b/x\n", []))
        self.assertNotIn("Largest files", review.diff_summary(b"", []))

    def test_diff_summary_reports_what_ignore_globs_leave_out(self):
        def entry(name, lines):
            return ("diff --git a/" + name + " b/" + name + "\n--- a/" + name + "\n+++ b/" + name + "\n@@ -0,0 +1 @@\n" + "+x\n" * lines).encode()
        globs = review.settings(inputs(ignore_globs='["src/data/*.json", "goals/**/evidence/**"]'))["ignore.glob"]
        patch_bytes = b"".join([entry("src/data/quotes.json", 5000), entry("goals/evidence/a.json", 900), entry("goals/voice/evidence/b.json", 800),
                                entry("package-lock.json", 700), entry("web/package-lock.json", 600), entry("vendor/lib/x.go", 500),
                                entry("src/app.ts", 3000), entry("src/data/readme.md", 100)])
        text = review.diff_summary(patch_bytes, globs)
        self.assertIn("leave out 6 file(s), ", text)
        self.assertIn("PR-Agent receives 2 file(s), ", text)
        sent, left = text.split("| Largest files left out | Diff size |")
        self.assertEqual([line.split("`")[1] for line in sent.splitlines() if line.startswith("| `")], ["src/app.ts", "src/data/readme.md"])
        self.assertEqual([line.split("`")[1] for line in left.splitlines() if line.startswith("| `")],
                         ["src/data/quotes.json", "goals/evidence/a.json", "goals/voice/evidence/b.json", "package-lock.json", "web/package-lock.json"])

    def test_failed_review_reports_only_a_fixed_reason(self):
        upstream_reason, (code, message) = next(iter(review.FAILURE_REASONS.items()))
        known = ("Failed to review PR\n\n**Reason:** " + upstream_reason).encode()
        for payload, suffix, reason in [(known, ". " + message, code), (known + b"\n", ". " + message, code), (b"Failed to review PR", "", ""),
                                        (b"Failed to review PR\n\n**Reason:** provider said hostile\n::error::x", "", ""),
                                        (known + b" extra provider text", "", "")]:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "artifact").mkdir()
                (root / "output").mkdir()
                (root / "config.json").write_text(json.dumps(review.settings(inputs())))
                artifact(root / "artifact")
                def runner(cmd, **kwargs):
                    if cmd[1] == "run": (root / "output/review.md").write_bytes(payload)
                    return subprocess.CompletedProcess(cmd, 1)
                with patch.dict(os.environ, {"XAI_API_KEY": "stub-key"}, clear=True), patch.object(review.subprocess, "run", side_effect=runner):
                    with self.assertRaises(review.WrapperError) as raised: review.infer(root)
                self.assertEqual(str(raised.exception), "Review failed: inference_failed" + suffix)
                self.assertEqual(json.loads((root / "artifact/manifest.json").read_text())["reason"], reason)

    def test_settings_are_encoded_as_toml_literals(self):
        for value in [True, False, 0, 32000, "", "xai/grok-4.7", 'quote " backslash \\ tab \t newline \n del \x7f', "caf\u00e9 \U0001f600 \u2028",
                      [], ["vendor/**", "a b"], "true", "123", "[1]"]:
            with self.subTest(value=value):
                self.assertEqual(tomllib.loads("key=" + review.encode(value))["key"], value)
                self.assertNotIn("\n", review.encode(value))
        for value in [None, 1.5, {"a": 1}, ["a", {"b": 1}]]:
            with self.assertRaises(ValueError): review.encode(value)

    def test_chunked_reviews_may_name_several_chain_models(self):
        plain = review.settings(inputs(fallback_models='["openai/a"]'))
        chunked = review.settings(inputs(fallback_models='["openai/a"]', large_pr_chunking=True))
        self.assertEqual((plain["pr_reviewer.enable_large_pr_chunking"], chunked["pr_reviewer.enable_large_pr_chunking"]), (False, True))
        self.assertEqual(chunked["pr_reviewer.max_number_of_calls"], 3)
        body = "Review\n\n\u2699\ufe0f Agent run details\n"
        cases = [("- Model: xai/grok-4.7\n", "success", "success"), ("- Model: openai/a (fallback)\n", "success", "success"),
                 ("- Models: xai/grok-4.7, openai/a (includes fallback)\n", "missing_model_details", "success"),
                 ("- Models: xai/grok-4.7, openai/a\n", "missing_model_details", "success"),
                 ("- Models: xai/grok-4.7, openai/other\n", "missing_model_details", "missing_model_details"),
                 ("- Models: xai/grok-4.7, xai/grok-4.7\n", "missing_model_details", "missing_model_details"),
                 ("- Models: xai/grok-4.7\n", "success", "success"), ("- Modelo: openai/a (respaldo)\n", "success", "success"),
                 ("- Model: openai/a\n", "missing_model_details", "missing_model_details"), ("- Model: xai/grok-4.7 (fallback)\n", "missing_model_details", "missing_model_details"),
                 ("- Tokens: 1 in\n- Model: xai/grok-4.7\n", "missing_model_details", "missing_model_details")]
        for footer, without, with_chunking in cases:
            with self.subTest(footer=footer):
                self.assertEqual(review.classify((body + footer).encode(), plain), without)
                self.assertEqual(review.classify((body + footer).encode(), chunked), with_chunking)

    def test_secret_diagnostic_accepts_only_known_names(self):
        self.assertEqual(set(review.SECRET_ERROR_MESSAGES), {public for public, _ in review.KEYS.values()})
        with self.assertRaises(ValueError):
            review.MissingProviderSecret("untrusted text\n::error::hostile")

    def test_mocked_container_output_never_uploads_a_secret(self):
        footer = b"\n\xe2\x9a\x99\xef\xb8\x8f Agent run details\n- Model: xai/grok-4.7\n"
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
            summary = temp / "job-summary"
            capture_root = temp / "capture"
            environment = {"INPUTS_JSON": json.dumps(inputs()), "GITHUB_EVENT_PATH": str(event_path),
                           "GITHUB_REPOSITORY": "example/project", "GITHUB_EVENT_NAME": "pull_request_target",
                           "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_OUTPUT": str(output),
                           "GITHUB_STEP_SUMMARY": str(summary)}
            with patch.dict(os.environ, environment, clear=True), patch.object(review, "git_diff", return_value=("c" * 40, b"patch\n")) as capture:
                review.prepare(capture_root)
            capture.assert_called_once()
            self.assertIn("Captured diff: 0 file(s), 1 KiB.", summary.read_text())
            self.assertEqual(capture.call_args.args[0]["head"], "b" * 40)
            outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
            self.assertEqual(json.loads(outputs["providers"]), ["xai"])
            keyed_root = temp / "review"
            shutil.copytree(capture_root, keyed_root)
            native = b"Native review\n\xe2\x9a\x99\xef\xb8\x8f Agent run details\n- Model: xai/grok-4.7\n"
            def runner(cmd, **kwargs):
                if cmd[1] == "run":
                    (keyed_root / "output/review.md").write_bytes(native)
                return subprocess.CompletedProcess(cmd, 0)
            with patch.dict(os.environ, {"XAI_API_KEY": "stub-key"}, clear=True), patch.object(review.subprocess, "run", side_effect=runner):
                review.infer(keyed_root)
            expected_meta = publisher.expected_identity(event(), {**environment, "CAPTURED_JSON": json.dumps(outputs)})
            client = PublishTests.Client()
            with redirect_stdout(io.StringIO()):
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
        def __init__(self, existing=()):
            self.current = event()["pull_request"]
            self.comments = []
            self.updates = []
            self.existing = list(existing)
            self.pages = []
        def get_pr(self, repository, number):
            return self.current
        def list_comments(self, repository, number, page):
            self.pages.append(page)
            return self.existing[(page - 1) * 20:page * 20]
        def create_comment(self, repository, number, body):
            self.comments.append((repository, number, body))
            return {"id": 1}
        def update_comment(self, repository, comment_id, body):
            self.updates.append((repository, comment_id, body))
            return {"id": comment_id}

    def setUp(self):
        # publish() reports through the job summary and stdout; keep test output quiet.
        self.stdout = io.StringIO()
        redirect = redirect_stdout(self.stdout)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    @staticmethod
    def comment(comment_id, body, login="github-actions[bot]", kind="Bot"):
        return {"id": comment_id, "body": body, "user": {"login": login, "type": kind}}

    def test_update_mode_edits_only_its_own_newest_comment(self):
        marker = publisher.MARKER
        others = [self.comment(1, marker + "\nspoofed by a person", "mallory", "User"),
                  self.comment(2, "quoting " + marker), self.comment(3, marker + "\nold review"),
                  self.comment(4, marker + "\nother bot", "dependabot[bot]"), self.comment(5, marker + "\nnewer review"),
                  self.comment(6, marker + "\nnot a bot account", "github-actions[bot]", "User"), self.comment(7, "unrelated")]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            meta = artifact(root)
            client = self.Client(others)
            publisher.publish(root, meta, client, "update")
            self.assertEqual((len(client.updates), client.comments), (1, []))
            self.assertEqual(client.updates[0][:2], ("example/project", 5))
            self.assertTrue(client.updates[0][2].startswith(marker + "\n"))
            client = self.Client(others)
            publisher.publish(root, meta, client, "new")
            self.assertEqual((client.updates, len(client.comments), client.pages), ([], 1, []))
            client = self.Client([c for c in others if c["id"] not in {3, 5}])
            publisher.publish(root, meta, client, "update")
            self.assertEqual((client.updates, len(client.comments)), ([], 1))
            # The newest own comment is found past the first page of a long conversation.
            client = self.Client([self.comment(i, "chatter") for i in range(100, 220)] + [self.comment(999, marker + "\nlate")])
            publisher.publish(root, meta, client, "update")
            self.assertEqual((client.updates[0][1], client.pages), (999, [1, 2, 3, 4, 5, 6, 7]))
            for mode in ["", "replace", None]:
                client = self.Client(others)
                with self.assertRaises(ValueError): publisher.publish(root, meta, client, mode)
                self.assertFalse(client.comments or client.updates)

    def test_update_mode_never_overwrites_a_newer_push(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            meta = artifact(root)
            client = self.Client([self.comment(5, publisher.MARKER + "\nHead: `" + "d" * 40 + "`\nreview of the newer head")])
            client.current["head"]["sha"] = "d" * 40
            self.assertIsNone(publisher.publish(root, meta, client, "update"))
            self.assertFalse(client.comments or client.updates)
            self.assertIn("newer head", self.stdout.getvalue())
            # No run reviewed the newer head (a bot push triggers none), so this review still lands, with the note.
            client = self.Client([self.comment(5, publisher.MARKER + "\nHead: `" + "f" * 40 + "`\nolder review")])
            client.current["head"]["sha"] = "d" * 40
            publisher.publish(root, meta, client, "update")
            self.assertEqual((client.updates[0][1], client.comments), (5, []))
            self.assertIn("PR advanced", client.updates[0][2])
            # A moved base alone still publishes, with the note.
            client = self.Client()
            client.current["base"]["sha"] = "e" * 40
            publisher.publish(root, meta, client, "update")
            self.assertIn("PR advanced", client.comments[0][2])

    def test_new_comment_with_exact_native_markdown(self):
        native = 'Review `$(anything)`\n<script>never evaluated</script>\n\n\u2699\ufe0f Agent run details\n- Model: openai/a (fallback)\n'
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
               "CAPTURED_JSON": json.dumps({"captured": "true", "merge_base": "c" * 40, "run_attempt": "2",
                                            "diff_sha256": hashlib.sha256(b"patch\n").hexdigest()})}
        self.assertEqual(publisher.expected_identity(event(), env), expected())
        # A rerun of only the failed jobs has a later attempt number; the identity keeps the attempt that captured the diff.
        self.assertEqual(publisher.expected_identity(event(), {**env, "GITHUB_RUN_ATTEMPT": "5"}), expected())
        env["GITHUB_EVENT_NAME"] = "issue_comment"
        with self.assertRaises(ValueError):
            publisher.expected_identity(event(), env)

    def test_advanced_pr_is_reported_without_replacing_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            meta = artifact(root)
            client = self.Client()
            client.current["head"]["sha"] = "d" * 40
            publisher.publish(root, meta, client, "new")
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

    def test_comment_listing_is_bounded(self):
        # Other bots can fill a PR with comments; the search stops after 50 pages of 20.
        others = [self.comment(i, "unrelated") for i in range(1, 1201)]
        client = self.Client(others)
        self.assertIsNone(publisher.own_comment(client, "example/repository", 7))
        self.assertEqual(client.pages, list(range(1, 51)))
        # The newest marked comment within those pages wins; one past the cap is not seen.
        marked = [self.comment(i, publisher.MARKER + "\nreview " + str(i)) if i in {3, 999, 1001} else self.comment(i, "unrelated") for i in range(1, 1201)]
        self.assertEqual(publisher.own_comment(self.Client(marked), "example/repository", 7), (999, publisher.MARKER + "\nreview 999"))
        with self.assertRaises(ValueError): publisher.own_comment(types.SimpleNamespace(list_comments=lambda *args: {"message": "not a list"}), "example/repository", 7)

    def test_api_responses_over_the_size_limit_are_rejected(self):
        class Response:
            def __init__(self, data): self.data = data
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, count): return self.data[:count]
        seen = []
        def opener_for(data):
            def open_(request, timeout):
                seen.append((request.full_url, request.get_header("Authorization")))
                return Response(data)
            return types.SimpleNamespace(open=open_)
        client = publisher.GitHubClient("stub-token")
        with patch.object(publisher.urllib.request, "build_opener", return_value=opener_for(b"[]")):
            self.assertEqual(client.list_comments("example/repository", 7, 2), [])
        self.assertEqual(seen, [("https://api.github.com/repos/example/repository/issues/7/comments?per_page=20&page=2", "Bearer stub-token")])
        with patch.object(publisher.urllib.request, "build_opener", return_value=opener_for(b"[" + b" " * (8 * 1024 * 1024) + b"]")):
            with self.assertRaises(ValueError): client.list_comments("example/repository", 7, 1)
        with patch.object(publisher.urllib.request, "build_opener", return_value=opener_for(b"{" + b" " * (2 * 1024 * 1024) + b"}")):
            with self.assertRaises(ValueError): client.get_pr("example/repository", 7)

    def test_only_the_final_run_details_block_names_the_model(self):
        config = review.settings(inputs())
        footer = "\n\u2699\ufe0f Agent run details\n\n- Model: xai/grok-4.7\n- Tokens: 1 in\n\n</details>\n"
        cases = [("Review\n- Provider: xai/grok-4.7\n" + footer, True),
                 ("Review\n\u2699 Settings\n- Provider: openai/other\n" + footer, True),
                 ("Review\n- Recommendation: xai/grok-4.7\nmore text\n", False),
                 ("Review\n" + footer + "\n\u2699 Late block\n- Provider: not a model\n", False),
                 ("Review\n\u2699\ufe0f Agent run details\n- Tokens: 1 in\n- Model: xai/grok-4.7\n", False)]
        for text, genuine in cases:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as directory:
                self.assertEqual(review.classify(text.encode(), config), "success" if genuine else "missing_model_details")
                meta = artifact(Path(directory), text)
                if genuine:
                    publisher.validate(Path(directory), meta)
                else:
                    with self.assertRaises(ValueError): publisher.validate(Path(directory), meta)

    def test_failures_and_limits_are_explicit(self):
        config = review.settings(inputs())
        footer = "\n\u2699\ufe0f Agent run details\n- Model: xai/grok-4.7\n"
        self.assertEqual(review.classify(b"", config), "empty_output")
        self.assertEqual(review.classify(b"no footer", config), "missing_model_details")
        self.assertEqual(review.classify(("x" * 60001 + footer).encode(), config), "oversized_output")
        self.assertEqual(review.classify(footer.encode(), config), "success")
        for status in publisher.STATUSES - {"success"}:
            for mode in ["update", "new"]:
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    meta = artifact(root, "", status)
                    client = self.Client([self.comment(5, publisher.MARKER + "\nlast good review")])
                    summary = root.parent / (root.name + "-summary")
                    with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}):
                        publisher.publish(root, meta, client, mode)
                    # A failed run posts a fixed notice and keeps the last good review under it.
                    notice = publisher.failure_notice({"status": status, "reason": ""}, meta, "last good review" if mode == "update" else "")
                    self.assertIn(publisher.STATUS_TEXT[status], notice)
                    self.assertEqual((client.comments, client.updates),
                                     ([], [("example/project", 5, notice)]) if mode == "update" else ([("example/project", 17, notice)], []))
                    self.assertIn("Status: `" + status + "`", summary.read_text())
                    summary.unlink()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            meta = artifact(root, "x" * 60001 + footer)
            with self.assertRaises(ValueError): publisher.validate(root, meta)

    def test_failure_notice_lifecycle(self):
        footer = "\n\u2699\ufe0f Agent run details\n- Model: xai/grok-4.7\n"
        self.assertEqual(set(publisher.STATUS_TEXT), publisher.STATUSES - {"success"})
        self.assertEqual(publisher.REASON_TEXT, dict(review.FAILURE_REASONS.values()))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # No earlier comment: the notice is the whole comment, with the reason and a link to the run.
            meta = artifact(root, "", "inference_failed", "timeout")
            client = self.Client()
            publisher.publish(root, meta, client)
            body = client.comments[0][2]
            self.assertTrue(body.startswith(publisher.MARKER + "\n" + publisher.NOTICE + "\n> **PR-Agent could not review commit `" + "b" * 40 + "`.** The provider timed out."))
            self.assertIn("> [Run log](https://github.com/example/project/actions/runs/123)\n" + publisher.NOTICE_END, body)
            self.assertNotIn("earlier run", body)
            # A second failure replaces the notice instead of stacking a new one.
            artifact(root, "", "empty_output")
            client = self.Client([self.comment(5, body)])
            publisher.publish(root, meta, client)
            again = client.updates[0][2]
            self.assertEqual(again.count(publisher.NOTICE), 1)
            self.assertIn(publisher.STATUS_TEXT["empty_output"], again)
            self.assertNotIn("timed out", again)
            # A failure after a good review keeps that review, and a second failure does not duplicate it.
            artifact(root)
            client = self.Client()
            publisher.publish(root, meta, client)
            good = client.comments[0][2]
            artifact(root, "", "inference_failed", "auth")
            for existing in [good, None]:
                client = self.Client([self.comment(5, existing or noticed)])
                publisher.publish(root, meta, client)
                noticed = client.updates[0][2]
                self.assertEqual(noticed, publisher.failure_notice({"status": "inference_failed", "reason": "auth"}, meta, good[len(publisher.MARKER) + 1:]))
                self.assertTrue(noticed.endswith(good[len(publisher.MARKER) + 1:]))
                self.assertIn("> The review below is from an earlier run.", noticed)
            # The next good review replaces the notice.
            artifact(root)
            client = self.Client([self.comment(5, noticed)])
            publisher.publish(root, meta, client)
            self.assertEqual(client.updates[0][2], good)
            # A failed run for a head the PR has moved past stays quiet; the newer run reports.
            artifact(root, "", "inference_failed", "timeout")
            client = self.Client([self.comment(5, good)])
            client.current = {**client.current, "head": {**client.current["head"], "sha": "e" * 40}}
            self.assertIsNone(publisher.publish(root, meta, client))
            self.assertFalse(client.comments or client.updates)
            # A comment with no room for the notice is left alone.
            client = self.Client([self.comment(5, publisher.MARKER + "\n" + "x" * 64900)])
            self.assertIsNone(publisher.publish(root, meta, client))
            self.assertFalse(client.comments or client.updates)
            # The reason is a known code, and only an inference failure carries one.
            for status, reason in [("inference_failed", "SENTINEL"), ("empty_output", "timeout"), ("success", "timeout")]:
                artifact(root, "" if status != "success" else "Native review" + footer, status, reason)
                with self.assertRaises(ValueError): publisher.validate(root, meta)

    def test_post_failure_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            meta = artifact(root)
            client = self.Client()
            with patch.object(client, "create_comment", side_effect=OSError("network")) as post:
                with self.assertRaises(OSError): publisher.publish(root, meta, client)
                self.assertEqual(post.call_count, 1)
            client = self.Client([self.comment(5, publisher.MARKER + "\nold")])
            with patch.object(client, "update_comment", side_effect=OSError("network")) as edit:
                with self.assertRaises(OSError): publisher.publish(root, meta, client)
                self.assertEqual(edit.call_count, 1)
            self.assertFalse(client.comments)

    def test_openrouter_footer_is_publishable(self):
        native = "Review\n\n\u2699\ufe0f Agent run details\n- Model: openrouter/x-ai/grok-4.6:free (fallback)\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            meta = artifact(root, native)
            client = self.Client()
            publisher.publish(root, meta, client)
            self.assertTrue(client.comments[0][2].endswith(native))
        config = review.settings(inputs(fallback_models='["openrouter/x-ai/grok-4.6:free"]'))
        self.assertEqual(review.classify(native.encode(), config), "success")


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
        build = reviewer["steps"][2]
        keyed = reviewer["steps"][3]
        # Artifacts are named after the attempt that made them, so a rerun of only the failed jobs finds them.
        run = "${{ github.run_id }}-${{ "
        self.assertEqual([step["with"]["name"] for job in [capture, reviewer, publisher_job] for step in job["steps"] if "artifact" in step.get("uses", "")],
                         ["pr-review-input-" + run + "github.run_attempt }}", "pr-review-input-" + run + "needs.capture.outputs.run_attempt }}",
                          "pr-review-result-" + run + "github.run_attempt }}", "pr-review-result-" + run + "needs.review.outputs.run_attempt }}"])
        self.assertEqual(reviewer["steps"][0]["run"], 'echo "run_attempt=$GITHUB_RUN_ATTEMPT" >> "$GITHUB_OUTPUT"')
        self.assertNotIn("env", build)
        self.assertEqual(set(keyed["env"]), {public for public, _ in review.KEYS.values()})
        for provider, (public, _) in review.KEYS.items():
            self.assertIn("contains(fromJSON(needs.capture.outputs.providers), '" + provider + "')", keyed["env"][public])
            self.assertIn("secrets." + public, keyed["env"][public])
        self.assertNotIn("${{ secrets.", str(capture))
        self.assertNotIn("${{ secrets.", str(publisher_job))
        self.assertNotIn("github.token", str(reviewer))
        self.assertIn("toJSON(needs.capture.outputs)", str(publisher_job))
        declared = data["on"]["workflow_call"]
        self.assertEqual(set(declared["inputs"]), review.INPUT_NAMES)
        self.assertEqual(declared["inputs"]["model"], {"type": "string", "required": "true"})
        self.assertEqual(set(declared["secrets"]), {public for public, _ in review.KEYS.values()})
        self.assertEqual(set(publisher.PROVIDERS.split("|")), set(review.KEYS))
        self.assertEqual(publisher.COMMENT_MODES, review.COMMENT_MODES)
        # Workflow defaults must pass validation unchanged once a model is supplied.
        defaults = {name: spec["default"] for name, spec in declared["inputs"].items() if "default" in spec}
        numbers = {name for name, spec in declared["inputs"].items() if spec["type"] == "number"}
        booleans = {name for name, spec in declared["inputs"].items() if spec["type"] == "boolean"}
        review.settings({"model": "xai/grok-4.7", **{k: int(v) if k in numbers else v == "true" if k in booleans else v for k, v in defaults.items()}})
        self.assertEqual(booleans, {"large_pr_chunking"})
        for value in (source("review"), source("publish")):
            self.assertNotIn("${{", value)
        import check_tree
        check_tree.check()
