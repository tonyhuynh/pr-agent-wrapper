"""Validate YAML, action pins, and the public file boundary without credentials."""
from pathlib import Path
import re

import yaml

from workflow_runtime import ROOT, source


def check():
    for path in [*ROOT.glob(".github/workflows/*.yml"), *ROOT.glob("examples/*.yml")]:
        # BaseLoader retains the literal 'on' key under YAML 1.1.
        data = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        assert isinstance(data, dict) and "on" in data and "jobs" in data, path
        for action in re.findall(r"uses:\s*([^\s#]+)", path.read_text()):
            if re.fullmatch(r"[A-Za-z0-9-]+/pr-agent-wrapper/\.github/workflows/review\.yml@<MAIN_SHA>", action):
                # The placeholder pin is allowed only in the example, under whichever owner hosts the wrapper.
                assert path.parent.name == "examples"
            else:
                assert re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}", action), action
    for kind in ("review", "publish"):
        compile(source(kind), str(ROOT / ".github/workflows/review.yml") + ":" + kind, "exec")
    for path in [ROOT / "README.md", ROOT / "SECURITY.md", ROOT / "CHANGELOG.md", ROOT / "CONTRIBUTING.md", *ROOT.glob("docs/**/*.md")]:
        text = path.read_text()
        assert "\u2014" not in text, path
        assert "\u2013" not in text, path
        assert not re.search(r"/(?:Users|home)/|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text), path


if __name__ == "__main__":
    check()
