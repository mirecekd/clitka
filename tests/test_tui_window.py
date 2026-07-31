"""`w`: the time window picker, driven through the app. No AWS calls."""

from __future__ import annotations

import pytest

from clitka.core import timerange as tr
from clitka.core.context import Context, Identity
from clitka.services.logs import actions as la
from clitka.tui import windowpick as wp
from clitka.tui.app import ClitkaApp
from clitka.tui.dropdown import TextDrop
from clitka.tui.dropmenu import DropMenu
from clitka.tui.keybar import KeyBar
from clitka.tui.picker import CommandPalette


@pytest.fixture(autouse=True)
def _fresh_window():
    tr.reset()
    yield
    tr.reset()


@pytest.fixture
def offline(monkeypatch):
    ident = Identity(account="123456789012", arn="arn:aws:iam::1:user/mirek", user_id="A")
    monkeypatch.setattr(Context, "identity_or_none", lambda _self: ident)
    return Context(profile="sw-sandbox", region="eu-central-1")


# --- the item list --------------------------------------------------------


def test_window_items_offer_every_preset_plus_custom():
    values = [item.value for item in wp.window_items()]
    assert values == [preset.label for preset in tr.PRESETS] + [wp.CUSTOM]
    assert "2w" in values and "1mo" in values and "1y" in values


def test_window_items_mark_the_window_in_force():
    marked = [i.value for i in wp.window_items(tr.parse("6h")) if i.current]
    assert marked == ["6h"]


def test_window_items_mark_custom_for_a_window_nobody_offers():
    marked = [i.value for i in wp.window_items(tr.parse("42m")) if i.current]
    assert marked == [wp.CUSTOM]


def test_resolve_ignores_a_typo_rather_than_widening_the_window():
    good = wp.resolve("3h")
    assert good is not None and good.minutes == 180.0
    assert wp.resolve("tomorrow") is None
    assert wp.resolve(None) is None


# --- driven through the app ----------------------------------------------


@pytest.mark.asyncio
async def test_w_drops_the_window_menu_and_picks_a_preset(offline):
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        await pilot.press("w")
        await pilot.pause()
        menu = app.screen
        assert isinstance(menu, DropMenu)
        assert "[reverse]W Window[/reverse]" in app.query_one(KeyBar).line()
        # 13 rows is over the filter threshold, but the shortcuts must still work.
        assert menu.filtered is False

        await pilot.press("4")  # the `3h` preset
        await pilot.pause()
        assert tr.current().label == "3h"
        assert tr.minutes() == 180.0
        assert "[b]W[/b] Window" in app.query_one(KeyBar).line()


@pytest.mark.asyncio
async def test_escaping_the_window_menu_changes_nothing(offline):
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        await pilot.press("w")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert tr.current() is tr.DEFAULT


@pytest.mark.asyncio
async def test_custom_opens_the_palette_and_accepts_a_typed_duration(offline):
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        await pilot.press("w")
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        palette = app.screen
        assert isinstance(palette, CommandPalette)

        for key in ("9", "0", "m"):  # "90m"
            await pilot.press(key)
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert tr.current().minutes == 90.0


@pytest.mark.asyncio
async def test_an_unreadable_duration_is_refused_with_an_explanation(offline):
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        app._window_typed("tomorrow")
        await pilot.pause()
        panel = app.screen
        assert isinstance(panel, TextDrop)
        assert "duration" in panel.body
        assert tr.current() is tr.DEFAULT


@pytest.mark.asyncio
async def test_the_window_is_not_persisted(offline, monkeypatch):
    """Like P and R, `w` lasts for the session only (owner's rule)."""
    from clitka.core import clitkaconfig

    def boom(*_args, **_kwargs):
        raise AssertionError("w must not write the CLITKA config")

    monkeypatch.setattr(clitkaconfig, "save", boom, raising=False)
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        await pilot.press("w")
        await pilot.pause()
        await pilot.press("1")  # 5m
        await pilot.pause()
        assert tr.current().label == "5m"


# --- what the window actually changes ------------------------------------


def test_the_f9_label_names_the_window_in_force():
    assert la.events_label() == "Last 1 h of events"
    tr.select(tr.parse("3d"))
    assert la.events_label() == "Last 3 d of events"
    # The Action reads it lazily, so the menu can never show a stale window.
    events = next(action for action in la.ACTIONS if action.id == "logs.events")
    assert events.text() == "Last 3 d of events"
    assert "Last 3 d" in events.menu_label()


def test_the_events_tab_asks_for_the_chosen_window(monkeypatch):
    asked: list[float] = []

    def spy(_ctx, _group, minutes=60.0, pattern=None, limit=200):
        asked.append(minutes)
        return []

    monkeypatch.setattr(la.lg, "recent_events", spy)
    ref = la.ResourceRef.from_row(la.TYPE_NAME, {"identifier": "/aws/lambda/x"})

    body = la.build_events_tab(Context(), ref)
    assert asked == [60.0]
    assert "last 1 h" in body

    tr.select(tr.parse("2w"))
    body = la.build_events_tab(Context(), ref)
    assert asked == [60.0, tr.WEEK * 2]
    assert "last 2 w" in body
