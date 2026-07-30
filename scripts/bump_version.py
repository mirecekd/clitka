#!/usr/bin/env python3
"""Bump the date-based build version: vYYMMDD.N.

Run this before every commit. If the stored date is today, N goes up; if it is an
older date, the version restarts at today's date with N = 1. Both
`src/clitka/__init__.py` and `pyproject.toml` are rewritten.

    python scripts/bump_version.py          # bump and print the new version
    python scripts/bump_version.py --show   # print the current one, change nothing

ponytail: two small regex substitutions rather than a release tool. Ceiling: it
assumes the two files each hold the version exactly once, which a self-check
verifies. Upgrade path: hatch-vcs, once tagging actually starts.
"""

from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "src" / "clitka" / "__init__.py"
PYPROJECT = ROOT / "pyproject.toml"

_INIT_RE = re.compile(r'^__version__ = "v(\d{6})\.(\d+)"$', re.MULTILINE)
_PYPROJECT_RE = re.compile(r'^version = "(\d{6})\.(\d+)"$', re.MULTILINE)


def current() -> tuple[str, int]:
    """The (YYMMDD, N) recorded in `__init__.py`."""
    match = _INIT_RE.search(INIT.read_text(encoding="utf-8"))
    if match is None:
        raise SystemExit(f"[ERROR] no vYYMMDD.N version found in {INIT}")
    return match.group(1), int(match.group(2))


def next_version(stored_date: str, stored_run: int, today: str) -> tuple[str, int]:
    """Same day -> next run; a new day -> run 1."""
    if stored_date == today:
        return today, stored_run + 1
    return today, 1


def _replace_one(path: Path, pattern: re.Pattern[str], new_line: str) -> None:
    text = path.read_text(encoding="utf-8")
    if len(pattern.findall(text)) != 1:
        raise SystemExit(f"[ERROR] expected exactly one version line in {path}")
    path.write_text(pattern.sub(new_line, text, count=1), encoding="utf-8")


def write(date: str, run: int) -> str:
    version = f"v{date}.{run}"
    _replace_one(INIT, _INIT_RE, f'__version__ = "{version}"')
    _replace_one(PYPROJECT, _PYPROJECT_RE, f'version = "{date}.{run}"')
    return version


def main(argv: list[str]) -> int:
    date, run = current()
    if "--show" in argv:
        print(f"v{date}.{run}")
        return 0
    today = dt.date.today().strftime("%y%m%d")
    print(f"[OK] {write(*next_version(date, run, today))}")
    return 0


def _self_check() -> None:
    assert next_version("260731", 1, "260731") == ("260731", 2)
    assert next_version("260731", 9, "260731") == ("260731", 10)
    assert next_version("260730", 7, "260731") == ("260731", 1)
    # The files must be parseable and consistent with each other right now.
    date, run = current()
    body = PYPROJECT.read_text(encoding="utf-8")
    assert f'version = "{date}.{run}"' in body, "pyproject is out of step with __init__"
    print(f"[OK] bump self-check passed (currently v{date}.{run})")


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        _self_check()
    else:
        raise SystemExit(main(sys.argv[1:]))
