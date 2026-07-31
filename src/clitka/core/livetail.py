"""`logs.StartLiveTail` - the one event-stream API in CLITKA.

Everything in here is what the throwaway PoC (2026-07-31, against sw-sandbox)
actually established, not what the docs imply:

- `start_live_tail()` returns `{"responseStream": <EventStream>}`. Iterating it
  blocks; each item is a dict with exactly one of `sessionStart`,
  `sessionUpdate` or an exception member. A `sessionUpdate` carries
  `sessionResults`, a list of `{timestamp, message, logStreamName, ...}` - and it
  arrives roughly once a second even when there is nothing to say, so an idle tail
  looks alive.
- **Setting a flag does not stop it.** The iterator is blocked inside a socket
  read, so it only notices after the next event. What stops it promptly is
  `stream.close()` from another thread - measured at 0.37 s.
- Closing it that way makes the blocked iterator raise
  `AttributeError("'NoneType' object has no attribute 'read'")`, because botocore
  drops the raw stream underneath it. That is the *normal* end of a cancelled
  tail, so it is swallowed rather than reported.
- Bad or expired credentials fail in `start_live_tail()` itself, before any
  stream exists, as a normal `ClientError` - so `wrap_aws_errors` catches it and
  the TUI can offer a login.
- Twelve concurrent sessions on one account were accepted without throttling.
  The documented ceiling is 10 log groups per session and ~3 h per session.
- No thread or socket leak: after a session the fd count returned to its start.

ponytail: one session per `LiveTail` object, no automatic restart when AWS ends
it after ~3 h. Ceiling: a very long tail stops with a message. Upgrade path: watch
for the stream ending cleanly and start a new session.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from clitka.core.context import Context
from clitka.core.errors import wrap_aws_errors
from clitka.core.logsmodel import LogEvent, group_name_of, moment

MAX_GROUPS = 10  # a hard AWS limit on StartLiveTail
# What a cancelled stream raises from inside the blocked read. Matched by type
# *and* by the stopping flag, never by message text.
_TEARDOWN = (AttributeError, ValueError, OSError)

Sink = Callable[[list[LogEvent]], None]
Notice = Callable[[str], None]


@dataclass
class LiveTail:
    """One live tail session, driven from a worker thread.

    `run()` blocks until the session ends or `stop()` is called, handing every
    batch of events to `on_events` and every status change to `on_notice`. Both
    callbacks are invoked on the *worker* thread, so a TUI caller must marshal
    them (`app.call_from_thread`).
    """

    context: Context
    group_arns: list[str]
    pattern: str | None = None
    on_events: Sink | None = None
    on_notice: Notice | None = None

    started: bool = False
    stopped: bool = False
    events_seen: int = 0
    updates_seen: int = 0
    error: str = ""

    _stopping: threading.Event = field(default_factory=threading.Event, repr=False)
    _stream: Any = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        if not self.group_arns:
            raise ValueError("a live tail needs at least one log group")
        if len(self.group_arns) > MAX_GROUPS:
            raise ValueError(f"StartLiveTail accepts at most {MAX_GROUPS} log groups")

    # --- the public contract ---------------------------------------------

    def run(self) -> None:
        """Pump the stream until it ends. Call this on a worker thread."""
        try:
            stream = self._open()
        except Exception as exc:
            # Credentials and permissions fail here, before any stream exists.
            self.error = str(exc)
            self._say(f"[ERROR] {exc}")
            self.stopped = True
            return
        with self._lock:
            self._stream = stream
        self._say("live tail started")
        try:
            for item in stream:
                if self._stopping.is_set():
                    break
                self._handle(item)
        except _TEARDOWN as exc:
            if not self._stopping.is_set():
                self.error = f"{type(exc).__name__}: {exc}"
                self._say(f"[ERROR] {self.error}")
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self._say(f"[ERROR] {self.error}")
        finally:
            self._shut()
        self.stopped = True
        self._say("live tail stopped")

    def stop(self) -> None:
        """Ask the session to end - safe from any thread, returns immediately.

        Closing the stream is what unblocks the reader; the flag alone would only
        take effect after the next event. Measured at 0.37 s in the PoC.
        """
        self._stopping.set()
        self._shut()

    @property
    def stopping(self) -> bool:
        return self._stopping.is_set()

    # --- internals --------------------------------------------------------

    @wrap_aws_errors
    def _open(self) -> Any:
        kwargs: dict[str, Any] = {"logGroupIdentifiers": self.group_arns}
        if self.pattern:
            kwargs["logEventFilterPattern"] = self.pattern
        response = self.context.client("logs").start_live_tail(**kwargs)
        return response["responseStream"]

    def _shut(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.close()
        except Exception:
            return  # already gone - nothing to do about it

    def _handle(self, item: dict[str, Any]) -> None:
        if "sessionStart" in item:
            self.started = True
            return
        update = item.get("sessionUpdate")
        if update is None:
            # Every other member is an exception shape (SessionTimeoutException,
            # SessionStreamingException). Surface it rather than looping on it.
            self.error = f"live tail ended: {', '.join(item)}"
            self._say(f"[ERROR] {self.error}")
            self._stopping.set()
            return
        self.updates_seen += 1
        events = [event_from(raw) for raw in update.get("sessionResults", [])]
        if not events:
            return  # an idle keep-alive; the session is fine
        self.events_seen += len(events)
        if self.on_events is not None:
            self.on_events(events)

    def _say(self, text: str) -> None:
        if self.on_notice is not None:
            self.on_notice(text)


def event_from(raw: dict[str, Any]) -> LogEvent:
    """One `sessionResults` entry as a `LogEvent`."""
    return LogEvent(
        timestamp=moment(raw.get("timestamp")),
        message=str(raw.get("message", "")),
        stream=str(raw.get("logStreamName", "")),
        group=group_name_of(str(raw.get("logGroupIdentifier", ""))),
    )


def _self_check() -> None:
    ctx = Context(region="eu-central-1")

    # The AWS limits are refused up front, not discovered at call time.
    for bad in ([], [f"arn{index}" for index in range(MAX_GROUPS + 1)]):
        try:
            LiveTail(ctx, list(bad))
        except ValueError:
            continue
        raise AssertionError(f"{len(bad)} log groups should be refused")

    seen: list[LogEvent] = []
    tail = LiveTail(ctx, ["arn:aws:logs:eu-central-1:1:log-group:/x"])
    tail.on_events = seen.extend
    tail._handle({"sessionStart": {"sessionId": "s"}})
    # An update with no results is an idle keep-alive, not an event.
    tail._handle({"sessionUpdate": {"sessionResults": []}})
    assert tail.started and not seen and tail.updates_seen == 1

    # Anything that is neither a start nor an update ends the session, loudly.
    tail._handle({"sessionTimeoutException": {"message": "3h is up"}})
    assert tail.stopping and "sessionTimeoutException" in tail.error

    # stop() must be safe with no stream open at all.
    LiveTail(ctx, ["arn:x"]).stop()
    print("[OK] live tail self-check passed")


if __name__ == "__main__":
    _self_check()
