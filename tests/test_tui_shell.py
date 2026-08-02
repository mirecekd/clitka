"""TUI shell: status bar contents, key bar, help toggle, quit. No AWS calls."""

from __future__ import annotations

import pytest
from textual.screen import Screen

from clitka.core.context import Context, Identity
from clitka.tui.app import ClitkaApp
from clitka.tui.dropdown import TextDrop
from clitka.tui.keybar import SLOTS, KeyBar, render_bar
from clitka.tui.status import StatusBar, format_account


@pytest.fixture
def offline_context(monkeypatch):
    """A Context whose identity resolves without touching the network."""
    ctx = Context(profile="demo", region="eu-central-1")
    ident = Identity(
        account="123456789012", arn="arn:aws:iam::123456789012:user/mirek", user_id="A"
    )
    monkeypatch.setattr(Context, "identity_or_none", lambda _self: ident)
    return ctx


def test_format_account():
    assert format_account("123456789012") == "1234-5678-9012"
    assert format_account("") == "(unknown)"
    assert format_account("nonsense") == "nonsense"


def test_status_bar_renders_without_network():
    bar = StatusBar(Context(profile="p", region="eu-west-1", read_only=True))
    line = bar.line()
    assert "profile: p" in line
    assert "region: eu-west-1" in line
    assert "READ-ONLY" in line


def test_key_bar_slots_are_fixed_and_dimmable():
    """P/R/W/C are letters (owner's call, 2026-07-31); F3/F4 are view and edit.

    There is no `L`: signing in was taken out of the TUI - `clitka auth login`.
    `C` (config) joined on 2026-08-02, after the three session switches: it is
    the one panel that writes to disk, so it belongs next to them.
    """
    assert [key for key, _ in SLOTS] == [
        "F1",
        "P",
        "R",
        "W",
        "C",
        "F3",
        "F4",
        "F5",
        "F9",
        "F10",
    ]

    full = render_bar()
    assert "[b]F9[/b] Actions" in full
    assert "[b]F10[/b] Quit" in full
    assert "[b]F3[/b] View" in full
    assert "[b]F4[/b] Edit" in full
    assert "[b]W[/b] Window" in full
    assert "[b]C[/b] Config" in full
    assert "[dim]P Profile[/dim]" in render_bar(frozenset({"F1", "F10"}))


def test_key_bar_highlights_the_open_panel():
    opened = render_bar(open_key="P")
    assert "[reverse]P Profile[/reverse]" in opened
    assert "[b]R[/b] Region" in opened
    bar = KeyBar()
    bar.set_open("F1")
    assert "[reverse]F1 Help[/reverse]" in bar.line()
    bar.set_open(None)
    assert "[b]F1[/b] Help" in bar.line()
    # Textual reports a bare letter lowercase; the slot is labelled upper case.
    bar.set_open("r")
    assert "[reverse]R Region[/reverse]" in bar.line()


@pytest.mark.asyncio
async def test_app_shows_context_in_the_status_bar(offline_context):
    app = ClitkaApp(offline_context, open_tree=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        bar = app.query_one(StatusBar)
        assert bar.profile == "demo"
        assert bar.aws_region == "eu-central-1"
        assert bar.account == "1234-5678-9012"
        assert bar.identity == "mirek"
        assert "[b]F9[/b] Actions" in app.query_one(KeyBar).line()
        assert "[b]P[/b] Profile" in app.query_one(KeyBar).line()


@pytest.mark.asyncio
async def test_a_screen_pushed_later_gets_the_resolved_account(offline_context):
    """Every screen composes its own StatusBar, and it must not say "(resolving)".

    The identity is resolved once, on a worker; a bar mounted after that had no
    way to learn the answer and used to sit on "(resolving)" for good.
    """
    app = ClitkaApp(offline_context, open_tree=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        class BarScreen(Screen[None]):
            def compose(self):
                yield StatusBar(offline_context)

        app.push_screen(BarScreen())

        await pilot.pause()
        bar = app.screen.query_one(StatusBar)
        assert bar.account == "1234-5678-9012", bar.line()
        assert bar.identity == "mirek"
        assert bar.profile == "demo"


@pytest.mark.asyncio
async def test_f1_drops_the_help_panel_and_f1_closes_it(offline_context):

    app = ClitkaApp(offline_context, open_tree=False)
    async with app.run_test() as pilot:
        await pilot.press("f1")
        await pilot.pause()
        panel = app.screen
        assert isinstance(panel, TextDrop)
        assert "command palette" in panel.body
        assert "[reverse]F1 Help[/reverse]" in app.query_one(KeyBar).line()

        await pilot.press("f1")
        await pilot.pause()
        assert not isinstance(app.screen, TextDrop)
        assert "[b]F1[/b] Help" in app.query_one(KeyBar).line()


@pytest.mark.asyncio
async def test_escape_also_closes_the_help_panel(offline_context):
    app = ClitkaApp(offline_context, open_tree=False)
    async with app.run_test() as pilot:
        await pilot.press("f1")
        await pilot.pause()
        assert isinstance(app.screen, TextDrop)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, TextDrop)


@pytest.mark.asyncio
async def test_f10_quits(offline_context):
    app = ClitkaApp(offline_context, open_tree=False)
    async with app.run_test() as pilot:
        await pilot.press("f10")
        await pilot.pause()
    assert app.return_value is None


@pytest.mark.asyncio
async def test_unauthenticated_context_does_not_crash_the_bar(monkeypatch):
    monkeypatch.setattr(Context, "identity_or_none", lambda _self: None)
    app = ClitkaApp(Context(profile="p", region="eu-central-1"), open_tree=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.query_one(StatusBar).account == "(unauthenticated)"
