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

from clitka.core.awsconfig import load_aws_config
from clitka.core.context import Context
from clitka.tui.dropdown import TextDrop
from clitka.tui.dropmenu import DropMenu
from clitka.tui.explorer import COMMON_TYPES, ExplorerScreen
from clitka.tui.keybar import KeyBar
from clitka.tui.picker import CommandPalette
from clitka.tui.status import StatusBar
from clitka.tui.switch import (
    PROFILE_HINT,
    PROFILE_TITLE,
    REGION_HINT,
    REGION_TITLE,
    profile_items,
    region_items,
)

_WELCOME = """\
CLITKA - CLI ToolKit for AWS

  :    open a resource type (Cloud Control explorer)
  F1   help
  F5   refresh identity
  F10  quit

Every screen has a scriptable CLI equivalent - try `clitka resources --help`.
"""

_HELP = """\
  :    command palette - pick a resource type to explore
  F1   this help (F1 or escape closes it)
  F2   switch profile - for this session only
  F3   switch region  - for this session only
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
        Binding("f2", "switch_profile", "Profile", show=False),
        Binding("f3", "switch_region", "Region", show=False),
        Binding("f5", "refresh", "Refresh", show=False),
        Binding("f10", "quit", "Quit", show=False),
        Binding("q", "quit", "Quit", show=False),
    ]

    def __init__(self, context: Context | None = None) -> None:
        super().__init__()
        self.context = context or Context.from_env()

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
        """F1: drop the key reference out from under the menu bar."""
        self.push_screen(TextDrop("F1  Help", _HELP, toggle_key="f1"))

    def action_palette(self) -> None:
        """`:` - choose a resource type and open the explorer for it."""
        self.push_screen(CommandPalette(COMMON_TYPES), self.open_type)

    def open_type(self, type_name: str | None) -> None:
        if type_name:
            self.push_screen(ExplorerScreen(self.context, type_name))

    # --- F2 / F3: switch profile and region for this session --------------

    def action_switch_profile(self) -> None:
        """F2: drop the profile list out from under the menu bar."""
        self._drop(
            PROFILE_TITLE,
            profile_items(load_aws_config(), self.context.profile),
            "f2",
            PROFILE_HINT,
            self._profile_chosen,
        )

    def action_switch_region(self) -> None:
        """F3: drop the region list out from under the menu bar."""
        self._drop(
            REGION_TITLE,
            region_items(self._regions(), self.context.effective_region),
            "f3",
            REGION_HINT,
            self._region_chosen,
        )

    def _regions(self) -> list[str]:
        """Region names botocore knows about; an empty list if it cannot say."""
        try:
            return sorted(self.context.session.get_available_regions("ec2"))
        except Exception:
            # A broken profile must not take the panel down - offer what we have.
            return [self.context.effective_region] if self.context.effective_region else []

    def _drop(self, title, items, key, hint, then) -> None:
        if not items:
            self.push_screen(TextDrop(title, "Nothing to choose from.", key))
            return
        self.push_screen(DropMenu(title, items, key, hint), then)

    def _profile_chosen(self, profile: object) -> None:
        """A new profile means a new Context - and a new identity to resolve."""
        if not isinstance(profile, str) or profile == self.context.profile:
            return
        # ponytail: the switch is in-memory only, by the owner's explicit call.
        # Ceiling: it is forgotten on exit; `clitka ctx use` is the way to persist.
        self.context = self.context.with_profile(profile)
        self._context_changed()

    def _region_chosen(self, region: object) -> None:
        if not isinstance(region, str) or region == self.context.region:
            return
        self.context = self.context.with_region(region)
        self._context_changed()

    def _context_changed(self) -> None:
        """Repaint the status bar, re-resolve the identity, reload the screen."""
        bar = self.query_one(StatusBar)
        bar.set_context(self.context)
        bar.set_pending()
        self.refresh_identity()
        for screen in self.screen_stack:
            adopt = getattr(screen, "adopt_context", None)
            if adopt is not None:
                adopt(self.context)


def run(context: Context | None = None) -> None:
    ClitkaApp(context).run()


if __name__ == "__main__":
    run()
