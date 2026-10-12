#!/usr/bin/env python3
"""Reject mismatched release source before the Make promotion touches Docker.

This checks source identity, not whether an arbitrary digest was built/tested
from it. Same-source build and test receipts remain a publication prerequisite.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys


class Rejected(Exception):
    pass


def verify(root, version, source):
    def git(*args):
        result = subprocess.run(["git", "-C", str(root), *args],
                                capture_output=True, text=True)
        if result.returncode:
            raise Rejected("required Git source or release tag is unavailable")
        return result.stdout.strip()

    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise Rejected("release version must be numeric X.Y.Z")
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise Rejected("RELEASE_SOURCE_COMMIT must be an explicit full commit identity")
    if Path(git("rev-parse", "--show-toplevel")).resolve() != root:
        raise Rejected("release source must be the repository root")
    if git("rev-parse", "HEAD") != source:
        raise Rejected("release source does not match HEAD")
    if git("rev-parse", "--verify", f"refs/tags/v{version}^{{commit}}") != source:
        raise Rejected("immutable local release tag does not match source")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise Rejected("tracked release source must be clean, including the index")

    git("ls-files", "--error-unmatch", "src/angular/package.json",
        "src/debian/changelog", "src/e2e/tests/about.page.spec.ts")

    package = json.loads((root / "src/angular/package.json").read_text(encoding="utf-8"))
    changelog = (root / "src/debian/changelog").read_text(encoding="utf-8").splitlines()
    debian = re.fullmatch(r"seedsync \(([^)]+)\) .+", changelog[0] if changelog else "")
    about = (root / "src/e2e/tests/about.page.spec.ts").read_text(encoding="utf-8")
    expected = re.findall(
        r"expect\s*\(\s*await\s+page\.getVersion\(\)\s*\)\s*\.toEqual\(\s*['\"]v([^'\"]+)['\"]\s*\)",
        about,
    )
    if package.get("version") != version or not debian or debian[1] != version or expected != [version]:
        raise Rejected("package, first Debian changelog, and About version must match release")


def main():
    root = Path(__file__).resolve().parents[2]
    version = os.environ.get("RELEASE_VERSION", "")
    source = os.environ.get("RELEASE_SOURCE_COMMIT", "")
    try:
        verify(root, version, source)
    except (Rejected, OSError, ValueError, TypeError, AttributeError) as error:
        reason = str(error) if isinstance(error, Rejected) else "canonical release metadata is unavailable or invalid"
        print(f"ERROR: release source policy: {reason}", file=sys.stderr)
        return 1
    print(f"Release source verified: version={version} source={source}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
