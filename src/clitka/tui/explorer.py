"""The resources explorer screen: pick a type, browse its resources.

Every AWS call happens on a thread worker; the screen only ever receives
finished results through `call_from_thread`, so the UI never blocks and a slow
or failing region cannot freeze the app.
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Static
from textual.worker import get_current_worker

from clitka.core import actions as act
from clitka.core import cloudcontrol as cc
from clitka.core.context import Context
from clitka.tui.actionhost import ActionHost
from clitka.tui.dropdown import TextDrop
from clitka.tui.keybar import KeyBar
from clitka.tui.qlconsole import QlConsole
from clitka.tui.restypes import COMMON_TYPES, EXPLORER_HELP, MAX_ROWS, PAGE_ROWS
from clitka.tui.shellhost import ShellHost
from clitka.tui.status import StatusBar
from clitka.tui.table import ResourceTable
from clitka.tui.viewedit import ViewEditHost

__all__ = ["COMMON_TYPES", "MAX_ROWS", "PAGE_ROWS", "ExplorerScreen"]


class ExplorerScreen(ViewEditHost, ShellHost, QlConsole, ActionHost, Screen[None]):
    """One resource type at a time, in the generic table.

    `ActionHost` supplies the whole F9 flow; this screen only says what a row is
    (`selected_ref`) and what to do afterwards (`_after_action`).
    """

    BINDINGS = [
        Binding("f1", "help", "Help", show=False),
        Binding("f3", "view", "View", show=False),
        Binding("f4", "edit", "Edit", show=False),
        Binding("x", "connect", "Shell", show=False),
        # Upper case alone - `q` is quit. See `treekeys.py` for why that matters.
        Binding("Q", "query", "PartiQL", show=False),
        # P/R/L are the app's, but a Screen shadows the App's bindings, so they
        # have to be forwarded explicitly or they would be dead inside here.
        Binding("p,P", "app.switch_profile", "Profile", show=False),
        Binding("r,R", "app.switch_region", "Region", show=False),
        Binding("w,W", "app.switch_window", "Window", show=False),
        Binding("c,C", "app.configure", "Config", show=False),
        Binding("f5", "reload", "Refresh", show=False),
        Binding("f9", "actions", "Actions", show=False),
        Binding("f10", "quit", "Quit", show=False),
        Binding("escape", "back", "Back", show=False),
    ]

    def __init__(self, context: Context, type_name: str) -> None:
        super().__init__()
        self.context = context
        self.type_name = type_name
        self.resources: list[cc.Resource] = []
        self.title_text = f"{type_name} - loading..."

    def compose(self) -> ComposeResult:
        yield KeyBar()
        yield Vertical(
            Static(f"{self.type_name} - loading...", id="explorer-title"),
            ResourceTable(),
            id="explorer-body",
        )
        yield StatusBar(self.context)

    def on_mount(self) -> None:
        self.reload()

    def adopt_context(self, context: Context) -> None:
        """The app switched profile or region (P/R) - re-list against the new one."""

        self.context = context
        self.query_one(StatusBar).set_context(context)
        self.reload()

    # --- loading ----------------------------------------------------------

    def reload(self) -> None:
        self.resources = []
        self._title(f"{self.type_name} - loading...")
        self.run_worker(self._load, thread=True, exclusive=True)

    def _load(self) -> None:
        """Page through the type, handing each page to the table as it arrives.

        A busy account can hold thousands of resources, and waiting for all of
        them before showing anything makes the explorer feel broken. `MAX_ROWS`
        is a stop, not a page size.
        """
        worker = get_current_worker()
        page: list[cc.Resource] = []
        try:
            for resource in cc.iter_resources(self.context, self.type_name):
                if worker.is_cancelled:
                    return
                page.append(resource)
                if len(page) >= PAGE_ROWS:
                    self.app.call_from_thread(self._page, page)
                    page = []
                    if self._reached_limit():
                        break
        except Exception as exc:  # any failure must reach the user, not the log
            self.app.call_from_thread(self._failed, exc)
            return
        if worker.is_cancelled:
            return
        self.app.call_from_thread(self._page, page)
        self.app.call_from_thread(self._done)

    def _reached_limit(self) -> bool:
        return len(self.resources) >= MAX_ROWS

    def _page(self, found: list[cc.Resource]) -> None:
        """One page has landed. The first one also decides the columns."""
        if not found and self.resources:
            return
        table = self.query_one(ResourceTable)
        rows = [resource.row() for resource in found]
        if not self.resources:
            # ponytail: the columns come from the first page only. Ceiling: a
            # property that appears exclusively in a later page gets no column.
            # Upgrade path: recompute columns and rebuild when a new key shows up.
            self.resources = found
            table.set_rows(rows, cc.columns_for(found) or ["identifier"])
            # The first page is the moment the screen becomes usable, so hand the
            # keyboard to the rows right away.
            table.focus_table()
        else:
            self.resources.extend(found)
            table.add_rows(rows)
        self._title(f"{self.type_name} - {len(self.resources)} resources, loading...")

    def _done(self) -> None:
        total = len(self.resources)
        capped = " (stopped at the display limit)" if total >= MAX_ROWS else ""
        self._title(f"{self.type_name} - {total} resources{capped}")

    def _failed(self, exc: Exception) -> None:
        self.query_one(ResourceTable).set_rows([], columns=["identifier"])
        self._title(f"{self.type_name}\n[ERROR] {exc}")

    def _title(self, text: str) -> None:
        """Set the heading. Kept as an attribute too, so tests can read it."""
        self.title_text = text
        self.query_one("#explorer-title", Static).update(text)

    # --- actions ----------------------------------------------------------

    def selected(self) -> dict[str, Any] | None:
        return self.query_one(ResourceTable).selected_row()

    def selected_ref(self) -> act.ResourceRef | None:
        row = self.selected()
        return None if row is None else act.ResourceRef.from_row(self.type_name, row)

    def _after_action(self, reload: bool) -> None:
        """`ActionHost` calls this once an action has finished."""
        if reload:
            self.reload()
        else:
            self._title(f"{self.type_name} - {len(self.resources)} resources")

    def action_reload(self) -> None:

        self.reload()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_help(self) -> None:
        """F1: the same drop-down panel the welcome screen uses."""
        self.app.push_screen(TextDrop("F1  Help - explorer", EXPLORER_HELP, "f1"))
