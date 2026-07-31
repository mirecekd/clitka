"""The live tail screen: a log group's events arriving as they happen.

Everything about *how* StartLiveTail behaves is in `core/livetail.py`, and the
buffer/pause/wrap/save logic is in `tui/tailmodel.py`; this file is the keyboard
and the paint. Two facts from the PoC shape it:

- The pump blocks in a socket read, so it runs on a thread worker and hands every
  batch over with `call_from_thread`. Escape calls `LiveTail.stop()`, which closes
  the stream - that is the only thing that unblocks the reader (0.37 s measured).
- Leaving the screen must always stop the session. `on_unmount` does it too, not
  just the escape binding, so `F10` or an exception cannot leave a socket open.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Static

from clitka.core.context import Context
from clitka.core.livetail import LiveTail
from clitka.core.logsmodel import LogEvent
from clitka.tui.keybar import KeyBar
from clitka.tui.status import StatusBar
from clitka.tui.tailmodel import KEY_LINE, TAIL_HELP, TailBuffer, default_path


class TailScreen(Screen[None]):
    """Follow up to ten log groups live."""

    DEFAULT_CSS = """
    TailScreen #tail-title {
        height: auto;
        padding: 0 1;
        color: $text-muted;
    }
    TailScreen #tail-body {
        height: 1fr;
        padding: 0 1;
    }
    """
    BINDINGS = [
        Binding("escape", "back", "Stop", show=False),
        Binding("space", "pause", "Pause", show=False),
        Binding("w", "wrap", "Wrap", show=False),
        Binding("s", "save", "Save", show=False),
        Binding("c", "clear", "Clear", show=False),
        Binding("f1", "help", "Help", show=False),
        Binding("f10", "quit", "Quit", show=False),
    ]

    def __init__(
        self,
        context: Context,
        groups: list[str],
        group_arns: list[str],
        pattern: str | None = None,
    ) -> None:
        super().__init__()
        self.context = context
        self.groups = groups
        self.group_arns = group_arns
        self.pattern = pattern
        self.buffer = TailBuffer()
        self.tail: LiveTail | None = None
        self.note = "starting..."

    # --- layout -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield KeyBar()
        yield Static(self.heading(), id="tail-title")
        with VerticalScroll(id="tail-body"):
            yield Static("", id="tail-text")
        yield StatusBar(self.context)

    def heading(self) -> str:
        where = ", ".join(self.groups)
        filtered = f"  filter: {self.pattern}" if self.pattern else ""
        return f"{where}{filtered}\n{self.buffer.status()}  |  {self.note}\n{KEY_LINE}"

    def on_mount(self) -> None:
        self.query_one("#tail-body", VerticalScroll).focus()
        self.start()

    def on_unmount(self) -> None:
        """Leaving by any route must close the stream, not just escape."""
        self.stop()

    # --- the session ------------------------------------------------------

    def start(self) -> None:
        try:
            self.tail = LiveTail(
                self.context,
                self.group_arns,
                pattern=self.pattern,
                on_events=self._events_from_thread,
                on_notice=self._notice_from_thread,
            )
        except ValueError as exc:  # more than ten groups, or none
            self._note(f"[ERROR] {exc}")
            return
        self.run_worker(self.tail.run, thread=True, exclusive=False, group="livetail")

    def stop(self) -> None:
        tail, self.tail = self.tail, None
        if tail is not None:
            tail.stop()

    # Both callbacks run on the worker thread, so they only marshal.
    def _events_from_thread(self, events: list[LogEvent]) -> None:
        self.app.call_from_thread(self._events, events)

    def _notice_from_thread(self, text: str) -> None:
        self.app.call_from_thread(self._note, text)

    def _events(self, events: list[LogEvent]) -> None:
        self.buffer.add(events)
        if self.buffer.paused:
            self._refresh_heading()  # the count still moves while paused
        else:
            self._repaint()

    def _note(self, text: str) -> None:
        self.note = text
        self._refresh_heading()

    # --- painting ---------------------------------------------------------

    def _refresh_heading(self) -> None:
        try:
            self.query_one("#tail-title", Static).update(self.heading())
        except Exception:
            return

    def _repaint(self) -> None:
        self._refresh_heading()
        try:
            body = self.query_one("#tail-text", Static)
            scroll = self.query_one("#tail-body", VerticalScroll)
        except Exception:
            return
        # ponytail: the whole buffer is re-rendered on every batch. Ceiling: a
        # busy group at 5000 lines re-paints a lot of text every second. Upgrade
        # path: a RichLog widget, which appends instead.
        body.update(self.buffer.text())
        body.styles.width = "100%" if self.buffer.wrap else "auto"
        scroll.scroll_end(animate=False)

    # --- keys -------------------------------------------------------------

    def action_pause(self) -> None:
        if self.buffer.toggle_pause():
            self._refresh_heading()
        else:
            self._repaint()  # resuming must show what arrived meanwhile

    def action_wrap(self) -> None:
        self.buffer.toggle_wrap()
        self._repaint()

    def action_clear(self) -> None:
        self.buffer.clear()
        self._repaint()

    def action_save(self) -> None:
        path = default_path(self.groups)
        try:
            count = self.buffer.save(path)
        except OSError as exc:
            self._note(f"[ERROR] cannot write {path}: {exc}")
            return
        self._note(f"saved {count} line(s) to {path}")

    def action_back(self) -> None:
        self.stop()
        self.app.pop_screen()

    def action_help(self) -> None:
        from clitka.tui.dropdown import TextDrop

        self.app.push_screen(TextDrop("F1  Help - live tail", TAIL_HELP, "f1"))


def open_tail(app, context: Context, group_names: list[str], pattern: str | None = None) -> None:
    """Resolve the groups' ARNs on a worker, then push the screen.

    `StartLiveTail` wants ARNs, and the ':*' suffix DescribeLogGroups returns is
    rejected - `LogGroup.tail_arn` is what handles that.
    """
    from clitka.core import logs as lg

    def go() -> None:
        try:
            arns = [lg.get_log_group(context, name).tail_arn for name in group_names]
        except Exception as exc:
            app.call_from_thread(app.push_screen, failed_screen(context, group_names, str(exc)))
            return
        app.call_from_thread(app.push_screen, TailScreen(context, group_names, arns, pattern))

    app.run_worker(go, thread=True, exclusive=False, group="tail-open")


def failed_screen(context: Context, groups: list[str], message: str):
    """A tail that could not even be opened still owes the user the reason."""
    from clitka.core.actions import ActionResult
    from clitka.tui.resultview import ResultScreen

    return ResultScreen(
        context,
        ActionResult(f"live tail: {', '.join(groups)}", f"[red][ERROR] {message}[/red]"),
    )


def _self_check() -> None:
    """The screen needs a running app, so check what does not: the heading."""
    screen = TailScreen(Context(region="eu-central-1"), ["/a", "/b"], ["arn:a", "arn:b"])
    head = screen.heading()
    assert "/a, /b" in head and "0 event(s)" in head and KEY_LINE in head
    screen.pattern = "ERROR"
    assert "filter: ERROR" in screen.heading()
    # stop() before anything has started must be harmless.
    screen.stop()
    assert isinstance(default_path(["/a"]), Path)
    print("[OK] tail screen self-check passed")


if __name__ == "__main__":
    _self_check()
