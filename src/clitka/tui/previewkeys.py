"""`PaneKeys`: the preview pane's focus and keyboard, mixed into `PreviewPane`.

The owner's report (2026-07-31): "in the view window I cannot move between
Overview / Raw with the keys - only by clicking, and unlike the explorer the
viewer does not look focused". Both halves of the fix live here:

- **Focus lands on the tab strip**, not on a scroll body. `Tabs` binds left/right
  itself, so whichever widget holds the focus decides whether the arrows walk the
  tabs at all - the strip is the one that does.
- **up/down still scroll the visible tab**, so the arrows behave the way they look
  even while the strip has the focus.

The highlight itself is one CSS rule (`PreviewPane:focus-within`) in `preview.py`.
Split out to keep both files under 8 kB; the seam is real: `preview.py` is what
the pane *shows*, this is how it is *driven*.
"""

from __future__ import annotations

from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.widgets import TabbedContent
from textual.widgets._tabbed_content import ContentTabs

# The strip holds the focus, so every scrolling key has to be re-declared here:
# `Tabs` itself only binds left/right, and a focused `Tabs` never lets the
# VerticalScroll underneath see a key. That is the whole of the owner's second
# report ("pgdn/pgup, up/down do nothing in the Events tab").
PANE_BINDINGS: list[BindingType] = [
    Binding("down", "scroll_body(1)", "Scroll down", show=False),
    Binding("up", "scroll_body(-1)", "Scroll up", show=False),
    Binding("pagedown", "page_body(1)", "Page down", show=False),
    Binding("pageup", "page_body(-1)", "Page up", show=False),
    Binding("home,ctrl+home", "end_body(-1)", "Top", show=False),
    Binding("end,ctrl+end", "end_body(1)", "Bottom", show=False),
]


# The `:focus-within` rule is the other half of the owner's report: the explorer
# showed which side had the keyboard and the viewer did not.
#
# The frame is drawn ALWAYS (owner, 2026-07-31: "the panes should be framed by
# default, and the active frame just yellow"). Only the colour changes on focus -
# painting the border on focus alone also nudged the layout by a cell every time
# the keyboard moved.
PANE_CSS = """
PreviewPane {
    width: 2fr;
    border: round $panel;
}
PreviewPane:focus-within {
    border: round $warning;
}
PreviewPane #preview-heading {
    height: 1;
    padding: 0 1;
    color: $text-muted;
}
/* `TabbedContent` and `TabPane` are both `height: auto` by default, so a long
   Events tab grew past the pane instead of giving its VerticalScroll something
   to scroll - nothing moved however many keys were pressed. Pin all three to the
   space the pane has. */
PreviewPane TabbedContent {
    height: 1fr;
}
PreviewPane TabPane {
    height: 1fr;
    padding: 0;
}
PreviewPane VerticalScroll {
    height: 1fr;
    padding: 0 1;
}
"""


class PaneKeys:
    """Mixed into `PreviewPane`. Expects `query` / `query_one` - i.e. a Widget."""

    def focus_pane(self) -> None:
        """Take the keyboard, landing on the tab strip (so left/right work)."""
        strip = self._strip()
        if strip is not None:
            strip.focus()
            return
        self.focus()  # type: ignore[attr-defined]

    def _strip(self) -> ContentTabs | None:
        """The `Tabs` widget inside our `TabbedContent`, or None before mount."""
        for strip in self.query(ContentTabs):  # type: ignore[attr-defined]
            return strip
        return None

    def focused_here(self, focused) -> bool:
        """True when `focused` is this pane or anything inside it."""
        return focused is not None and self in focused.ancestors_with_self

    def activate_first(self, tab_id: str) -> None:
        """Make `tab_id` current if nothing is.

        After a rebuild NOTHING is active: `clear_panes()` is deferred and the
        `Tabs.Cleared` it eventually posts sets `active = ""` - *after* the new
        panes are in. Without this the first left/right press only selects the tab
        it was already on, and no tab looks current. Found with a real EC2 preview.
        """
        try:
            container = self.query_one("#preview-tabs", TabbedContent)  # type: ignore[attr-defined]
        except Exception:
            return
        if not container.active:
            container.active = tab_id

    def _body(self) -> VerticalScroll | None:
        """The scroll of the *active* tab.

        Walking `self.query(VerticalScroll)` and taking the first displayed one was
        wrong: only the inactive `TabPane` gets `display = False`, the scroll inside
        it keeps its own `display: block`. So the Overview scroll was always the one
        being moved and the Events tab never budged. Ask `TabbedContent` instead.
        """
        try:
            pane = self.query_one("#preview-tabs", TabbedContent).active_pane  # type: ignore[attr-defined]
        except Exception:
            return None
        if pane is None:
            return None
        for scroll in pane.query(VerticalScroll):
            return scroll
        return None

    def action_scroll_body(self, direction: int) -> None:
        """up/down scroll the visible tab body even while the strip has focus."""
        body = self._body()
        if body is None:
            return
        body.scroll_down(animate=False) if direction > 0 else body.scroll_up(animate=False)

    def action_page_body(self, direction: int) -> None:
        """page down / page up - a focused `Tabs` swallows them, so we forward."""
        body = self._body()
        if body is None:
            return
        body.scroll_page_down(animate=False) if direction > 0 else body.scroll_page_up(
            animate=False
        )

    def action_end_body(self, direction: int) -> None:
        """home / end - jump to the top or the bottom of the visible tab."""
        body = self._body()
        if body is None:
            return
        body.scroll_end(animate=False) if direction > 0 else body.scroll_home(animate=False)


def _self_check() -> None:
    """The contract: two arrow bindings and four methods, and no state of its own."""
    keys = [getattr(binding, "key", None) for binding in PANE_BINDINGS]

    assert keys == ["down", "up", "pagedown", "pageup", "home,ctrl+home", "end,ctrl+end"], keys

    assert "focus-within" in PANE_CSS, "the focused side has to be visible"
    assert "width: 2fr" in PANE_CSS, "the owner's 1/3 : 2/3 split"
    # The frame is unconditional; the focused one is only a different colour.
    assert "border: round $panel" in PANE_CSS, "the pane is framed even unfocused"
    assert "border: round $warning" in PANE_CSS, "the active frame is yellow"

    for name in (
        "focus_pane",
        "_strip",
        "focused_here",
        "activate_first",
        "_body",
        "action_scroll_body",
        "action_page_body",
        "action_end_body",
    ):
        assert callable(getattr(PaneKeys, name)), name
    assert not hasattr(PaneKeys, "__init__") or PaneKeys.__init__ is object.__init__

    # `focused_here` is pure: no widget tree needed to check what it answers.
    class Fake(PaneKeys):
        pass

    fake = Fake()
    assert fake.focused_here(None) is False

    class Elsewhere:
        ancestors_with_self: list[object] = []

    assert fake.focused_here(Elsewhere()) is False

    class Inside:
        ancestors_with_self = [fake]

    assert fake.focused_here(Inside()) is True

    # No pane, no crash: every scrolling action tolerates a missing body.
    class NoPane(PaneKeys):
        def query_one(self, *_a, **_k):
            raise LookupError

    nowhere = NoPane()
    assert nowhere._body() is None
    nowhere.action_scroll_body(1)
    nowhere.action_page_body(-1)
    nowhere.action_end_body(1)
    print("[OK] preview keys self-check passed")


if __name__ == "__main__":
    _self_check()
