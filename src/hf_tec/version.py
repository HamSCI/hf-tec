"""Version + git provenance.

`GIT_INFO` is populated lazily on first access by shelling out to `git`
in the repo directory; falls back to a static stub when the repo isn't
present (e.g. when the venv is installed from a tarball).
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

from . import __version__ as PACKAGE_VERSION


class _GitInfo(TypedDict):
    sha: str
    short: str
    ref: str
    dirty: bool
    source: str


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(args: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), *args],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


@lru_cache(maxsize=1)
def get_git_info() -> _GitInfo:
    sha = _git(["rev-parse", "HEAD"]) or ""
    short = _git(["rev-parse", "--short", "HEAD"]) or ""
    ref = _git(["rev-parse", "--abbrev-ref", "HEAD"]) or ""
    dirty_status = _git(["status", "--porcelain"]) or ""
    return _GitInfo(
        sha=sha,
        short=short,
        ref=ref,
        dirty=bool(dirty_status),
        source=str(_REPO_ROOT / "src" / "hf_tec"),
    )


GIT_INFO = get_git_info()

__all__ = ["PACKAGE_VERSION", "GIT_INFO", "get_git_info"]
