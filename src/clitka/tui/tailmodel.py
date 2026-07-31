"""The live tail's buffer: what is kept, what is shown, what is written out.

No Textual import, so pausing, wrapping and the ring buffer are testable without a
screen - the same seam as `tablemodel.py`. `tui/tailscreen.py` is the widget.
"""

from __future__ import annotations

import datetime as dt
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from clitka.core.logsmodel import LogEvent

MAX_LINES = 5000
"""ponytail: a fixed ring buffer rather than paging to disk. Ceiling: a very busy
group loses its oldest lines. Upgrade path: spill to a temp file and let `save`
copy it."""

KEY_LINE = "space pause   w wrap   s save   c clear   escape stop and go back"

TAIL_HELP = """\
The log group's events as they happen (CloudWatch StartLiveTail).

  space    pause / resume - a pause keeps collecting, it only stops the scroll
  w        wrap long lines on or off
  s        save what is buffered to a file in the current directory
  c        clear the buffer
  escape   stop the session and go back
  F10      quit

The buffer keeps the last 5000 lines and says so when it starts dropping. AWS ends
a live tail session after about three hours, and at most ten groups can be followed
at once. `clitka logs tail <group>` does the same thing in a plain shell.
"""


@dataclass
class TailBuffer:
    """Holds the tail. `paused` decides whether new events reach the display.

    Events that arrive while paused are still *kept* (that is the point of a
    pause, not a stop), they simply do not move the view until it resumes.
    """

    show_stream: bool = False
    wrap: bool = True
    paused: bool = False
    dropped: int = 0
    received: int = 0
    events: deque[LogEvent] = field(default_factory=lambda: deque(maxlen=MAX_LINES))
    since_pause: int = 0

    def add(self, events: list[LogEvent]) -> None:
        """Take a batch from the pump. Safe to call while paused."""
        for event in events:
            if len(self.events) == self.events.maxlen:
                self.dropped += 1
            self.events.append(event)
        self.received += len(events)
        if self.paused:
            self.since_pause += len(events)

    def toggle_pause(self) -> bool:
        """Flip the pause. Returns the new state."""
        self.paused = not self.paused
        if not self.paused:
            self.since_pause = 0
        return self.paused

    def toggle_wrap(self) -> bool:
        self.wrap = not self.wrap
        return self.wrap

    def clear(self) -> None:
        self.events.clear()
        self.dropped = 0
        self.since_pause = 0

    # --- rendering --------------------------------------------------------

    def lines(self) -> list[str]:
        """Every kept event as a display line, oldest first."""
        return [event.line(show_stream=self.show_stream) for event in self.events]

    def text(self) -> str:
        """The whole buffer, escaped so a JSON payload is not read as markup."""
        return "\n".join(escape(line) for line in self.lines())

    def status(self) -> str:
        """The one-line summary above the log."""
        parts = [f"{self.received} event(s)"]
        if self.dropped:
            parts.append(f"{self.dropped} dropped (buffer is {MAX_LINES})")
        parts.append("wrap on" if self.wrap else "wrap off")
        if self.paused:
            held = f", {self.since_pause} arrived" if self.since_pause else ""
            parts.append(f"PAUSED{held}")
        return "  |  ".join(parts)

    # --- saving -----------------------------------------------------------

    def save(self, path: Path) -> int:
        """Write the buffer out as plain text. Returns the number of lines."""
        lines = self.lines()
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return len(lines)


def escape(text: str) -> str:
    """Rich markup is off-limits for log content - a payload is full of brackets."""
    return text.replace("[", "\\[")


def default_path(groups: list[str], when: dt.datetime | None = None) -> Path:
    """Where "save" writes when the user does not say: cwd, named after the group."""
    stamp = (when or dt.datetime.now()).strftime("%Y%m%d-%H%M%S")
    first = (groups[0] if groups else "logs").strip("/").replace("/", "-")
    return Path.cwd() / f"clitka-tail-{first}-{stamp}.log"


def _self_check() -> None:
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    buffer = TailBuffer()
    buffer.add([LogEvent(now, "one", stream="s1"), LogEvent(now, "two", stream="s1")])
    assert buffer.received == 2
    assert buffer.lines() == ["2026-01-01 00:00:00  one", "2026-01-01 00:00:00  two"]
    assert "s1" not in buffer.text()
    buffer.show_stream = True
    assert "s1" in buffer.text()

    # A pause keeps collecting - it only stops the view from moving.
    assert buffer.toggle_pause() is True
    buffer.add([LogEvent(now, "three")])
    assert buffer.since_pause == 1
    assert "PAUSED" in buffer.status() and "1 arrived" in buffer.status()
    assert len(buffer.events) == 3, "a pause must not drop events"
    assert buffer.toggle_pause() is False
    assert buffer.since_pause == 0

    assert buffer.toggle_wrap() is False
    assert "wrap off" in buffer.status()

    # The ring buffer counts what it threw away.
    small = TailBuffer()
    small.events = deque(maxlen=2)
    small.add([LogEvent(now, str(index)) for index in range(5)])
    assert len(small.events) == 2 and small.dropped == 3
    assert "3 dropped" in small.status()

    assert escape('{"a": [1]}') == '{"a": \\[1]}'
    path = default_path(["/aws/lambda/x"], now)
    assert path.name == "clitka-tail-aws-lambda-x-20260101-000000.log"
    assert default_path([], now).name.startswith("clitka-tail-logs-")

    buffer.clear()
    assert buffer.lines() == [] and buffer.text() == ""
    print("[OK] tail buffer self-check passed")


if __name__ == "__main__":
    _self_check()
