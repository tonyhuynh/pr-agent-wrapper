"""Fetch public pinned source fixtures; verify Git blob IDs before use."""
import hashlib
import os
from pathlib import Path
import urllib.request

from workflow_runtime import ROOT, load

runtime = load("review")


def fixture_root():
    return Path(os.environ.get("PRW_UPSTREAM_DIR", ROOT / ".test-upstream"))


def verify(path, data):
    actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    if actual != runtime.SOURCE_BLOBS[path]:
        raise ValueError("Pinned source blob mismatch: " + path)


def fetch():
    root = fixture_root()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for path in runtime.SOURCE_BLOBS:
        destination = root / path
        if destination.exists():
            verify(path, destination.read_bytes())
            continue
        url = f"https://raw.githubusercontent.com/The-PR-Agent/pr-agent/{runtime.UPSTREAM}/{path}"
        with opener.open(url, timeout=60) as response:
            data = response.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise ValueError("Source fixture too large")
        verify(path, data)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)


if __name__ == "__main__":
    fetch()
