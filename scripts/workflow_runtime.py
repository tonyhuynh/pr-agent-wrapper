"""Load the exact Python bodies embedded in the reusable workflow."""
from pathlib import Path
import types

ROOT = Path(__file__).resolve().parents[1]


def source(kind):
    marker = {"review": "PRW_REVIEW_PY", "publish": "PRW_PUBLISH_PY"}[kind]
    text = (ROOT / ".github/workflows/review.yml").read_text()
    start = text.index("<<'" + marker + "'\n") + len("<<'" + marker + "'\n")
    end = text.index("          " + marker + "\n", start)
    lines = text[start:end].splitlines(keepends=True)
    assert all(line.startswith("          ") or not line.strip() for line in lines)
    return "".join(line[10:] if line.startswith("          ") else line for line in lines)


def load(kind):
    module = types.ModuleType("workflow_" + kind)
    exec(compile(source(kind), ".github/workflows/review.yml:" + kind, "exec"), module.__dict__)
    return module
