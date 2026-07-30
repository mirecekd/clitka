"""F9: the action menu, generated from the registered actions.

The menu never hard-codes what a screen can do - it lists whatever
`actions.available()` returns for the selected row, so a new plugin gets its
actions into the menu without touching the TUI.
"""

from __future__ import annotations

from collections.abc import Sequence

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import ListItem, ListView, Static

from clitka.core.actions import Action


class ActionMenu(ModalScreen[Action | None]):
    """Pick one action for the selected resource, or escape."""

    DEFAULT_CSS = """
    ActionMenu {
        align: center middle;
    }
    ActionMenu > Vertical {
        width: 60%;
        max-height: 60%;
        border: round $primary;
        background: $surface;
    }
    ActionMenu Static.heading {
        height: auto;
        padding: 0 1;
        color: $text-muted;
    }
    """
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("f9", "cancel", "Cancel", show=False),
    ]

    def __init__(self, actions: Sequence[Action], subject: str = "") -> None:
        super().__init__()
        self.actions = list(actions)
        self.subject = subject

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.heading(), classes="heading")
            yield ListView(
                *[ListItem(Static(action.menu_label())) for action in self.actions],
                id="action-list",
            )

    def heading(self) -> str:
        if not self.actions:
            return "No actions for this selection (escape to close)"
        return f"Actions for {self.subject or 'the selection'} (enter to run, escape to cancel)"

    def on_mount(self) -> None:
        listing = self.query_one(ListView)
        if self.actions:
            listing.index = 0
        listing.focus()

    def on_key(self, event) -> None:
        """A single keystroke runs the action that claims it (k9s-style)."""
        for action in self.actions:
            if action.key and event.key == action.key:
                event.stop()
                event.prevent_default()
                self.dismiss(action)
                return

    def on_list_view_selected(self, _event: ListView.Selected) -> None:
        self.action_accept()

    def action_accept(self) -> None:
        index = self.query_one(ListView).index
        if self.actions and index is not None and 0 <= index < len(self.actions):
            self.dismiss(self.actions[index])
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmModal(ModalScreen[bool]):
    """A yes/no gate in front of every destructive action.

    Defaults to "no": enter alone must never delete anything.
    """

    DEFAULT_CSS = """
    ConfirmModal {
        align: center middle;
    }
    ConfirmModal > Vertical {
        width: 60%;
        height: auto;
        border: round $error;
        background: $surface;
        padding: 1 2;
    }
    """
    BINDINGS = [
        Binding("escape", "refuse", "No", show=False),
        Binding("n", "refuse", "No", show=False),
        Binding("y", "accept", "Yes", show=False),
        Binding("enter", "refuse", "No", show=False),
    ]

    def __init__(self, question: str, detail: str = "") -> None:
        super().__init__()
        self.question = question
        self.detail = detail

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.line(), id="confirm-text")

    def line(self) -> str:
        detail = f"\n{self.detail}" if self.detail else ""
        return f"{self.question}{detail}\n\ny = yes, anything else = no"

    def action_accept(self) -> None:
        self.dismiss(True)

    def action_refuse(self) -> None:
        self.dismiss(False)


def _self_check() -> None:
    from clitka.core.actions import ActionResult

    noop = Action("x", "Noop", lambda _c, _r: ActionResult("ok"), key="n")
    menu = ActionMenu([noop], subject="AWS::S3::Bucket one")
    assert "AWS::S3::Bucket one" in menu.heading()
    assert "No actions" in ActionMenu([]).heading()
    confirm = ConfirmModal("DELETE bucket one?", "profile: demo")
    assert "DELETE bucket one?" in confirm.line()
    assert "profile: demo" in confirm.line()
    print("[OK] action menu self-check passed")


if __name__ == "__main__":
    _self_check()
