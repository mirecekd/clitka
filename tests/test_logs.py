"""CloudWatch Logs: the model, the paginated API layer, and the live tail pump.

No network: the API layer goes through `botocore.stub.Stubber` and the live tail
is fed hand-made event-stream items - exactly the shapes the throwaway PoC saw
against sw-sandbox on 2026-07-31.
"""

from __future__ import annotations

import datetime as dt

import pytest
from botocore.stub import Stubber

from clitka.core import livetail as lt
from clitka.core import logs
from clitka.core import logsmodel as lm
from clitka.core.context import Context
from clitka.core.errors import AwsError

ARN = "arn:aws:logs:eu-central-1:111122223333:log-group:/aws/lambda/x:*"
WHEN = 1767225600000  # 2026-01-01T00:00:00Z


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    return Context(region="eu-central-1")


# --- the model ------------------------------------------------------------


def test_tail_arn_drops_the_star_suffix():
    """StartLiveTail rejects the ':*' form DescribeLogGroups hands back."""
    group = lm.LogGroup("/aws/lambda/x", ARN)
    assert group.tail_arn == ARN[:-2]
    assert lm.LogGroup("n", "arn:plain").tail_arn == "arn:plain"


def test_group_row_says_never_for_unlimited_retention():
    assert lm.LogGroup("n", "a").row()["retention"] == "never"
    assert lm.LogGroup("n", "a", retention_days=7).row()["retention"] == "7d"


@pytest.mark.parametrize(
    ("size", "expected"),
    [(0, "0B"), (1023, "1023B"), (1024, "1.0K"), (1536, "1.5K"), (5 * 1024**3, "5.0G")],
)
def test_human_size(size, expected):
    assert lm.human_size(size) == expected


def test_moment_and_epoch_millis_round_trip():
    when = lm.moment(WHEN)
    assert when is not None
    assert when.tzinfo is dt.UTC
    assert lm.epoch_millis(when) == WHEN
    # A naive datetime is taken as UTC rather than local time.
    assert lm.epoch_millis(when.replace(tzinfo=None)) == WHEN


def test_moment_rejects_nonsense():
    for bad in (None, 0, -1, "", "1767225600000"):
        assert lm.moment(bad) is None


def test_event_line_shows_the_stream_only_when_asked():
    event = lm.LogEvent(lm.moment(WHEN), "boom\n", stream="2026/01/01/[$LATEST]a")
    assert event.line() == "2026-01-01 00:00:00  boom"
    assert "$LATEST" in event.line(show_stream=True)


def test_event_line_survives_a_missing_timestamp():
    assert lm.LogEvent(None, "x").line().startswith("-")


def test_group_name_of():
    assert lm.group_name_of(ARN) == "/aws/lambda/x"
    assert lm.group_name_of("/plain") == "/plain"


def test_since_is_in_the_past():
    delta = dt.datetime.now(dt.UTC) - lm.since(30)
    assert 1790 < delta.total_seconds() < 1810


# --- the API layer --------------------------------------------------------


def test_iter_log_groups_follows_the_next_token(ctx):
    client = ctx.client("logs")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_log_groups",
            {
                "logGroups": [
                    {
                        "logGroupName": "/a",
                        "logGroupArn": ARN,
                        "storedBytes": 2048,
                        "retentionInDays": 30,
                        "creationTime": WHEN,
                    }
                ],
                "nextToken": "more",
            },
            {"limit": logs.PAGE},
        )
        stub.add_response(
            "describe_log_groups",
            {"logGroups": [{"logGroupName": "/b", "logGroupArn": "arn:b"}]},
            {"limit": logs.PAGE, "nextToken": "more"},
        )
        found = list(logs.iter_log_groups(ctx))
        stub.assert_no_pending_responses()

    assert [group.name for group in found] == ["/a", "/b"]
    assert found[0].stored_bytes == 2048
    assert found[0].retention_days == 30
    assert found[0].created is not None
    assert found[1].retention_days is None


def test_list_log_groups_stops_at_the_limit(ctx):
    client = ctx.client("logs")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_log_groups",
            {
                "logGroups": [
                    {"logGroupName": f"/g{index}", "logGroupArn": "a"} for index in range(5)
                ],
                "nextToken": "more",
            },
            {"limit": logs.PAGE},
        )
        found = logs.list_log_groups(ctx, limit=2)
    # The second page is never requested, so the stub has no pending response.
    assert [group.name for group in found] == ["/g0", "/g1"]


def test_list_log_groups_passes_the_prefix(ctx):
    client = ctx.client("logs")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_log_groups",
            {"logGroups": []},
            {"limit": logs.PAGE, "logGroupNamePrefix": "/aws/lambda"},
        )
        assert logs.list_log_groups(ctx, prefix="/aws/lambda") == []
        stub.assert_no_pending_responses()


def test_get_log_group_matches_the_exact_name(ctx):
    client = ctx.client("logs")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_log_groups",
            {
                "logGroups": [
                    {"logGroupName": "/aws/lambda/xyz", "logGroupArn": "arn:other"},
                    {"logGroupName": "/aws/lambda/x", "logGroupArn": ARN},
                ]
            },
            {"logGroupNamePrefix": "/aws/lambda/x", "limit": 50},
        )
        group = logs.get_log_group(ctx, "/aws/lambda/x")
    assert group.arn == ARN


def test_get_log_group_raises_when_nothing_matches(ctx):
    client = ctx.client("logs")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_log_groups",
            {"logGroups": [{"logGroupName": "/other", "logGroupArn": "a"}]},
            {"logGroupNamePrefix": "/nope", "limit": 50},
        )
        with pytest.raises(LookupError):
            logs.get_log_group(ctx, "/nope")


def test_iter_log_streams_asks_for_the_newest_first(ctx):
    client = ctx.client("logs")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_log_streams",
            {
                "logStreams": [
                    {
                        "logStreamName": "s1",
                        "firstEventTimestamp": WHEN,
                        "lastEventTimestamp": WHEN + 1000,
                        "storedBytes": 10,
                    }
                ]
            },
            {
                "logGroupName": "/a",
                "limit": logs.PAGE,
                "orderBy": "LastEventTime",
                "descending": True,
            },
        )
        found = list(logs.iter_log_streams(ctx, "/a"))
        stub.assert_no_pending_responses()
    assert found[0].name == "s1"
    assert found[0].group == "/a"
    assert found[0].row()["last_event"] == "2026-01-01 00:00:01"


def test_iter_log_streams_can_order_by_name(ctx):
    client = ctx.client("logs")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_log_streams",
            {"logStreams": []},
            {"logGroupName": "/a", "limit": logs.PAGE, "orderBy": "LogStreamName"},
        )
        assert list(logs.iter_log_streams(ctx, "/a", newest_first=False)) == []
        stub.assert_no_pending_responses()


def test_iter_events_passes_the_window_and_the_pattern(ctx):
    client = ctx.client("logs")
    start = lm.moment(WHEN)
    end = lm.moment(WHEN + 60_000)
    assert start is not None and end is not None
    with Stubber(client) as stub:
        stub.add_response(
            "filter_log_events",
            {
                "events": [
                    {"timestamp": WHEN, "message": "one", "logStreamName": "s1"},
                    {"timestamp": WHEN + 5, "message": "two", "logStreamName": "s1"},
                ],
                "nextToken": "more",
            },
            {
                "logGroupName": "/a",
                "limit": logs.EVENT_PAGE,
                "filterPattern": "ERROR",
                "startTime": WHEN,
                "endTime": WHEN + 60_000,
                "logStreamNames": ["s1"],
            },
        )
        stub.add_response(
            "filter_log_events",
            {"events": [{"timestamp": WHEN + 9, "message": "three"}]},
            {
                "logGroupName": "/a",
                "limit": logs.EVENT_PAGE,
                "filterPattern": "ERROR",
                "startTime": WHEN,
                "endTime": WHEN + 60_000,
                "logStreamNames": ["s1"],
                "nextToken": "more",
            },
        )
        found = list(
            logs.iter_events(ctx, "/a", pattern="ERROR", start=start, end=end, streams=["s1"])
        )
        stub.assert_no_pending_responses()
    assert [event.message for event in found] == ["one", "two", "three"]
    assert all(event.group == "/a" for event in found)


def test_recent_events_stops_at_the_limit(ctx):
    client = ctx.client("logs")
    with Stubber(client) as stub:
        stub.add_response(
            "filter_log_events",
            {
                "events": [
                    {"timestamp": WHEN + index, "message": str(index)} for index in range(5)
                ],
                "nextToken": "more",
            },
            None,  # the start time moves with the clock
        )
        found = logs.recent_events(ctx, "/a", minutes=5, limit=3)
    assert [event.message for event in found] == ["0", "1", "2"]


def test_an_access_denied_becomes_an_aws_error(ctx):
    client = ctx.client("logs")
    with Stubber(client) as stub:
        stub.add_client_error(
            "describe_log_groups", service_error_code="AccessDeniedException", http_status_code=400
        )
        with pytest.raises(AwsError) as excinfo:
            list(logs.iter_log_groups(ctx))
    assert excinfo.value.code == "AccessDeniedException"
    assert "IAM permission" in str(excinfo.value)


# --- the live tail pump ---------------------------------------------------
#
# The item shapes below are what the throwaway PoC actually received from
# StartLiveTail on sw-sandbox; a fake stream replays them.


def update(*messages: str, group: str = ARN) -> dict:
    return {
        "sessionUpdate": {
            "sessionResults": [
                {
                    "timestamp": WHEN + index,
                    "message": message,
                    "logStreamName": "s1",
                    "logGroupIdentifier": group,
                }
                for index, message in enumerate(messages)
            ]
        }
    }


class FakeStream:
    """An event stream that yields canned items and records being closed."""

    def __init__(self, items):
        self.items = list(items)
        self.closed = False
        self.kwargs: dict = {}

    def __iter__(self):
        for item in self.items:
            if self.closed:
                # This is what botocore does: the raw stream is gone underneath.
                raise AttributeError("'NoneType' object has no attribute 'read'")
            yield item

    def close(self):
        self.closed = True


@pytest.fixture
def tail_ctx(ctx, monkeypatch):
    """A Context whose logs client returns a FakeStream we control."""
    streams: list[FakeStream] = []

    def make(items):
        def start_live_tail(**kwargs):
            stream = FakeStream(items)
            stream.kwargs = kwargs
            streams.append(stream)
            return {"responseStream": stream}

        client = ctx.client("logs")
        monkeypatch.setattr(client, "start_live_tail", start_live_tail, raising=False)
        return ctx

    make.streams = streams
    return make


def test_a_live_tail_needs_between_one_and_ten_groups(ctx):
    with pytest.raises(ValueError, match="at least one"):
        lt.LiveTail(ctx, [])
    with pytest.raises(ValueError, match="at most 10"):
        lt.LiveTail(ctx, [f"arn{index}" for index in range(lt.MAX_GROUPS + 1)])
    assert lt.LiveTail(ctx, ["arn"] * lt.MAX_GROUPS).group_arns


def test_the_pump_delivers_events_and_ends_cleanly(tail_ctx):
    ctx = tail_ctx([{"sessionStart": {"sessionId": "s"}}, update("one", "two")])
    seen: list[lm.LogEvent] = []
    notices: list[str] = []
    tail = lt.LiveTail(ctx, [ARN[:-2]], on_events=seen.extend, on_notice=notices.append)
    tail.run()

    assert tail.started and tail.stopped and not tail.error
    assert [event.message for event in seen] == ["one", "two"]
    # The ARN is turned back into a plain group name for display.
    assert seen[0].group == "/aws/lambda/x"
    assert notices[0] == "live tail started"
    assert notices[-1] == "live tail stopped"


def test_an_idle_keep_alive_is_not_an_event(tail_ctx):
    ctx = tail_ctx([{"sessionUpdate": {"sessionResults": []}}])
    seen: list[lm.LogEvent] = []
    tail = lt.LiveTail(ctx, ["arn"], on_events=seen.extend)
    tail.run()
    assert tail.updates_seen == 1
    assert tail.events_seen == 0
    assert seen == []


def test_the_filter_pattern_is_passed_to_aws(tail_ctx):
    ctx = tail_ctx([])
    tail = lt.LiveTail(ctx, ["arn"], pattern="ERROR")
    tail.run()
    assert tail_ctx.streams[0].kwargs["logEventFilterPattern"] == "ERROR"
    assert tail_ctx.streams[0].kwargs["logGroupIdentifiers"] == ["arn"]


def test_stopping_closes_the_stream_and_swallows_the_teardown(tail_ctx):
    """Closing the stream is the only thing that unblocks the reader."""
    ctx = tail_ctx([update("one"), update("two"), update("three")])
    seen: list[lm.LogEvent] = []

    tail = lt.LiveTail(ctx, ["arn"])

    def stop_after_first(events):
        seen.extend(events)
        tail.stop()

    tail.on_events = stop_after_first
    tail.run()

    assert [event.message for event in seen] == ["one"]
    assert tail_ctx.streams[0].closed
    # The AttributeError botocore raises from the closed stream is expected.
    assert tail.error == "", tail.error
    assert tail.stopped


def test_a_teardown_error_without_a_stop_is_reported(tail_ctx):
    """The same exception must NOT be swallowed if nobody asked to stop."""

    class Exploding(FakeStream):
        def __iter__(self):
            raise AttributeError("'NoneType' object has no attribute 'read'")

    stream = Exploding([])
    ctx = tail_ctx([])
    ctx.client("logs").start_live_tail = lambda **_kw: {"responseStream": stream}
    tail = lt.LiveTail(ctx, ["arn"])
    tail.run()
    assert "AttributeError" in tail.error


def test_a_session_exception_item_ends_the_tail(tail_ctx):
    ctx = tail_ctx([{"sessionTimeoutException": {"message": "3h is up"}}, update("never")])
    seen: list[lm.LogEvent] = []
    tail = lt.LiveTail(ctx, ["arn"], on_events=seen.extend)
    tail.run()
    assert "sessionTimeoutException" in tail.error
    assert seen == [], "nothing may be delivered after the session ended"


def test_a_failed_start_is_reported_not_raised(ctx, monkeypatch):
    def boom(**_kwargs):
        from botocore.exceptions import ClientError

        raise ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "no"}, "ResponseMetadata": {}},
            "StartLiveTail",
        )

    monkeypatch.setattr(ctx.client("logs"), "start_live_tail", boom, raising=False)
    notices: list[str] = []
    tail = lt.LiveTail(ctx, ["arn"], on_notice=notices.append)
    tail.run()  # must not raise: the screen shows the message instead
    assert "AccessDenied" in tail.error
    assert tail.stopped and not tail.started
    assert any("[ERROR]" in note for note in notices)


def test_stop_is_safe_before_anything_started(ctx):
    tail = lt.LiveTail(ctx, ["arn"])
    tail.stop()
    assert tail.stopping


def test_event_from_handles_a_bare_group_name():
    event = lt.event_from({"timestamp": WHEN, "message": "x", "logGroupIdentifier": "/plain"})
    assert event.group == "/plain"
    assert event.timestamp is not None
