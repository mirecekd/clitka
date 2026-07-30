"""The CLITKA Textual application shell.

Layout (owner's request, revised 2026-07-30): the fixed F-key menu bar on top,
the status bar at the bottom, content in between. The menu is on top so the
F1/F2/F3 drop-down panels slide out from directly under the key that was pressed.

Nothing here talks to AWS on the UI thread - the identity lookup runs in a thread
worker so the app paints instantly even when the SSO token has expired.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Static

from clitka.core.context import Context
from clitka.tui.explorer import COMMON_TYPES, ExplorerScreen
from clitka.tui.keybar import KeyBar
from clitka.tui.picker import CommandPalette
from clitka.tui.status import StatusBar

_WELCOME = """\
CLITKA - CLI ToolKit for AWS

  :    open a resource type (Cloud Control explorer)
  F1   help
  F5   refresh identity
  F10  quit

Every screen has a scriptable CLI equivalent - try `clitka resources --help`.
"""

_HELP = """\
Keys

  :    command palette - pick a resource type to explore
  F1   this help
  F2   switch profile   (not implemented yet)
  F3   switch region    (not implemented yet)
  F5   refresh
  F9   actions for the selected resource (inside the explorer)
  F10  quit
  q    quit

Inside the explorer: / filters, s sorts the current column, F9 opens the action
menu for the highlighted row, escape goes back. Destructive actions always ask
first, and "no" is the default answer.

The status bar at the bottom always shows which profile, account and region every
call would use, and says READ-ONLY when mutating operations are refused.
"""


class ClitkaApp(App[None]):
    """Root application. One instance per process."""

    TITLE = "CLITKA"
    CSS = """
    #body {
        padding: 1 2;
    }
    """
    BINDINGS = [
        Binding("colon", "palette", "Command palette", show=False),
        Binding("f1", "help", "Help", show=False),
        Binding("f5", "refresh", "Refresh", show=False),
        Binding("f10", "quit", "Quit", show=False),
        Binding("q", "quit", "Quit", show=False),
        Binding("escape", "dismiss_help", "Back", show=False),
    ]

    def __init__(self, context: Context | None = None) -> None:
        super().__init__()
        self.context = context or Context.from_env()
        self._showing_help = False

    def compose(self) -> ComposeResult:
        yield KeyBar()
        yield Container(Static(_WELCOME, id="content"), id="body")
        yield StatusBar(self.context)

    def on_mount(self) -> None:
        self.refresh_identity()

    # --- identity ---------------------------------------------------------

    def refresh_identity(self) -> None:
        """Resolve the caller identity off the UI thread."""
        self.run_worker(self._load_identity, thread=True, exclusive=True)

    def _load_identity(self) -> None:
        ident = self.context.identity_or_none()
        account = ident.account if ident else "(unauthenticated)"
        display = ident.display if ident else ""
        self.call_from_thread(self._apply_identity, account, display)

    def _apply_identity(self, account: str, display: str) -> None:
        bar = self.query_one(StatusBar)
        bar.set_context(self.context)
        if self.context.region is None:
            # botocore may have resolved a region from the profile.
            bar.set_region(self.context.effective_region)
        bar.set_identity(account, display)

    # --- actions ----------------------------------------------------------

    def action_refresh(self) -> None:
        self.query_one(StatusBar).set_pending()
        self.refresh_identity()

    def action_help(self) -> None:
        self._showing_help = True
        self.query_one("#content", Static).update(_HELP)

    def action_dismiss_help(self) -> None:
        if self._showing_help:
            self._showing_help = False
            self.query_one("#content", Static).update(_WELCOME)

    def action_palette(self) -> None:
        """`:` - choose a resource type and open the explorer for it."""
        self.push_screen(CommandPalette(COMMON_TYPES), self.open_type)

    def open_type(self, type_name: str | None) -> None:
        if type_name:
            self.push_screen(ExplorerScreen(self.context, type_name))


def run(context: Context | None = None) -> None:
    ClitkaApp(context).run()


if __name__ == "__main__":
    run()
