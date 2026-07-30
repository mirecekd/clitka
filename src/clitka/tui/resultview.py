"""The screen an action's output lands on: a scrollable, read-only document.

Actions return text (YAML, JSON, an identifier, an API response), and the user
needs to page through it, not have it flash by in a toast.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Static

from clitka.core.actions import ActionResult
from clitka.core.context import Context
from clitka.tui.keybar import KeyBar
from clitka.tui.status import StatusBar


class ResultScreen(Screen[None]):
    """Show one action result. Escape goes back to where it was invoked from."""

    DEFAULT_CSS = """
    ResultScreen #result-title {
        height: auto;
        padding: 0 1;
        color: $text-muted;
    }
    ResultScreen #result-body {
        height: 1fr;
        padding: 0 1;
    }
    """
    BINDINGS = [
        Binding("escape", "back", "Back", show=False),
        Binding("f10", "quit", "Quit", show=False),
    ]

    def __init__(self, context: Context, result: ActionResult) -> None:
        super().__init__()
        self.context = context
        self.result = result

    def compose(self) -> ComposeResult:
        yield KeyBar()
        yield Static(self.heading(), id="result-title")
        with VerticalScroll(id="result-body"):
            yield Static(self.result.body or "(no output)")
        yield StatusBar(self.context)

    def heading(self) -> str:
        return f"{self.result.title}  (escape to go back)"

    def action_back(self) -> None:
        self.app.pop_screen()


def _self_check() -> None:
    screen = ResultScreen(Context(), ActionResult("identifier", "my-bucket"))
    assert "identifier" in screen.heading()
    assert screen.result.body == "my-bucket"
    print("[OK] result view self-check passed")


if __name__ == "__main__":
    _self_check()
