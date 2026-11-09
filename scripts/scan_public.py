"""Scan Git's public file inventory, including uncommitted nonignored files."""
from pathlib import Path
import shutil
import subprocess
import tempfile

from workflow_runtime import ROOT


def scan():
    names = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT).split(b"\0")
    scanner = shutil.which("gitleaks")
    if scanner is None:
        go_root = subprocess.check_output(["go", "env", "GOPATH"], text=True).strip()
        scanner = str(Path(go_root) / "bin/gitleaks")
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory)
        for raw in set(names) - {b""}:
            name = Path(raw.decode("utf-8"))
            if name.is_absolute() or ".." in name.parts or ".git" in name.parts:
                raise ValueError("Invalid scan path")
            source = ROOT / name
            if source.is_symlink() or not source.is_file():
                raise ValueError("Only regular public files may be scanned")
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        subprocess.run([scanner, "dir", "--redact", "--no-banner", "--exit-code", "1", str(target)], check=True)


if __name__ == "__main__":
    scan()
