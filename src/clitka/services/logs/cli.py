"""`clitka logs` - groups, search and live tail from the command line.

The scriptable half of M3. `tail` is the interesting one: it is the same
`core.livetail.LiveTail` the TUI screen drives, only with `print` as the sink and
ctrl-c as the stop button.
"""

from __future__ import annotations

import signal
import sys
from typing import Any

import typer

from clitka.core import logs as lg
from clitka.core.context import Context
from clitka.core.errors import ClitkaError
from clitka.core.livetail import MAX_GROUPS, LiveTail
from clitka.core.output import OutputFormat, console, err_console, render

app = typer.Typer(no_args_is_help=True, help="CloudWatch Logs: browse, search and tail.")

GROUP_COLUMNS = ["identifier", "stored", "retention"]
STREAM_COLUMNS = ["identifier", "last_event", "stored"]
EVENT_COLUMNS = ["timestamp", "stream", "message"]


def _ctx(typer_ctx: typer.Context) -> Context:
    state = typer_ctx.obj
    return state["context"] if state else Context.from_env()


def _fail(exc: Exception) -> typer.Exit:
    err_console.print(f"[ERROR] {exc}")
    return typer.Exit(1)


def _event_rows(events: list[lg.LogEvent]) -> list[dict[str, Any]]:
    return [
        {
            "timestamp": event.timestamp,
            "stream": event.stream,
            "message": event.message.rstrip("\n"),
        }
        for event in events
    ]


@app.command("groups")
def groups(
    typer_ctx: typer.Context,
    prefix: str = typer.Option(None, "--prefix", "-p", help="Only groups whose name starts here."),
    limit: int = typer.Option(None, "--limit", "-n", help="Stop after this many groups."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """List log groups."""
    ctx = _ctx(typer_ctx)
    try:
        found = lg.list_log_groups(ctx, prefix=prefix, limit=limit)
    except ClitkaError as exc:
        raise _fail(exc) from exc
    render(
        [group.row() for group in found],
        fmt=output,
        columns=GROUP_COLUMNS,
        title="log groups",
    )


@app.command("streams")
def streams(
    typer_ctx: typer.Context,
    group: str = typer.Argument(..., help="Log group name."),
    limit: int = typer.Option(20, "--limit", "-n", help="Stop after this many streams."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """List a group's streams, most recently written first."""
    ctx = _ctx(typer_ctx)
    rows: list[dict[str, Any]] = []
    try:
        for stream in lg.iter_log_streams(ctx, group):
            rows.append(stream.row())
            if limit and len(rows) >= limit:
                break
    except ClitkaError as exc:
        raise _fail(exc) from exc
    render(rows, fmt=output, columns=STREAM_COLUMNS, title=group)


@app.command("search")
def search(
    typer_ctx: typer.Context,
    group: str = typer.Argument(..., help="Log group name."),
    pattern: str = typer.Option(None, "--pattern", "-f", help="CloudWatch filter pattern."),
    minutes: float = typer.Option(60.0, "--minutes", "-m", help="How far back to look."),
    limit: int = typer.Option(200, "--limit", "-n", help="Stop after this many events."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """Search a group's events with FilterLogEvents."""
    ctx = _ctx(typer_ctx)
    try:
        found = lg.recent_events(ctx, group, minutes=minutes, pattern=pattern, limit=limit)
    except ClitkaError as exc:
        raise _fail(exc) from exc
    if output.resolve() is OutputFormat.TABLE:
        for event in found:
            console.print(event.line(show_stream=True), highlight=False, markup=False)
        console.print(f"[dim]{len(found)} event(s)[/dim]")
        return
    render(_event_rows(found), fmt=output, columns=EVENT_COLUMNS, title=group)


@app.command("tail")
def tail(
    typer_ctx: typer.Context,
    groups_wanted: list[str] = typer.Argument(..., help=f"Up to {MAX_GROUPS} log group names."),
    pattern: str = typer.Option(None, "--pattern", "-f", help="CloudWatch filter pattern."),
    show_stream: bool = typer.Option(False, "--stream", "-s", help="Print the stream name too."),
) -> None:
    """Follow log groups live (StartLiveTail). Ctrl-C stops it."""
    ctx = _ctx(typer_ctx)
    try:
        arns = [lg.get_log_group(ctx, name).tail_arn for name in groups_wanted]
    except (ClitkaError, LookupError) as exc:
        raise _fail(exc) from exc

    def show(events: list[lg.LogEvent]) -> None:
        for event in events:
            # print, not console.print: a log line must never be re-interpreted
            # as Rich markup, and this has to keep up with a busy group.
            sys.stdout.write(event.line(show_stream=show_stream) + "\n")
        sys.stdout.flush()

    try:
        session = LiveTail(ctx, arns, pattern=pattern, on_events=show)
    except ValueError as exc:
        raise _fail(exc) from exc

    def bye(_signum: int, _frame: Any) -> None:
        session.stop()

    signal.signal(signal.SIGINT, bye)
    session.on_notice = lambda text: err_console.print(f"[dim]{text}[/dim]")
    session.run()
    if session.error:
        raise typer.Exit(1)
    err_console.print(f"[dim]{session.events_seen} event(s)[/dim]")
