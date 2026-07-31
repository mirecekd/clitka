"""The CLITKA Textual application shell.

Layout (owner's request, revised 2026-07-30): the fixed F-key menu bar on top,
the status bar at the bottom, content in between. The menu is on top so the
F1..F4 drop-down panels slide out from directly under the key that was pressed.

Nothing here talks to AWS on the UI thread - the identity lookup runs in a thread
worker so the app paints instantly even when the SSO token has expired.

Everything that changes *who* we act as (P profile, R region) is in
`tui/appswitch.ContextSwitcher`, mixed in below. Those moved off F2/F3 onto
letters on 2026-07-31, which freed F3 for "view" and F4 for "edit".

Signing in is **not** part of the TUI (owner's call, 2026-07-31): it is a shell
job - `clitka auth login` - and F5 picks the fresh token up.

"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Static

from clitka.core.context import Context
from clitka.tui.appswitch import ContextSwitcher
from clitka.tui.apptext import HELP, WELCOME
from clitka.tui.dropdown import TextDrop
from clitka.tui.explorer import COMMON_TYPES, ExplorerScreen
from clitka.tui.keybar import KeyBar
from clitka.tui.picker import CommandPalette
from clitka.tui.restree import ResourceTree
from clitka.tui.restypes import TREE_TYPES
from clitka.tui.status import StatusBar
from clitka.tui.switch import type_names
from clitka.tui.windowpick import WindowSwitcher


class ClitkaApp(ContextSwitcher, WindowSwitcher, App[None]):
    """Root application. One instance per process."""

    TITLE = "CLITKA"
    CSS = """
    #body {
        padding: 1 2;
    }
    """
    # The context switches are letters now, upper and lower case alike (the
    # owner's call): `p` profile, `r` region.
    BINDINGS = [
        Binding("colon", "palette", "Command palette", show=False),
        Binding("f1", "help", "Help", show=False),
        Binding("p,P", "switch_profile", "Profile", show=False),
        Binding("r,R", "switch_region", "Region", show=False),
        Binding("w,W", "switch_window", "Window", show=False),
        Binding("f5", "refresh", "Refresh", show=False),
        Binding("f10", "quit", "Quit", show=False),
        Binding("q", "quit", "Quit", show=False),
    ]

    def __init__(self, context: Context | None = None, open_tree: bool = True) -> None:
        super().__init__()
        self.context = context or Context.from_env()
        # Open on the resource tree. `open_tree=False` stays on the welcome text - only
        # the tests that are about the shell itself, or a single explorer screen,
        # pass that.
        self.open_tree = open_tree
        # The last resolved identity, kept on the app because EVERY screen
        # composes its own StatusBar: a bar mounted after the lookup finished had
        # no way to learn the answer and stayed on "(resolving)" for good.
        # `None` means "not resolved yet" -> the bars show pending.
        self.account: str | None = None
        self.identity: str = ""

    def compose(self) -> ComposeResult:
        yield KeyBar()
        yield Container(Static(WELCOME, id="content"), id="body")
        yield StatusBar(self.context)

    def on_mount(self) -> None:
        """Resolve the identity and open the resource tree.

        The welcome text behind it is only a backdrop - CLITKA opens on the tree
        of resource types, and nothing is fetched until a branch is expanded.
        """
        self.refresh_identity()
        if self.open_tree:
            self.push_screen(ResourceTree(self.context, TREE_TYPES))

    # --- identity ---------------------------------------------------------

    def refresh_identity(self) -> None:
        """Forget the resolved identity and resolve it again off the UI thread."""
        self.account = None
        self.identity = ""
        self.paint_status()
        self.run_worker(self._load_identity, thread=True, exclusive=True)

    def _load_identity(self) -> None:
        ident = self.context.identity_or_none()
        account = ident.account if ident else "(unauthenticated)"
        display = ident.display if ident else ""
        self.call_from_thread(self._apply_identity, account, display)

    def _apply_identity(self, account: str, display: str) -> None:
        self.account = account
        self.identity = display
        self.paint_status()

    def paint_status(self) -> None:
        """Bring EVERY mounted status bar up to date with the current context.

        There is one bar per screen, not one per app, so this walks the whole
        screen stack. A bar that mounts later calls this itself from `on_mount` -
        that is the fix for a pushed screen sitting on "(resolving)" forever.
        """
        region = self.context.region
        if region is None:
            # botocore may have resolved a region from the profile.
            try:
                region = self.context.effective_region
            except Exception:
                region = None
        for screen in self.screen_stack:
            for bar in screen.query(StatusBar):
                bar.set_context(self.context)
                bar.set_region(region)
                if self.account is None:
                    bar.set_pending()
                else:
                    bar.set_identity(self.account, self.identity)

    # --- actions ----------------------------------------------------------

    def action_refresh(self) -> None:
        self.refresh_identity()

    def action_help(self) -> None:
        """F1: drop the key reference out from under the menu bar."""
        self.push_screen(TextDrop("F1  Help", HELP, toggle_key="f1"))

    def action_palette(self) -> None:
        """`:` - choose a resource type and open the explorer for it.

        The candidate list comes from `cloudformation:ListTypes`, which is a
        multi-page call, so it is fetched on a worker; the palette opens at once
        with the fallback list and is refilled when the real answer lands.
        """
        palette = CommandPalette(COMMON_TYPES)
        self.push_screen(palette, self.open_type)
        self.run_worker(
            lambda: self._load_types(palette), thread=True, exclusive=False, group="types"
        )

    def _load_types(self, palette: CommandPalette) -> None:
        found = type_names(self.context, COMMON_TYPES)
        self.call_from_thread(palette.set_candidates, list(found))

    def open_type(self, type_name: str | None) -> None:
        """What `:` does with the type it was given.

        On the tree it becomes a further branch (and is expanded); anywhere else
        it opens the flat explorer, which is what the `resources` CLI mirrors.
        """
        if not type_name:
            return
        for screen in reversed(self.screen_stack):
            if isinstance(screen, ResourceTree):
                screen.add_type(type_name)
                return
        self.push_screen(ExplorerScreen(self.context, type_name))


def run(context: Context | None = None) -> None:
    ClitkaApp(context).run()


if __name__ == "__main__":
    run()
