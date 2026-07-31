"""CloudWatch Logs: groups, streams and events.

Same shape as `core/cloudcontrol.py` - generators plus `wrap_aws_errors`, so the
TUI can fill a screen page by page and the CLI can just exhaust the iterator. The
row/label types live in `core/logsmodel.py` (no boto3 there, so they are testable
on their own) and are re-exported here for convenience.

Live tail is deliberately *not* here: it is an HTTP/2 event stream with its own
cancellation problem and lives in `core/livetail.py`.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Any

from clitka.core.context import Context
from clitka.core.errors import wrap_aws_errors
from clitka.core.logsmodel import (
    LogEvent,
    LogGroup,
    LogStream,
    epoch_millis,
    moment,
    since,
)

__all__ = [
    "EVENT_PAGE",
    "PAGE",
    "LogEvent",
    "LogGroup",
    "LogStream",
    "epoch_millis",
    "get_log_group",
    "iter_events",
    "iter_log_groups",
    "iter_log_streams",
    "list_log_groups",
    "moment",
    "recent_events",
    "since",
]

PAGE = 50
EVENT_PAGE = 1000  # FilterLogEvents allows up to 10 000, but 1 000 paints sooner


def _client(ctx: Context) -> Any:
    return ctx.client("logs")


def _group_from(raw: dict[str, Any], name: str | None = None) -> LogGroup:
    return LogGroup(
        name=name or str(raw.get("logGroupName", "")),
        arn=str(raw.get("logGroupArn", "")),
        stored_bytes=int(raw.get("storedBytes", 0) or 0),
        retention_days=raw.get("retentionInDays"),
        created=moment(raw.get("creationTime")),
    )


# --- groups ---------------------------------------------------------------


def iter_log_groups(
    ctx: Context, prefix: str | None = None, page_size: int = PAGE
) -> Iterator[LogGroup]:
    """Yield log groups page by page, optionally filtered by name prefix."""
    client = _client(ctx)
    kwargs: dict[str, Any] = {"limit": page_size}
    if prefix:
        kwargs["logGroupNamePrefix"] = prefix
    token: str | None = None
    while True:
        if token:
            kwargs["nextToken"] = token
        page = _groups_page(ctx, client, kwargs)
        for raw in page.get("logGroups", []):
            yield _group_from(raw)
        token = page.get("nextToken")
        if not token:
            return


@wrap_aws_errors
def _groups_page(ctx: Context, client: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    return client.describe_log_groups(**kwargs)


def list_log_groups(
    ctx: Context, prefix: str | None = None, limit: int | None = None
) -> list[LogGroup]:
    """Eager variant for the CLI."""
    out: list[LogGroup] = []
    for group in iter_log_groups(ctx, prefix):
        out.append(group)
        if limit is not None and len(out) >= limit:
            break
    return out


@wrap_aws_errors
def get_log_group(ctx: Context, name: str) -> LogGroup:
    """One group by exact name. Raises LookupError if there is no such group."""
    page = _client(ctx).describe_log_groups(logGroupNamePrefix=name, limit=50)
    for raw in page.get("logGroups", []):
        if raw.get("logGroupName") == name:
            return _group_from(raw, name)
    raise LookupError(f"no log group named {name!r}")


# --- streams --------------------------------------------------------------


def iter_log_streams(
    ctx: Context, group: str, newest_first: bool = True, page_size: int = PAGE
) -> Iterator[LogStream]:
    """Yield the group's streams, most recently written first by default."""
    client = _client(ctx)
    kwargs: dict[str, Any] = {
        "logGroupName": group,
        "limit": page_size,
        "orderBy": "LastEventTime" if newest_first else "LogStreamName",
    }
    if newest_first:
        kwargs["descending"] = True
    token: str | None = None
    while True:
        if token:
            kwargs["nextToken"] = token
        page = _streams_page(ctx, client, kwargs)
        for raw in page.get("logStreams", []):
            yield LogStream(
                name=str(raw.get("logStreamName", "")),
                group=group,
                first_event=moment(raw.get("firstEventTimestamp")),
                last_event=moment(raw.get("lastEventTimestamp")),
                stored_bytes=int(raw.get("storedBytes", 0) or 0),
            )
        token = page.get("nextToken")
        if not token:
            return


@wrap_aws_errors
def _streams_page(ctx: Context, client: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    return client.describe_log_streams(**kwargs)


# --- events ---------------------------------------------------------------


def iter_events(
    ctx: Context,
    group: str,
    pattern: str | None = None,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
    streams: list[str] | None = None,
    page_size: int = EVENT_PAGE,
) -> Iterator[LogEvent]:
    """`FilterLogEvents`, page by page, oldest first (as the API returns them)."""
    client = _client(ctx)
    kwargs: dict[str, Any] = {"logGroupName": group, "limit": page_size}
    if pattern:
        kwargs["filterPattern"] = pattern
    if start is not None:
        kwargs["startTime"] = epoch_millis(start)
    if end is not None:
        kwargs["endTime"] = epoch_millis(end)
    if streams:
        kwargs["logStreamNames"] = streams
    token: str | None = None
    while True:
        if token:
            kwargs["nextToken"] = token
        page = _events_page(ctx, client, kwargs)
        for raw in page.get("events", []):
            yield LogEvent(
                timestamp=moment(raw.get("timestamp")),
                message=str(raw.get("message", "")),
                stream=str(raw.get("logStreamName", "")),
                group=group,
            )
        token = page.get("nextToken")
        if not token:
            return


@wrap_aws_errors
def _events_page(ctx: Context, client: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    return client.filter_log_events(**kwargs)


def recent_events(
    ctx: Context,
    group: str,
    minutes: float = 60.0,
    pattern: str | None = None,
    limit: int = 200,
) -> list[LogEvent]:
    """The most recent events, oldest first - what the preview tab shows.

    ponytail: `FilterLogEvents` only pages forward, so "the last N" means "the
    first `limit` inside the window". Ceiling: a very busy group shows the start
    of the window rather than the true tail. Upgrade path: walk the newest stream
    backwards with `GetLogEvents(startFromHead=False)`.
    """
    out: list[LogEvent] = []
    for event in iter_events(ctx, group, pattern=pattern, start=since(minutes)):
        out.append(event)
        if len(out) >= limit:
            break
    return out
