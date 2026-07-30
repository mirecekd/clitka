"""Drop-down panels that slide out from under the top F-key menu bar.

Turbo Vision / Norton Commander behaviour: press F2, a panel appears directly
below the F2 slot, pick something with one keystroke or the cursor, press the
same F-key again (or escape) to close it.

This module holds the panel geometry (`DropPanel`), the row data type
(`MenuItem`) and the plain text panel (`TextDrop`, used for F1 help). The
chooser itself is `dropmenu.DropMenu` - split out to keep both files small.

ponytail: these are `ModalScreen`s, not overlay widgets. A modal screen already
gives us key capture and a dismiss-with-value contract for free, and the panel
still looks anchored because the CSS pins it below the menu bar. Ceiling: the
content underneath is dimmed rather than fully live. Upgrade path: mount a plain
widget into the screen and manage focus by hand.
"""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static


@dataclass(frozen=True)
class MenuItem:
    """One row of a drop-down menu.

    `key` is an optional single keystroke that runs the item immediately.
    `value` is what the screen dismisses with; `detail` is dimmed trailing text.
    `current` marks the item that reflects the present state (active profile).
    """

    label: str
    value: str
    key: str = ""
    detail: str = ""
    current: bool = False

    def line(self) -> str:
        mark = "*" if self.current else " "
        shortcut = f"{self.key}  " if self.key else "   "
        text = f"{mark} {shortcut}{self.label}"
        return f"{text}   [dim]{self.detail}[/dim]" if self.detail else text

    def haystack(self) -> str:
        """What the filter box matches against."""
        return f"{self.label} {self.detail}"


class DropPanel(ModalScreen[object]):
    """Base geometry: a bordered panel pinned under the menu bar, top-left.

    Subclasses compose into `Vertical`. `TOGGLE_KEY` is the F-key that opened the
    panel; pressing it again closes it, which is what makes the menu bar feel
    like a menu bar rather than a set of one-way doors.
    """

    TOGGLE_KEY: str = ""

    DEFAULT_CSS = """
    DropPanel {
        align: left top;
        background: $background 40%;
    }
    DropPanel > Vertical {
        margin-top: 1;
        margin-left: 1;
        width: auto;
        min-width: 40;
        max-width: 80%;
        max-height: 80%;
        height: auto;
        border: round $accent;
        background: $surface;
    }
    DropPanel Static.title {
        height: 1;
        padding: 0 1;
        background: $accent;
        color: $text;
    }
    DropPanel Static.hint {
        height: auto;
        padding: 0 1;
        color: $text-muted;
    }
    """
    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
    ]

    def on_mount(self) -> None:
        self._mark_bar(self.TOGGLE_KEY or None)

    def _mark_bar(self, key: str | None) -> None:
        """Highlight (or un-highlight) our slot on the key bar behind us.

        `app.query()` only searches the *active* screen, which is this modal, so
        the bar has to be looked up on every screen in the stack instead.
        """
        from clitka.tui.keybar import KeyBar

        for screen in self.app.screen_stack:
            if screen is self:
                continue
            for bar in screen.query(KeyBar):
                bar.set_open(key)

    def action_close(self) -> None:
        self.dismiss(None)

    def _on_screen_suspend(self) -> None:
        self._mark_bar(None)

    def on_key(self, event) -> None:
        """The opening F-key toggles the panel shut."""
        if self.TOGGLE_KEY and event.key == self.TOGGLE_KEY.lower():
            event.stop()
            event.prevent_default()
            self.dismiss(None)


class TextDrop(DropPanel):
    """A scrollable block of text under the menu bar - F1 help lives here."""

    DEFAULT_CSS = """
    TextDrop > Vertical {
        min-width: 60;
    }
    TextDrop VerticalScroll {
        height: auto;
        max-height: 100%;
        padding: 0 1;
    }
    """

    def __init__(self, title: str, body: str, toggle_key: str = "") -> None:
        super().__init__()
        self.TOGGLE_KEY = toggle_key
        self.title_text = title
        self.body = body

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.title_text, classes="title")
            with VerticalScroll():
                yield Static(self.body, id="drop-text")

    def on_mount(self) -> None:
        super().on_mount()
        self.query_one(VerticalScroll).focus()


def _self_check() -> None:
    item = MenuItem("k-d-mirdvorak", "k-d-mirdvorak", detail="sso eu-central-1")
    assert "k-d-mirdvorak" in item.line()
    assert "[dim]sso eu-central-1[/dim]" in item.line()
    assert item.line().startswith("  ")
    assert item.haystack() == "k-d-mirdvorak sso eu-central-1"
    assert MenuItem("x", "x", current=True).line().startswith("*")
    assert MenuItem("x", "x", key="d").line().strip().startswith("d")

    drop = TextDrop("Help", "line one\nline two", toggle_key="f1")
    assert drop.body.startswith("line one")
    assert drop.TOGGLE_KEY == "f1"
    print("[OK] dropdown self-check passed")


if __name__ == "__main__":
    _self_check()
