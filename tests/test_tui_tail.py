"""The live tail screen: the buffer, pause/wrap/save, and the `t` key on the tree.

No AWS: `LiveTail` is replaced with a fake that delivers canned batches.
"""

from __future__ import annotations

import datetime as dt
from collections import deque
from typing import ClassVar

import pytest

from clitka.core import cloudcontrol as cc
from clitka.core import logs as lg
from clitka.core.context import Context, Identity
from clitka.core.logsmodel import LogEvent, LogGroup
from clitka.tui import tailmodel as tm
from clitka.tui import tailscreen as ts
from clitka.tui.app import ClitkaApp
from clitka.tui.restree import ResourceTree

WHEN = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
GROUP = "/aws/lambda/x"
ARN = f"arn:aws:logs:eu-central-1:1:log-group:{GROUP}:*"
TYPE = "AWS::Logs::LogGroup"


def events(*messages: str) -> list[LogEvent]:
    return [LogEvent(WHEN, message, stream="s1", group=GROUP) for message in messages]


# --- the buffer -----------------------------------------------------------


def test_a_pause_keeps_collecting():
    """A pause stops the scroll, not the session - the events are still kept."""
    buffer = tm.TailBuffer()
    buffer.add(events("one"))
    assert buffer.toggle_pause() is True
    buffer.add(events("two", "three"))
    assert len(buffer.events) == 3
    assert buffer.since_pause == 2
    assert "PAUSED" in buffer.status() and "2 arrived" in buffer.status()
    assert buffer.toggle_pause() is False
    assert buffer.since_pause == 0
    assert "PAUSED" not in buffer.status()


def test_the_ring_buffer_counts_what_it_drops():
    buffer = tm.TailBuffer()
    buffer.events = deque(maxlen=3)
    buffer.add(events(*[str(index) for index in range(10)]))
    assert len(buffer.events) == 3
    assert buffer.dropped == 7
    assert buffer.received == 10
    assert "7 dropped" in buffer.status()
    # The newest lines are the ones kept.
    assert buffer.lines()[-1].endswith("9")


def test_wrap_shows_in_the_status():
    buffer = tm.TailBuffer()
    assert "wrap on" in buffer.status()
    assert buffer.toggle_wrap() is False
    assert "wrap off" in buffer.status()


def test_the_stream_name_is_optional():
    buffer = tm.TailBuffer()
    buffer.add(events("one"))
    assert "s1" not in buffer.text()
    buffer.show_stream = True
    assert "s1" in buffer.text()


def test_a_json_line_is_escaped_before_display():
    buffer = tm.TailBuffer()
    buffer.add(events('{"tags": [1, 2]}'))
    assert "\\[1, 2]" in buffer.text()


def test_clear_forgets_the_lines_but_not_the_total():
    buffer = tm.TailBuffer()
    buffer.add(events("one", "two"))
    buffer.clear()
    assert buffer.lines() == []
    assert buffer.received == 2, "the session total is still the truth"


def test_save_writes_the_buffer(tmp_path):
    buffer = tm.TailBuffer()
    buffer.add(events("one", "two"))
    path = tmp_path / "out.log"
    assert buffer.save(path) == 2
    written = path.read_text(encoding="utf-8").splitlines()
    assert len(written) == 2
    # The file gets the raw line, not the markup-escaped one.
    assert written[0].endswith("one")


def test_save_of_an_empty_buffer_makes_an_empty_file(tmp_path):
    path = tmp_path / "out.log"
    assert tm.TailBuffer().save(path) == 0
    assert path.read_text(encoding="utf-8") == ""


def test_the_default_filename_names_the_group():
    path = tm.default_path([GROUP], WHEN)
    assert path.name == "clitka-tail-aws-lambda-x-20260101-000000.log"


# --- the screen -----------------------------------------------------------


class FakeTail:
    """Stands in for LiveTail: delivers one batch, records being stopped."""

    instances: ClassVar[list[FakeTail]] = []

    def __init__(self, _ctx, arns, pattern=None, on_events=None, on_notice=None):
        self.arns = arns
        self.pattern = pattern
        self.on_events = on_events
        self.on_notice = on_notice
        self.stopped = False
        self.error = ""
        FakeTail.instances.append(self)

    def run(self):
        if self.on_notice:
            self.on_notice("live tail started")
        if self.on_events:
            self.on_events(events("hello", "world"))

    def stop(self):
        self.stopped = True


@pytest.fixture(autouse=True)
def fake_tail(monkeypatch):
    FakeTail.instances.clear()
    monkeypatch.setattr(ts, "LiveTail", FakeTail)
    return FakeTail


@pytest.fixture
def offline(monkeypatch):
    ident = Identity(account="123456789012", arn="arn:aws:iam::1:user/mirek", user_id="A")
    monkeypatch.setattr(Context, "identity_or_none", lambda _self: ident)
    return Context(profile="demo", region="eu-central-1")


@pytest.mark.asyncio
async def test_the_screen_shows_what_arrives(offline):
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        app.push_screen(ts.TailScreen(offline, [GROUP], [ARN[:-2]]))
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ts.TailScreen)
        assert screen.buffer.received == 2
        assert "hello" in screen.buffer.text()
        assert GROUP in screen.heading()
        assert FakeTail.instances[0].arns == [ARN[:-2]]


@pytest.mark.asyncio
async def test_space_pauses_and_resumes(offline):
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        screen = ts.TailScreen(offline, [GROUP], [ARN[:-2]])
        app.push_screen(screen)
        await pilot.pause()
        await app.workers.wait_for_complete()

        await pilot.press("space")
        await pilot.pause()
        assert screen.buffer.paused is True
        assert "PAUSED" in screen.heading()

        await pilot.press("space")
        await pilot.pause()
        assert screen.buffer.paused is False


@pytest.mark.asyncio
async def test_w_toggles_wrap_and_c_clears(offline):
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        screen = ts.TailScreen(offline, [GROUP], [ARN[:-2]])
        app.push_screen(screen)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        await pilot.press("w")
        await pilot.pause()
        assert screen.buffer.wrap is False
        assert "wrap off" in screen.heading()

        await pilot.press("c")
        await pilot.pause()
        assert screen.buffer.lines() == []


@pytest.mark.asyncio
async def test_s_saves_and_says_where(offline, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        screen = ts.TailScreen(offline, [GROUP], [ARN[:-2]])
        app.push_screen(screen)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        await pilot.press("s")
        await pilot.pause()
        assert "saved 2 line(s)" in screen.note
        written = list(tmp_path.glob("clitka-tail-*.log"))
        assert len(written) == 1
        assert "hello" in written[0].read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_escape_stops_the_session_and_goes_back(offline):
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        app.push_screen(ts.TailScreen(offline, [GROUP], [ARN[:-2]]))
        await pilot.pause()
        await app.workers.wait_for_complete()

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ts.TailScreen)
        assert FakeTail.instances[0].stopped, "the stream must be closed"


@pytest.mark.asyncio
async def test_too_many_groups_is_reported_not_raised(offline, monkeypatch):
    def refuse(*_a, **_k):
        raise ValueError("StartLiveTail accepts at most 10 log groups")

    monkeypatch.setattr(ts, "LiveTail", refuse)
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        screen = ts.TailScreen(offline, ["/a"] * 11, ["arn"] * 11)
        app.push_screen(screen)
        await pilot.pause()
        assert "[ERROR]" in screen.note
        assert "at most 10" in screen.note


# --- the `t` key on the tree ---------------------------------------------


@pytest.mark.asyncio
async def test_t_on_a_log_group_opens_the_tail(offline, monkeypatch):
    group = cc.Resource(TYPE, GROUP, {"LogGroupName": GROUP})
    monkeypatch.setattr(cc, "iter_resources", lambda *_a, **_k: iter([group]))
    monkeypatch.setattr(lg, "get_log_group", lambda _ctx, name: LogGroup(name, ARN))

    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        app.push_screen(ResourceTree(offline, [TYPE]))
        await pilot.pause()
        await pilot.press("enter")  # expand the type
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("down")  # onto the group
        await pilot.press("t")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert isinstance(app.screen, ts.TailScreen)
        # The ':*' suffix must be gone - StartLiveTail rejects it.
        assert app.screen.group_arns == [ARN[:-2]]


@pytest.mark.asyncio
async def test_t_on_something_else_does_nothing(offline, monkeypatch):
    bucket = cc.Resource("AWS::S3::Bucket", "b1", {})
    monkeypatch.setattr(cc, "iter_resources", lambda *_a, **_k: iter([bucket]))
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        app.push_screen(ResourceTree(offline, ["AWS::S3::Bucket"]))
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("t")
        await pilot.pause()
        assert isinstance(app.screen, ResourceTree)


@pytest.mark.asyncio
async def test_a_group_that_cannot_be_resolved_shows_the_error(offline, monkeypatch):
    group = cc.Resource(TYPE, GROUP, {})
    monkeypatch.setattr(cc, "iter_resources", lambda *_a, **_k: iter([group]))

    def denied(_ctx, _name):
        raise RuntimeError("AccessDenied: logs:DescribeLogGroups")

    monkeypatch.setattr(lg, "get_log_group", denied)
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        app.push_screen(ResourceTree(offline, [TYPE]))
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("t")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert not isinstance(app.screen, ts.TailScreen)
        assert "AccessDenied" in app.screen.result.body


def test_the_tail_help_documents_the_limits():
    assert "5000" in tm.TAIL_HELP
    assert "three hours" in tm.TAIL_HELP
    assert "ten groups" in tm.TAIL_HELP
    assert "clitka logs tail" in tm.TAIL_HELP
