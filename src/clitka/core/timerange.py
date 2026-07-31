"""How far back "recent" means - the one time window CLITKA looks through.

The log preview used to be hard-wired to 60 minutes. This module holds the
choices (`PRESETS`), the parser for a typed one (`parse`) and the window the
running session has picked (`current` / `select`).

No boto3 and no Textual in here, so the arithmetic and the parsing are testable
on their own - the same seam as `logsmodel.py`.

ponytail: the chosen window is module-level session state rather than an argument
threaded through `PreviewTab.build(ctx, ref)`, because that signature is a
published pluggy hook. Ceiling: one window for the whole session, not one per
tab. Upgrade path: add a `window` field to `PreviewTab.build` and pass it down.

Like the profile and the region switches, the window is deliberately NOT
persisted to `config.toml` - it lasts for the running session only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MINUTE = 1.0
HOUR = 60.0
DAY = 24 * HOUR
WEEK = 7 * DAY
MONTH = 30 * DAY  # a calendar month is not a fixed length; 30 days is the window
YEAR = 365 * DAY


@dataclass(frozen=True)
class TimeRange:
    """A "last N" window: minutes back from now, plus how to say that."""

    minutes: float
    label: str
    key: str = ""

    def __str__(self) -> str:
        return self.label


# The order the picker shows them in. The single-key shortcuts are unique and
# `_self_check` guards that. Note `1mo`, not `1m`: a typed `m` means minutes.
PRESETS: tuple[TimeRange, ...] = (
    TimeRange(5 * MINUTE, "5m", "1"),
    TimeRange(15 * MINUTE, "15m", "2"),
    TimeRange(1 * HOUR, "1h", "3"),
    TimeRange(3 * HOUR, "3h", "4"),
    TimeRange(6 * HOUR, "6h", "5"),
    TimeRange(12 * HOUR, "12h", "6"),
    TimeRange(1 * DAY, "24h", "7"),
    TimeRange(3 * DAY, "3d", "8"),
    TimeRange(1 * WEEK, "7d", "9"),
    TimeRange(2 * WEEK, "2w", "0"),
    TimeRange(1 * MONTH, "1mo", "n"),
    TimeRange(1 * YEAR, "1y", "y"),
)

DEFAULT = PRESETS[2]  # 1h - what the preview did before this was configurable

# A bare number is minutes, so "90" and "90m" mean the same thing.
_UNITS: dict[str, float] = {
    "": MINUTE,
    "m": MINUTE,
    "min": MINUTE,
    "h": HOUR,
    "hr": HOUR,
    "d": DAY,
    "w": WEEK,
    "mo": MONTH,
    "y": YEAR,
}

_SYNTAX = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([a-z]*)\s*$")

MAX_MINUTES = 2 * YEAR  # past this a FilterLogEvents call is a mistake, not a query


def parse(text: str) -> TimeRange:
    """`"90m"`, `"2h"`, `"3d"`, `"2w"`, `"1mo"`, `"1y"` or a bare number of minutes.

    Raises ValueError with something a user can read.
    """
    found = _SYNTAX.match((text or "").lower())
    if not found:
        raise ValueError(f"cannot read {text!r} as a duration - try 90m, 2h, 3d, 1mo")
    amount, unit = found.group(1), found.group(2)
    if unit not in _UNITS:
        raise ValueError(f"unknown unit {unit!r} - use m, h, d, w, mo or y")
    minutes = float(amount) * _UNITS[unit]
    if minutes <= 0:
        raise ValueError("the window must be longer than nothing")
    if minutes > MAX_MINUTES:
        raise ValueError(f"{text!r} is longer than the {human(MAX_MINUTES)} limit")
    return TimeRange(minutes, label(minutes))


def label(minutes: float) -> str:
    """The shortest label for a window: `90m`, `2h`, `3d`, `2w`, `1mo`, `1y`."""
    for preset in PRESETS:
        if preset.minutes == minutes:
            return preset.label
    for size, suffix in ((YEAR, "y"), (MONTH, "mo"), (WEEK, "w"), (DAY, "d"), (HOUR, "h")):
        if minutes >= size and minutes % size == 0:
            return f"{minutes / size:.0f}{suffix}"
    return f"{minutes:.0f}m" if minutes == int(minutes) else f"{minutes:g}m"


def human(minutes: float) -> str:
    """The same window with a space in it, for a heading: `15 min`, `3 h`, `7 d`."""
    text = label(minutes)
    unit = text.lstrip("0123456789.")
    amount = text[: len(text) - len(unit)]
    return f"{amount} {'min' if unit == 'm' else unit}"


_current: TimeRange = DEFAULT


def current() -> TimeRange:
    """The window this session is looking through."""
    return _current


def minutes() -> float:
    """Shorthand for `current().minutes` - what `recent_events` wants."""
    return _current.minutes


def select(window: TimeRange) -> TimeRange:
    """Pick a window for the rest of the session. Returns it, for chaining."""
    global _current
    _current = window
    return _current


def reset() -> TimeRange:
    """Back to the default - the tests use this so they cannot leak into each other."""
    return select(DEFAULT)


def _self_check() -> None:
    keys = [preset.key for preset in PRESETS]
    assert len(set(keys)) == len(keys), keys
    labels = [preset.label for preset in PRESETS]
    assert len(set(labels)) == len(labels), labels
    assert DEFAULT.label == "1h" and DEFAULT.minutes == 60.0

    assert parse("90").minutes == 90.0
    assert parse("90m").minutes == 90.0
    assert parse(" 2H ").minutes == 120.0
    assert parse("3d").minutes == 3 * DAY
    assert parse("2w").minutes == 2 * WEEK
    assert parse("1mo").minutes == MONTH
    assert parse("1y").minutes == YEAR
    assert parse("1.5h").minutes == 90.0

    for bad in ("", "soon", "-5m", "0", "5x", "10 years", "9y"):
        try:
            parse(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{bad!r} should not parse")

    # A parsed window labels itself the way the presets do.
    assert parse("60").label == "1h"
    assert parse("90").label == "90m"
    assert parse("48h").label == "2d"
    assert human(15) == "15 min" and human(180) == "3 h" and human(WEEK) == "7 d"
    assert human(YEAR) == "1 y" and human(MONTH) == "1 mo"

    assert current() is DEFAULT
    assert select(PRESETS[0]).label == "5m" and minutes() == 5.0
    assert reset() is DEFAULT
    print("[OK] timerange self-check passed")


if __name__ == "__main__":
    _self_check()
