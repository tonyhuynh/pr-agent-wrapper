import ast
import hashlib
import json
from pathlib import Path
import sys
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workflow_runtime import ROOT, load, source
from fetch_upstream import fixture_root, verify

review = load("review")
publisher = load("publish")


def inputs(**changes):
    # Mirrors toJSON(inputs): the caller's model plus every workflow default.
    return {"model": "xai/grok-4.7", "fallback_models": "[]", "extra_instructions": "",
            "num_max_findings": 3, "ignore_globs": '["vendor/**"]', "api_base": "",
            "custom_model_max_tokens": 0, "max_model_tokens": 64000, "reasoning_effort": "",
            "response_language": "en-US", "ai_timeout": 120, "comment_mode": "update", **changes}


def chains():
    """Model chains that together name every provider; one chain holds at most five."""
    names = sorted(review.KEYS)
    for start in range(0, len(names), 5):
        group = names[start:start + 5]
        yield group, inputs(model=group[0] + "/m", fallback_models=json.dumps([name + "/m" for name in group[1:]]))


def event():
    return {"action": "synchronize", "number": 17, "repository": {"full_name": "example/project"},
            "pull_request": {"number": 17, "draft": False, "state": "open",
                             "base": {"sha": "a" * 40, "repo": {"full_name": "example/project"}},
                             "head": {"sha": "b" * 40, "repo": {"full_name": "example/project", "fork": False}}}}


def expected():
    env = {"GITHUB_REPOSITORY": "example/project", "GITHUB_EVENT_NAME": "pull_request",
           "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2",
           "CAPTURED_JSON": json.dumps({"captured": "true", "merge_base": "c" * 40,
                                        "diff_sha256": hashlib.sha256(b"patch\n").hexdigest()})}
    return publisher.expected_identity(event(), env)


def artifact(root, text="Native review\n\nAgent run details\n- Model: xai/grok-4.7\n", status="success"):
    for name in ("manifest.json", "review.diff", "review.md"):
        (root / name).unlink(missing_ok=True)
    meta = expected()
    data = text.encode()
    manifest = {**meta, "status": status, "review_sha256": hashlib.sha256(data).hexdigest()}
    (root / "manifest.json").write_text(json.dumps(manifest))
    (root / "review.diff").write_bytes(b"patch\n")
    (root / "review.md").write_bytes(data)
    return meta


def upstream(path):
    data = (fixture_root() / path).read_bytes()
    verify(path, data)
    return data.decode()


def functions(path, names, namespace):
    tree = ast.parse(upstream(path))
    selected = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    assert {n.name for n in selected} == set(names)
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *selected], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), path, "exec"), namespace)
    return namespace
