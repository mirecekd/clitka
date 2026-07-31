"""Drop-down panels: item rendering, filtering, cursor placement, toggling.

Pure widget behaviour - nothing here touches AWS. The panels are driven through
a throwaway host app so the modal push/dismiss contract is exercised for real.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from clitka.tui.dropdown import MenuItem, TextDrop
from clitka.tui.dropmenu import FILTER_THRESHOLD, DropMenu, match, preferred_index
from clitka.tui.keybar import KeyBar

ITEMS = [
    MenuItem("sw-sandbox", "sw-sandbox", detail="sso eu-central-1", current=True),
    MenuItem("k-d-mirdvorak", "k-d-mirdvorak", detail="static eu-central-1"),
    MenuItem("trask", "trask", detail="sso eu-west-1", key="t"),
]


class Host(App[None]):
    """A minimal app with a key bar, so `set_open` has something to talk to."""

    def compose(self) -> ComposeResult:
        yield KeyBar()

    def __init__(self) -> None:
        super().__init__()
        self.picked: object = "unset"

    def remember(self, value: object) -> None:
        self.picked = value


# --- pure functions -------------------------------------------------------


def test_menu_item_line_marks_current_and_shortcut():
    plain = MenuItem("eu-west-1", "eu-west-1")
    assert plain.line().startswith("  ")
    assert "eu-west-1" in plain.line()

    current = MenuItem("eu-central-1", "eu-central-1", current=True)
    assert current.line().startswith("*")

    keyed = MenuItem("delete", "delete", key="d")
    assert keyed.line().strip().startswith("d")

    detailed = MenuItem("trask", "trask", detail="sso eu-west-1")
    assert "[dim]sso eu-west-1[/dim]" in detailed.line()


def test_match_filters_on_label_and_detail():
    assert match(ITEMS, "") == ITEMS
    assert [i.value for i in match(ITEMS, "sandbox")] == ["sw-sandbox"]
    assert [i.value for i in match(ITEMS, "eu-west")] == ["trask"]
    assert len(match(ITEMS, "eu-central")) == 2
    assert match(ITEMS, "nothing-like-this") == []


def test_match_is_case_insensitive_and_prefers_prefixes():
    assert [i.value for i in match(ITEMS, "TRASK")] == ["trask"]
    items = [MenuItem("ab-x", "ab-x"), MenuItem("x-ab", "x-ab")]
    assert [i.value for i in match(items, "ab")] == ["ab-x", "x-ab"]


def test_preferred_index_lands_on_the_current_item():
    assert preferred_index(ITEMS) == 0
    assert preferred_index([ITEMS[1], ITEMS[0], ITEMS[2]]) == 1
    assert preferred_index([ITEMS[1], ITEMS[2]]) == 0
    assert preferred_index([]) == 0


def test_filter_box_appears_only_for_long_lists():
    assert DropMenu("Region", ITEMS).filtered is False
    many = [MenuItem(f"p{n}", f"p{n}") for n in range(FILTER_THRESHOLD + 1)]
    assert DropMenu("Profile", many).filtered is True


# --- driven through a real screen ----------------------------------------


@pytest.mark.asyncio
async def test_drop_menu_returns_the_highlighted_value():
    app = Host()
    async with app.run_test() as pilot:
        app.push_screen(DropMenu("Profile", ITEMS, "p"), app.remember)
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    # The cursor starts on the item marked `current`.
    assert app.picked == "sw-sandbox"


@pytest.mark.asyncio
async def test_drop_menu_single_key_shortcut_runs_the_item():
    app = Host()
    async with app.run_test() as pilot:
        app.push_screen(DropMenu("Profile", ITEMS, "p"), app.remember)
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
    assert app.picked == "trask"


@pytest.mark.asyncio
async def test_drop_menu_cursor_moves_down():
    app = Host()
    async with app.run_test() as pilot:
        app.push_screen(DropMenu("Profile", ITEMS, "p"), app.remember)
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
    assert app.picked == "k-d-mirdvorak"


@pytest.mark.asyncio
async def test_escape_closes_the_panel_with_no_choice():
    app = Host()
    async with app.run_test() as pilot:
        app.push_screen(DropMenu("Profile", ITEMS, "p"), app.remember)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert app.picked is None


@pytest.mark.asyncio
async def test_the_opening_f_key_toggles_the_panel_shut():
    app = Host()
    async with app.run_test() as pilot:
        app.push_screen(DropMenu("Profile", ITEMS, "p"), app.remember)
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
    assert app.picked is None


@pytest.mark.asyncio
async def test_open_panel_highlights_its_slot_on_the_key_bar():
    app = Host()
    async with app.run_test() as pilot:
        app.push_screen(DropMenu("Profile", ITEMS, "p"), app.remember)
        await pilot.pause()
        assert "[reverse]P Profile[/reverse]" in app.query_one(KeyBar).line()
        await pilot.press("escape")
        await pilot.pause()
        assert "[b]P[/b] Profile" in app.query_one(KeyBar).line()


@pytest.mark.asyncio
async def test_text_drop_shows_its_body_and_closes_on_its_key():
    app = Host()
    async with app.run_test() as pilot:
        app.push_screen(TextDrop("Help", "press F1 for this", "f1"), app.remember)
        await pilot.pause()
        assert "[reverse]F1 Help[/reverse]" in app.query_one(KeyBar).line()
        await pilot.press("f1")
        await pilot.pause()
    assert app.picked is None


@pytest.mark.asyncio
async def test_typing_in_the_filter_narrows_the_list_and_keys_are_literal():
    many = list(ITEMS) + [MenuItem(f"p{n}", f"p{n}") for n in range(FILTER_THRESHOLD)]
    app = Host()
    async with app.run_test() as pilot:
        menu = DropMenu("Profile", many, "p")
        app.push_screen(menu, app.remember)
        await pilot.pause()
        # "t" is a shortcut for trask, but in the filter box it must just type.
        # (Careful: "tra" would also match the detail "eu-cen-tra-l-1".)
        await pilot.press("t", "r", "a", "s")
        await pilot.pause()
        assert [i.value for i in menu.matches] == ["trask"]

        assert app.picked == "unset"
        await pilot.press("enter")
        await pilot.pause()
    assert app.picked == "trask"
