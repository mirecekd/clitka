"""The `logs` plugin: CLI commands, F9 actions and the Events preview tab.

Nothing here touches AWS - `core.logs` is monkeypatched, which is the point of
having it as a separate layer.
"""

from __future__ import annotations

import datetime as dt

import pytest
from typer.testing import CliRunner

from clitka.core import actions as act
from clitka.core import logsmodel as lm
from clitka.core import plugins
from clitka.core import preview as pv
from clitka.core.context import Context
from clitka.services import logs as plugin
from clitka.services.logs import actions as la
from clitka.services.logs import cli as lc

runner = CliRunner()
WHEN = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
GROUP = "/aws/lambda/x"
ARN = f"arn:aws:logs:eu-central-1:1:log-group:{GROUP}:*"

GROUPS = [
    lm.LogGroup(GROUP, ARN, stored_bytes=2048, retention_days=30),
    lm.LogGroup("/other", "arn:other"),
]
EVENTS = [
    lm.LogEvent(WHEN, '{"level": "INFO", "tags": [1, 2]}', stream="s1", group=GROUP),
    lm.LogEvent(WHEN, "plain line", stream="s1", group=GROUP),
]
STREAMS = [lm.LogStream("s1", GROUP, last_event=WHEN, stored_bytes=10)]


@pytest.fixture
def offline(monkeypatch):
    """Every AWS call the plugin can make, answered from the fixtures above."""
    monkeypatch.setattr(la.lg, "recent_events", lambda *_a, **_k: list(EVENTS))
    monkeypatch.setattr(la.lg, "iter_log_streams", lambda *_a, **_k: iter(STREAMS))
    monkeypatch.setattr(lc.lg, "list_log_groups", lambda *_a, **_k: list(GROUPS))
    monkeypatch.setattr(lc.lg, "recent_events", lambda *_a, **_k: list(EVENTS))
    monkeypatch.setattr(lc.lg, "iter_log_streams", lambda *_a, **_k: iter(STREAMS))
    monkeypatch.setattr(lc.lg, "get_log_group", lambda _ctx, name: GROUPS[0])
    return Context(profile="demo", region="eu-central-1")


def ref(type_name: str = la.TYPE_NAME, identifier: str = GROUP) -> act.ResourceRef:
    return act.ResourceRef.from_row(type_name, {"identifier": identifier})


# --- the plugin is registered --------------------------------------------


def test_the_logs_plugin_is_a_builtin():
    assert "clitka.services.logs" in plugins.BUILTIN_SERVICES
    assert plugin.clitka_service_name() == "logs"
    assert plugin.clitka_cli_app() is lc.app


def test_the_plugin_publishes_its_actions_and_previews():
    assert [a.id for a in plugin.clitka_actions()] == [a.id for a in la.ACTIONS]
    assert [t.id for t in plugin.clitka_previews()] == ["logs.events"]


def test_the_registry_hands_the_tab_to_the_pane():
    """The tree never imports the logs plugin - it goes through the hook."""
    tabs = pv.registered()
    assert any(tab.id == "logs.events" for tab in tabs), [tab.id for tab in tabs]
    offered = pv.available(tabs, ref())
    assert [tab.id for tab in offered] == ["logs.events"]
    assert pv.available(tabs, ref("AWS::S3::Bucket", "b")) == []


def test_the_registry_hands_the_actions_to_f9():
    ids = [action.id for action in act.available(act.registered(), ref())]
    assert {"logs.events", "logs.streams", "logs.tail"} <= set(ids), ids
    # The generic Cloud Control actions still apply to a log group as well.
    assert "resources.view_yaml" in ids
    assert "resources.delete" in ids
    assert len(set(ids)) == len(ids), "two plugins must not publish the same id"


# --- the actions ----------------------------------------------------------


def test_group_name_falls_back_to_the_property():
    assert la.group_name(ref()) == GROUP
    assert la.group_name(act.ResourceRef(la.TYPE_NAME, "", {"LogGroupName": "/a"})) == "/a"
    assert la.group_name(act.ResourceRef(la.TYPE_NAME, "", {})) == ""


def test_a_json_log_line_is_not_read_as_markup(offline):
    result = la.show_events(offline, ref())
    assert "\\[1, 2]" in result.body, result.body
    assert "2 event(s)" in result.body


def test_events_action_says_so_when_there_is_nothing(offline, monkeypatch):
    monkeypatch.setattr(la.lg, "recent_events", lambda *_a, **_k: [])
    body = la.show_events(offline, ref()).body
    assert "nothing in the last" in body


def test_streams_action_lists_the_newest_first(offline):
    body = la.show_streams(offline, ref()).body
    assert "s1" in body
    assert "2026-01-01" in body


def test_streams_action_survives_an_empty_group(offline, monkeypatch):
    monkeypatch.setattr(la.lg, "iter_log_streams", lambda *_a, **_k: iter([]))
    assert "no streams" in la.show_streams(offline, ref()).body


def test_the_tail_hint_names_the_profile(offline):
    body = la.show_tail_hint(offline, ref()).body
    assert f"clitka -p demo logs tail {GROUP}" in body
    assert "-p " not in la.show_tail_hint(Context(), ref()).body


def test_no_logs_action_is_destructive():
    assert not any(action.destructive for action in la.ACTIONS)


def test_the_events_tab_is_lazy_and_type_scoped():
    tab = la.PREVIEWS[0]
    assert tab.lazy is True, "it calls FilterLogEvents, so not on the UI thread"
    assert tab.matches_type(la.TYPE_NAME)
    assert not tab.matches_type("AWS::S3::Bucket")


def test_the_events_tab_renders_the_log(offline):
    text = la.build_events_tab(offline, ref())
    assert "plain line" in text
    assert "\\[1, 2]" in text


# --- the CLI --------------------------------------------------------------


def invoke(args, ctx):
    return runner.invoke(lc.app, args, obj={"context": ctx})


def test_logs_groups_renders_a_table(offline):
    result = invoke(["groups", "-o", "table"], offline)
    assert result.exit_code == 0, result.output
    assert GROUP in result.output
    assert "2.0K" in result.output
    assert "never" in result.output  # /other has no retention


def test_logs_groups_json_is_machine_readable(offline):
    result = invoke(["groups", "-o", "json"], offline)
    assert result.exit_code == 0
    assert '"identifier"' in result.output


def test_logs_streams_stops_at_the_limit(offline, monkeypatch):
    many = [lm.LogStream(f"s{index}", GROUP, last_event=WHEN) for index in range(10)]
    monkeypatch.setattr(lc.lg, "iter_log_streams", lambda *_a, **_k: iter(many))
    result = invoke(["streams", GROUP, "-n", "3", "-o", "json"], offline)
    assert result.exit_code == 0
    assert result.output.count('"identifier"') == 3


def test_logs_search_prints_the_lines_verbatim_on_a_tty(offline):
    result = invoke(["search", GROUP, "-o", "table"], offline)
    assert result.exit_code == 0
    assert "plain line" in result.output
    # The raw JSON must survive, brackets and all.
    assert '"tags"' in result.output


def test_logs_search_json_keeps_the_timestamp(offline):
    result = invoke(["search", GROUP, "-o", "json"], offline)
    assert result.exit_code == 0
    assert "2026-01-01T00:00:00" in result.output


def test_logs_tail_reports_a_failed_session(offline, monkeypatch):
    """A live tail that cannot start must exit 1, not traceback."""

    class Dead:
        error = "AccessDenied"
        events_seen = 0
        on_notice = None

        def __init__(self, *_a, **_k):
            pass

        def run(self):
            return None

        def stop(self):
            return None

    monkeypatch.setattr(lc, "LiveTail", Dead)
    result = invoke(["tail", GROUP], offline)
    assert result.exit_code == 1


def test_logs_tail_prints_what_the_session_delivers(offline, monkeypatch):
    delivered: list[str] = []

    class Fake:
        events_seen = 2
        error = ""

        def __init__(self, _ctx, arns, pattern=None, on_events=None):
            self.arns = arns
            self.pattern = pattern
            self.on_events = on_events
            self.on_notice = None

        def run(self):
            assert self.on_events is not None
            self.on_events(list(EVENTS))

        def stop(self):
            return None

    def capture(_ctx, arns, pattern=None, on_events=None):
        session = Fake(_ctx, arns, pattern, on_events)
        delivered.append(arns[0])
        return session

    monkeypatch.setattr(lc, "LiveTail", capture)
    result = invoke(["tail", GROUP, "-f", "ERROR"], offline)
    assert result.exit_code == 0, result.output
    # StartLiveTail needs the ARN without the ':*' DescribeLogGroups adds.
    assert delivered == [ARN[:-2]]
    assert "plain line" in result.output


def test_logs_tail_refuses_more_than_ten_groups(offline):
    result = invoke(["tail", *[f"/g{index}" for index in range(11)]], offline)
    assert result.exit_code == 1
    assert "at most 10" in result.output
