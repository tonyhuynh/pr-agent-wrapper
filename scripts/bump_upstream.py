"""Repin PR-Agent: rewrite the commit, the verified blob IDs, and the documented version.

Usage: python scripts/bump_upstream.py v0.50.0

Reads public GitHub metadata without credentials. Review the upstream diff between the
old and new commit, remove the .test-upstream fixtures, then run the tests before committing.
"""
import json
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request

from workflow_runtime import ROOT, load

API = "https://api.github.com/repos/The-PR-Agent/pr-agent/"


def get(path):
    request = urllib.request.Request(API + path, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=60) as response:
        return json.loads(response.read(4 * 1024 * 1024))


def replace_exact(text, old, new, expected):
    if text.count(old) != expected:
        raise ValueError(f"Expected {expected} occurrence(s) of {old!r}")
    return text.replace(old, new)


def bump(tag):
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError("Pass a release tag such as v0.50.0")
    runtime = load("review")
    old = runtime.UPSTREAM
    new = get("commits/" + urllib.parse.quote(tag, safe=""))["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", new):
        raise ValueError("Unexpected commit ID")
    workflow = ROOT / ".github/workflows/review.yml"
    text = replace_exact(workflow.read_text(), old, new, 2)
    for path, blob in runtime.SOURCE_BLOBS.items():
        current = get("contents/" + urllib.parse.quote(path) + "?ref=" + new)["sha"]
        if not re.fullmatch(r"[0-9a-f]{40}", current):
            raise ValueError("Unexpected blob ID for " + path)
        text = replace_exact(text, f'"{path}": "{blob}"', f'"{path}": "{current}"', 1)
        print(("changed   " if current != blob else "unchanged ") + path)
    workflow.write_text(text)
    readme = ROOT / "README.md"
    words = readme.read_text()
    version = re.search(r"PR-Agent (v[0-9]+\.[0-9]+\.[0-9]+)", words).group(1)
    readme.write_text(words.replace(version, tag).replace(old, new))
    print(f"\nPinned {tag} at {new}.")
    print(f"Review https://github.com/The-PR-Agent/pr-agent/compare/{old}...{new}")
    print("Then remove .test-upstream, run scripts/fetch_upstream.py, and run the tests.")


if __name__ == "__main__":
    bump(sys.argv[1] if len(sys.argv) == 2 else "")
