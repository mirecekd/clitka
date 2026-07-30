"""The `:` command palette: type a resource type (or a command) and go there.

k9s-inspired: one keystroke, type a few characters, enter. The list narrows as
you type and the first match is what enter picks.
"""

from __future__ import annotations

from collections.abc import Sequence

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, ListItem, ListView, Static


def rank(candidates: Sequence[str], needle: str, limit: int = 40) -> list[str]:
    """Candidates that contain `needle`, prefix matches first.

    ponytail: substring matching with a prefix bonus, not a fuzzy matcher.
    Ceiling: "s3b" will not find "AWS::S3::Bucket". Upgrade path: swap this one
    function for `rapidfuzz`.
    """
    if not needle:
        return list(candidates)[:limit]
    lowered = needle.lower()
    prefix = [c for c in candidates if c.lower().startswith(lowered)]
    contains = [c for c in candidates if lowered in c.lower() and c not in prefix]
    return (prefix + contains)[:limit]


class CommandPalette(ModalScreen[str | None]):
    """Modal chooser; dismisses with the chosen string or None."""

    DEFAULT_CSS = """
    CommandPalette {
        align: center middle;
    }
    CommandPalette > Vertical {
        width: 70%;
        max-height: 60%;
        border: round $primary;
        background: $surface;
    }
    CommandPalette Static.prompt {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    """
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("down", "next", "Next", show=False),
        Binding("up", "previous", "Previous", show=False),
    ]

    def __init__(self, candidates: Sequence[str], prompt: str = "resource type") -> None:
        super().__init__()
        self.candidates = list(candidates)
        self.prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(f"{self.prompt} (enter to open, escape to cancel)", classes="prompt")
            yield Input(placeholder=self.prompt, id="palette-input")
            yield ListView(id="palette-list")

    def on_mount(self) -> None:
        self._refill("")
        self.query_one(Input).focus()

    def _refill(self, needle: str) -> None:
        listing = self.query_one(ListView)
        listing.clear()
        self.matches = rank(self.candidates, needle)
        for match in self.matches:
            listing.append(ListItem(Static(match)))
        if self.matches:
            listing.index = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refill(event.value)

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self.action_accept()

    def on_list_view_selected(self, _event: ListView.Selected) -> None:
        self.action_accept()

    def action_accept(self) -> None:
        index = self.query_one(ListView).index or 0
        typed = self.query_one(Input).value.strip()
        if self.matches:
            self.dismiss(self.matches[min(index, len(self.matches) - 1)])
        else:
            # Let the user open a type that is not in the candidate list.
            self.dismiss(typed or None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_next(self) -> None:
        self.query_one(ListView).action_cursor_down()

    def action_previous(self) -> None:
        self.query_one(ListView).action_cursor_up()


def _self_check() -> None:
    types = ["AWS::S3::Bucket", "AWS::S3::AccessPoint", "AWS::Lambda::Function"]
    assert rank(types, "") == types
    assert rank(types, "aws::s3")[0].startswith("AWS::S3")
    assert rank(types, "lambda") == ["AWS::Lambda::Function"]
    assert rank(types, "bucket") == ["AWS::S3::Bucket"]
    assert rank(types, "zzz") == []
    assert len(rank(types * 40, "aws", limit=5)) == 5
    print("[OK] palette self-check passed")


if __name__ == "__main__":
    _self_check()
