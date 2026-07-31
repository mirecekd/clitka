"""`DropMenu`: the chooser that drops out of the top F-key menu bar.

Pick an item with one keystroke (k9s style) or with the cursor and enter. Long
lists - a profile list can easily be dozens of entries - grow a filter box and
reuse `picker.rank()` rather than inventing a second matcher.
"""

from __future__ import annotations

from collections.abc import Sequence

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input, ListItem, ListView, Static

from clitka.tui.dropdown import DropPanel, MenuItem
from clitka.tui.picker import rank

# Above this many items the menu grows a filter box.
FILTER_THRESHOLD = 12


def match(items: Sequence[MenuItem], needle: str) -> list[MenuItem]:
    """Items whose label or detail contains `needle`, in `rank()` order.

    Pulled out of the widget so the filtering can be tested without a screen.
    """
    if not needle:
        return list(items)
    wanted = rank([item.haystack() for item in items], needle, limit=200)
    order = {hay: pos for pos, hay in enumerate(wanted)}
    found = [item for item in items if item.haystack() in order]
    return sorted(found, key=lambda item: order[item.haystack()])


def preferred_index(items: Sequence[MenuItem]) -> int:
    """Where the cursor should start: on the item marked `current`, else first."""
    for position, item in enumerate(items):
        if item.current:
            return position
    return 0


class DropMenu(DropPanel):
    """Pick one item. Dismisses with `MenuItem.value`, or None if cancelled."""

    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
        Binding("down", "next", "Next", show=False),
        Binding("up", "previous", "Previous", show=False),
    ]

    def __init__(
        self,
        title: str,
        items: Sequence[MenuItem],
        toggle_key: str = "",
        hint: str = "",
        filterable: bool = True,
    ) -> None:
        super().__init__()
        self.TOGGLE_KEY = toggle_key
        self.title_text = title
        self.items = list(items)
        self.hint = hint
        # `filterable=False` keeps the single-key shortcuts working on a list that
        # is long but fully keyed - the time windows are 13 rows, all on a key,
        # and a focused filter box would swallow every one of them.
        self.filterable = filterable
        self.matches: list[MenuItem] = list(self.items)

    @property
    def filtered(self) -> bool:
        return self.filterable and len(self.items) > FILTER_THRESHOLD

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.title_text, classes="title")
            if self.filtered:
                yield Input(placeholder="filter", id="drop-filter")
            yield ListView(id="drop-list")
            if self.hint:
                yield Static(self.hint, classes="hint")

    def on_mount(self) -> None:
        super().on_mount()
        self._refill("")
        if self.filtered:
            self.query_one(Input).focus()
        else:
            self.query_one(ListView).focus()

    def _refill(self, needle: str) -> None:
        listing = self.query_one(ListView)
        listing.clear()
        self.matches = match(self.items, needle)
        for item in self.matches:
            listing.append(ListItem(Static(item.line(), markup=True)))
        if self.matches:
            listing.index = preferred_index(self.matches)

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refill(event.value)

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self.action_accept()

    def on_list_view_selected(self, _event: ListView.Selected) -> None:
        self.action_accept()

    def on_key(self, event) -> None:
        """Single-key shortcuts, but never while the user is typing a filter."""
        if self.filtered and self.query_one(Input).has_focus:
            super().on_key(event)
            return
        for item in self.items:
            if item.key and event.key == item.key:
                event.stop()
                event.prevent_default()
                self.dismiss(item.value)
                return
        super().on_key(event)

    def action_accept(self) -> None:
        index = self.query_one(ListView).index
        if self.matches and index is not None and 0 <= index < len(self.matches):
            self.dismiss(self.matches[index].value)
        else:
            self.dismiss(None)

    def action_next(self) -> None:
        self.query_one(ListView).action_cursor_down()

    def action_previous(self) -> None:
        self.query_one(ListView).action_cursor_up()


def _self_check() -> None:
    items = [
        MenuItem("sw-sandbox", "sw-sandbox", detail="sso eu-central-1", current=True),
        MenuItem("k-d-mirdvorak", "k-d-mirdvorak", detail="static eu-central-1"),
        MenuItem("trask", "trask", detail="sso eu-west-1"),
    ]
    assert match(items, "") == items
    assert [i.value for i in match(items, "sw")] == ["sw-sandbox"]
    assert [i.value for i in match(items, "eu-west")] == ["trask"]
    assert match(items, "nonexistent") == []
    assert preferred_index(items) == 0
    assert preferred_index(items[1:]) == 0
    assert preferred_index([items[1], items[0]]) == 1

    small = DropMenu("Region", items)
    assert small.filtered is False
    big = DropMenu("Profile", [MenuItem(f"p{n}", f"p{n}") for n in range(20)], "f2")
    assert big.filtered is True
    assert big.TOGGLE_KEY == "f2"
    # A long but fully keyed list opts out, so its shortcuts keep working.
    keyed = DropMenu("Window", big.items, "w", filterable=False)
    assert keyed.filtered is False
    print("[OK] drop menu self-check passed")


if __name__ == "__main__":
    _self_check()
