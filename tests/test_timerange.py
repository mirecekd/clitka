"""The time window: presets, the duration parser and the session's choice."""

from __future__ import annotations

import pytest

from clitka.core import timerange as tr


@pytest.fixture(autouse=True)
def _fresh_window():
    """The chosen window is module state, so no test may leak into the next."""
    tr.reset()
    yield
    tr.reset()


def test_self_check() -> None:
    tr._self_check()


def test_presets_are_unique_and_ordered() -> None:
    assert [p.label for p in tr.PRESETS][:3] == ["5m", "15m", "1h"]
    assert [p.label for p in tr.PRESETS][-3:] == ["2w", "1mo", "1y"]
    assert len({p.key for p in tr.PRESETS}) == len(tr.PRESETS)
    # Strictly growing: the picker reads top to bottom.
    minutes = [p.minutes for p in tr.PRESETS]
    assert minutes == sorted(minutes)


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("5", 5.0),
        ("90m", 90.0),
        ("90 m", 90.0),
        ("2h", 120.0),
        (" 2H ", 120.0),
        ("1d", 1440.0),
        ("2w", 20160.0),
        ("1mo", 43200.0),
        ("1y", 525600.0),
        ("1.5h", 90.0),
    ],
)
def test_parse_accepts(text: str, minutes: float) -> None:
    assert tr.parse(text).minutes == minutes


@pytest.mark.parametrize("text", ["", "   ", "soon", "-5m", "0", "0m", "5x", "h", "3 days", "5y"])
def test_parse_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        tr.parse(text)


def test_label_prefers_the_preset_wording() -> None:
    assert tr.label(60) == "1h"
    assert tr.label(1440) == "24h"  # the preset says 24h, not 1d
    assert tr.label(2880) == "2d"
    assert tr.label(90) == "90m"
    assert tr.label(0.5) == "0.5m"


def test_human_puts_a_space_in() -> None:
    assert tr.human(15) == "15 min"
    assert tr.human(180) == "3 h"
    assert tr.human(tr.WEEK) == "7 d"


def test_select_changes_what_the_session_looks_through() -> None:
    assert tr.current() is tr.DEFAULT
    assert tr.minutes() == 60.0
    chosen = tr.select(tr.parse("3h"))
    assert chosen.label == "3h"
    assert tr.current() is chosen
    assert tr.minutes() == 180.0
    assert tr.reset() is tr.DEFAULT
