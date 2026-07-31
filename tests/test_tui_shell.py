"""TUI shell: status bar contents, key bar, help toggle, quit. No AWS calls."""

from __future__ import annotations

import pytest

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
    assert [key for key, _ in SLOTS] == ["F1", "F2", "F3", "F5", "F9", "F10"]
    full = render_bar()
    assert "[b]F9[/b] Actions" in full
    assert "[b]F10[/b] Quit" in full
    assert "[dim]F2 Profile[/dim]" in render_bar(frozenset({"F1", "F10"}))


def test_key_bar_highlights_the_open_panel():
    opened = render_bar(open_key="F2")
    assert "[reverse]F2 Profile[/reverse]" in opened
    assert "[b]F3[/b] Region" in opened
    bar = KeyBar()
    bar.set_open("F1")
    assert "[reverse]F1 Help[/reverse]" in bar.line()
    bar.set_open(None)
    assert "[b]F1[/b] Help" in bar.line()


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
