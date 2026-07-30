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

from clitka.core import actions as act
from clitka.core import cloudcontrol as cc
from clitka.core.context import Context
from clitka.tui.actionmenu import ActionMenu, ConfirmModal
from clitka.tui.keybar import KeyBar
from clitka.tui.resultview import ResultScreen
from clitka.tui.status import StatusBar
from clitka.tui.table import ResourceTable

# ponytail: a short starter list of types that list cleanly without a parent
# identifier, so the explorer is useful before `resources types` is browsable.
# Ceiling: not exhaustive. Upgrade path: the type picker screen (F2 in explorer)
# backed by cloudformation:ListTypes.
COMMON_TYPES: tuple[str, ...] = (
    "AWS::S3::Bucket",
    "AWS::Lambda::Function",
    "AWS::DynamoDB::Table",
    "AWS::EC2::Instance",
    "AWS::EC2::VPC",
    "AWS::ECS::Cluster",
    "AWS::ECR::Repository",
    "AWS::CloudFormation::Stack",
    "AWS::Logs::LogGroup",
    "AWS::StepFunctions::StateMachine",
    "AWS::ApiGateway::RestApi",
    "AWS::SNS::Topic",
    "AWS::SQS::Queue",
    "AWS::IAM::Role",
)


class ExplorerScreen(Screen[None]):
    """One resource type at a time, in the generic table."""

    BINDINGS = [
        Binding("f1", "help", "Help", show=False),
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

    # --- loading ----------------------------------------------------------

    def reload(self) -> None:
        self._title(f"{self.type_name} - loading...")
        self.run_worker(self._load, thread=True, exclusive=True)

    def _load(self) -> None:
        try:
            found = cc.list_resources(self.context, self.type_name, limit=500)
        except Exception as exc:  # any failure must reach the user, not the log
            self.app.call_from_thread(self._failed, exc)
            return
        self.app.call_from_thread(self._loaded, found)

    def _loaded(self, found: list[cc.Resource]) -> None:
        self.resources = found
        table = self.query_one(ResourceTable)
        table.set_rows([resource.row() for resource in found], cc.columns_for(found))
        self._title(f"{self.type_name} - {len(found)} resources")

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

    # --- F9 action menu ---------------------------------------------------

    def action_actions(self) -> None:
        """F9: offer whatever the plugins say applies to the selected row."""
        ref = self.selected_ref()
        if ref is None:
            self._title(f"{self.type_name}\nNothing selected - no actions to offer")
            return
        offered = act.available(act.registered(), ref)
        subject = f"{ref.type_name} {ref.identifier}"
        self.app.push_screen(ActionMenu(offered, subject), self._chosen)

    def _chosen(self, action: act.Action | None) -> None:
        ref = self.selected_ref()
        if action is None or ref is None:
            return
        if not action.destructive:
            self._start(action, ref)
            return
        detail = (
            f"profile: {self.context.profile or '(default)'}  "
            f"region: {self.context.effective_region}"
        )
        self.app.push_screen(
            ConfirmModal(f"{action.label}: {ref.type_name} '{ref.identifier}'?", detail),
            lambda ok: self._start(action, ref) if ok else None,
        )

    def _start(self, action: act.Action, ref: act.ResourceRef) -> None:
        """Run the action off the UI thread - any of them may call AWS."""
        self._title(f"{self.type_name}\n{action.label} - running...")
        self.run_worker(
            lambda: self._run(action, ref), thread=True, exclusive=False, group="action"
        )

    def _run(self, action: act.Action, ref: act.ResourceRef) -> None:
        try:
            result = action.run(self.context, ref)
        except Exception as exc:
            self.app.call_from_thread(self._action_failed, action, exc)
            return
        self.app.call_from_thread(self._action_done, result)

    def _action_done(self, result: act.ActionResult) -> None:
        self.app.push_screen(ResultScreen(self.context, result))
        if result.reload:
            self.reload()
        else:
            self._title(f"{self.type_name} - {len(self.resources)} resources")

    def _action_failed(self, action: act.Action, exc: Exception) -> None:
        self._title(f"{self.type_name}\n[ERROR] {action.label}: {exc}")

    def action_reload(self) -> None:
        self.reload()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_help(self) -> None:
        self._title(
            f"{self.type_name}\n"
            "/ filter   s sort the current column   F5 reload   F9 actions   "
            "escape back   F10 quit"
        )
