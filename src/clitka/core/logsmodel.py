"""What CloudWatch Logs hands back, as things CLITKA can render.

No boto3 call in here, so the formatting and the timestamp arithmetic are testable
without a network or a stub - the same seam as `tablemodel.py` in the TUI.
`core/logs.py` is the API side, `core/livetail.py` the event stream.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LogGroup:
    """One log group, as the browser and the CLI show it."""

    name: str
    arn: str
    stored_bytes: int = 0
    retention_days: int | None = None
    created: dt.datetime | None = None

    @property
    def tail_arn(self) -> str:
        """The ARN `StartLiveTail` wants - without the trailing `:*`.

        `DescribeLogGroups` returns `...:log-group:/aws/lambda/x:*` and
        StartLiveTail rejects that form. Verified against sw-sandbox.
        """
        return self.arn[:-2] if self.arn.endswith(":*") else self.arn

    def row(self) -> dict[str, Any]:
        return {
            "identifier": self.name,
            "retention": "never" if self.retention_days is None else f"{self.retention_days}d",
            "stored": human_size(self.stored_bytes),
            "arn": self.arn,
        }


@dataclass(frozen=True)
class LogStream:
    """One log stream inside a group."""

    name: str
    group: str
    first_event: dt.datetime | None = None
    last_event: dt.datetime | None = None
    stored_bytes: int = 0

    def row(self) -> dict[str, Any]:
        return {
            "identifier": self.name,
            "last_event": stamp(self.last_event),
            "first_event": stamp(self.first_event),
            "stored": human_size(self.stored_bytes),
        }


@dataclass(frozen=True)
class LogEvent:
    """One log line, wherever it came from - a query, a tail, a stream."""

    timestamp: dt.datetime | None
    message: str
    stream: str = ""
    group: str = ""

    def line(self, show_stream: bool = False) -> str:
        """One rendered line: time, optionally the stream, then the message."""
        parts = [stamp(self.timestamp) or "-"]
        if show_stream and self.stream:
            parts.append(self.stream)
        parts.append(self.message.rstrip("\n"))
        return "  ".join(parts)


def human_size(size: int) -> str:
    """Bytes as something readable. ponytail: powers of 1024, no localisation."""
    value = float(size)
    for unit in ("B", "K", "M", "G", "T"):
        if value < 1024 or unit == "T":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}T"


def stamp(when: dt.datetime | None) -> str:
    return "" if when is None else when.strftime("%Y-%m-%d %H:%M:%S")


def moment(millis: Any) -> dt.datetime | None:
    """CloudWatch speaks epoch milliseconds; everything else here is aware UTC."""
    if not isinstance(millis, int | float) or millis <= 0:
        return None
    return dt.datetime.fromtimestamp(millis / 1000, dt.UTC)


def epoch_millis(when: dt.datetime) -> int:
    """A datetime as CloudWatch's epoch milliseconds; naive input is taken as UTC."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.UTC)
    return int(when.timestamp() * 1000)


def since(minutes: float) -> dt.datetime:
    """ "The last N minutes" as a start time."""
    return dt.datetime.now(dt.UTC) - dt.timedelta(minutes=minutes)


def group_name_of(identifier: str) -> str:
    """Turn a log group ARN back into its name; a plain name passes through."""
    marker = ":log-group:"
    if marker in identifier:
        return identifier.split(marker, 1)[1].removesuffix(":*")
    return identifier


def _self_check() -> None:
    group = LogGroup(
        "/aws/lambda/x",
        "arn:aws:logs:eu-central-1:1:log-group:/aws/lambda/x:*",
        stored_bytes=2048,
        retention_days=30,
    )
    assert group.tail_arn.endswith("/aws/lambda/x") and not group.tail_arn.endswith(":*")
    assert group.row()["retention"] == "30d"
    assert LogGroup("n", "a").row()["retention"] == "never"
    assert human_size(999) == "999B" and human_size(2048) == "2.0K"

    when = moment(1767225600000)
    assert when is not None and epoch_millis(when) == 1767225600000
    assert moment(None) is None and moment(0) is None
    assert LogEvent(when, "boom\n").line().endswith("boom")
    assert group_name_of("arn:aws:logs:eu-west-1:1:log-group:/a/b:*") == "/a/b"
    print("[OK] logs model self-check passed")


if __name__ == "__main__":
    _self_check()
