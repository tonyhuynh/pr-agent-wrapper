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
    return {"model": "xai/grok-4.7", "fallback_models": "[]", "extra_instructions": "",
            "num_max_findings": 3, "ignore_globs": '["vendor/**"]', **changes}


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
