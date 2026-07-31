"""What F9 offers on a log group, and what the preview pane shows about one.

Both are published through pluggy hooks, so the tree and the F9 menu never import
this module - they only ever see `Action` and `PreviewTab` objects.
"""

from __future__ import annotations

from clitka.core import logs as lg
from clitka.core import preview as pv
from clitka.core import timerange as tr
from clitka.core.actions import Action, ActionResult, ResourceRef
from clitka.core.context import Context

TYPE_NAME = "AWS::Logs::LogGroup"
PREVIEW_LIMIT = 300
STREAM_LIMIT = 20


def is_log_group(ref: ResourceRef) -> bool:
    return ref.type_name == TYPE_NAME


def group_name(ref: ResourceRef) -> str:
    """The group's name. Cloud Control identifies a log group *by* its name."""
    return ref.identifier or str(ref.row.get("LogGroupName", ""))


def _events_text(ctx: Context, name: str, minutes: float, limit: int) -> str:
    events = lg.recent_events(ctx, name, minutes=minutes, limit=limit)
    window = tr.human(minutes)
    if not events:
        return f"[dim](nothing in the last {window})[/dim]"
    head = f"[dim]{len(events)} event(s) in the last {window}[/dim]"
    # No markup: a log line full of brackets must not be re-interpreted as one.
    body = "\n".join(escape_markup(event.line(show_stream=True)) for event in events)
    return f"{head}\n\n{body}"


def escape_markup(text: str) -> str:
    """Make a log line safe to hand to Rich - a JSON payload is full of brackets."""
    return text.replace("[", "\\[")


def show_events(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: the chosen time window of events, as a scrollable result.

    The window is whatever `w` last picked - see `core/timerange.py`.
    """
    name = group_name(ref)
    minutes = tr.minutes()
    return ActionResult(
        f"{name} - last {tr.human(minutes)}",
        _events_text(ctx, name, minutes, PREVIEW_LIMIT),
    )


def events_label() -> str:
    """The F9 menu entry, which names the window it is about to fetch."""
    return f"Last {tr.human(tr.minutes())} of events"


def show_streams(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: which streams have been written to, most recent first."""
    name = group_name(ref)
    lines: list[str] = []
    for stream in lg.iter_log_streams(ctx, name):
        row = stream.row()
        lines.append(f"{row['last_event'] or '-':<21} {row['stored']:>8}  {row['identifier']}")
        if len(lines) >= STREAM_LIMIT:
            break
    body = "\n".join(lines) if lines else "[dim](no streams)[/dim]"
    return ActionResult(f"{name} - streams", body)


def show_tail_hint(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: how to follow this group live, from here or from a shell.

    ponytail: the F9 menu hands the user the command rather than opening the tail
    screen, because an action returns one finished result and a tail never
    finishes. Ceiling: one extra keypress. Upgrade path: the tail screen is bound
    to `t` on the logs preview tab, and `Action` would need a `stream` attribute.
    """
    name = group_name(ref)
    where = f" -p {ctx.profile}" if ctx.profile else ""
    return ActionResult(
        f"{name} - live tail",
        "Follow this group live:\n\n"
        f"  clitka{where} logs tail {name}\n\n"
        "In the TUI, press `t` on the group's Events preview tab.",
    )


# The ids are namespaced like the `resources` plugin's, so two plugins can never
# collide in the F9 menu.
ACTIONS: tuple[Action, ...] = (
    Action(
        id="logs.events",
        # A static label would lie as soon as `w` changed the window; the menu
        # asks for `label` again every time it opens.
        label=events_label,
        run=show_events,
        key="l",
        applies_to=is_log_group,
    ),
    Action(
        id="logs.streams",
        label="Log streams",
        run=show_streams,
        key="m",
        applies_to=is_log_group,
    ),
    Action(
        id="logs.tail",
        label="Live tail (how to)",
        run=show_tail_hint,
        key="t",
        applies_to=is_log_group,
    ),
)


def build_events_tab(ctx: Context, ref: ResourceRef) -> str:
    """The `Events` preview tab: the log itself, right beside the tree."""
    return _events_text(ctx, group_name(ref), tr.minutes(), PREVIEW_LIMIT)


PREVIEWS: tuple[pv.PreviewTab, ...] = (
    pv.PreviewTab(
        id="logs.events",
        label="Events",
        build=build_events_tab,
        applies_to=is_log_group,
        lazy=True,  # it calls FilterLogEvents, so only when the tab is shown
    ),
)


def _self_check() -> None:
    ref = ResourceRef.from_row(TYPE_NAME, {"identifier": "/aws/lambda/x"})
    assert is_log_group(ref)
    assert not is_log_group(ResourceRef.from_row("AWS::S3::Bucket", {}))
    assert group_name(ref) == "/aws/lambda/x"
    # Cloud Control sometimes reports the name as a property instead.
    assert group_name(ResourceRef(TYPE_NAME, "", {"LogGroupName": "/a"})) == "/a"

    # A JSON log line must not be read as Rich markup.
    assert escape_markup('{"a": [1]}') == '{"a": \\[1]}'

    ids = [action.id for action in ACTIONS]
    assert len(set(ids)) == len(ids)
    keys = [action.key for action in ACTIONS]
    assert len(set(keys)) == len(keys), keys
    assert all(action.applies_to(ref) for action in ACTIONS)
    assert not any(action.destructive for action in ACTIONS)

    hint = show_tail_hint(Context(profile="sw-sandbox"), ref)
    assert "clitka -p sw-sandbox logs tail /aws/lambda/x" in hint.body
    assert not show_tail_hint(Context(), ref).body.count("-p ")

    assert [tab.id for tab in PREVIEWS] == ["logs.events"]

    assert PREVIEWS[0].lazy and PREVIEWS[0].matches_type(TYPE_NAME)
    print("[OK] logs actions self-check passed")


if __name__ == "__main__":
    _self_check()
