"""The `lambda` plugin: the hooks, the F9 actions, the preview tabs and the CLI.

The interesting assertion is the one about the plugin seam: registering this
package changed exactly one line outside it (`plugins.BUILTIN_SERVICES`), and the
proof is that `clitka lambda` appears in the CLI and the actions reach the F9
menu without either importing the plugin.
"""

from __future__ import annotations

import base64
import io

import pytest
from botocore.stub import ANY, Stubber
from typer.testing import CliRunner

from clitka.cli.main import app as root_app
from clitka.core import plugins
from clitka.core.actions import ResourceRef
from clitka.core.context import Context
from clitka.services.lambdafn import actions as la

runner = CliRunner()
ARN = "arn:aws:lambda:eu-central-1:111122223333:function:my-fn"


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    return Context(region="eu-central-1")


@pytest.fixture
def ref():
    return ResourceRef.from_row(la.TYPE_NAME, {"identifier": "my-fn"})


def configuration(**extra):
    raw = {
        "FunctionName": "my-fn",
        "FunctionArn": ARN,
        "Runtime": "python3.13",
        "Handler": "app.handler",
        "MemorySize": 512,
        "Timeout": 30,
        "Role": "arn:aws:iam::1:role/fn",
        "Version": "$LATEST",
        "Architectures": ["arm64"],
        "LastModified": "2026-08-01T06:00:00.000+0000",
    }
    raw.update(extra)
    return raw


def test_the_self_check_passes():
    la._self_check()


# --- the plugin seam ------------------------------------------------------


def test_the_plugin_publishes_its_cli_group_as_lambda():
    # The package is `lambdafn` (lambda is a keyword) but the verb is `lambda`.
    names = dict(plugins.service_apps())
    assert "lambda" in names
    assert "lambdafn" not in names


def test_the_actions_and_previews_reach_the_registry():
    ids = {action.id for action in plugins.actions()}
    assert {"lambda.config", "lambda.env", "lambda.invoke"} <= ids
    tabs = {tab.id for tab in plugins.previews()}
    assert {"lambda.config", "lambda.logs"} <= tabs


def test_the_lambda_group_is_in_the_root_help():
    result = runner.invoke(root_app, ["--help"])
    assert result.exit_code == 0
    assert "lambda" in result.stdout


def test_no_action_or_tab_id_collides_across_plugins():
    ids = [action.id for action in plugins.actions()]
    assert len(set(ids)) == len(ids), ids
    tabs = [tab.id for tab in plugins.previews()]
    assert len(set(tabs)) == len(tabs), tabs


def test_the_actions_only_offer_themselves_on_a_function():
    bucket = ResourceRef.from_row("AWS::S3::Bucket", {"identifier": "b"})
    assert not any(action.applies_to(bucket) for action in la.ACTIONS)


# --- the F9 actions -------------------------------------------------------


def test_the_config_action_shows_what_matters(ctx, ref):
    client = ctx.client("lambda")
    with Stubber(client) as stub:
        stub.add_response(
            "get_function", {"Configuration": configuration()}, {"FunctionName": "my-fn"}
        )
        result = la.show_config(ctx, ref)
    assert result.title == "my-fn - configuration"
    for expected in ("python3.13", "app.handler", "512 MB", "30 s", "arm64"):
        assert expected in result.body
    # The log group is spelled out - it is how the user finds the events.
    assert "/aws/lambda/my-fn" in result.body


def test_an_unhealthy_function_says_so_first(ctx, ref):
    client = ctx.client("lambda")
    with Stubber(client) as stub:
        stub.add_response(
            "get_function",
            {"Configuration": configuration(State="Failed", StateReason="bad image")},
            {"FunctionName": "my-fn"},
        )
        result = la.show_config(ctx, ref)
    assert result.body.splitlines()[0].endswith("Failed - bad image")


def test_the_env_action_sorts_and_survives_an_empty_environment(ctx, ref):
    client = ctx.client("lambda")
    with Stubber(client) as stub:
        stub.add_response(
            "get_function",
            {"Configuration": configuration(Environment={"Variables": {"B": "2", "A": "1"}})},
            {"FunctionName": "my-fn"},
        )
        result = la.show_env(ctx, ref)
    assert result.body.index("A") < result.body.index("B")

    with Stubber(client) as stub:
        stub.add_response(
            "get_function", {"Configuration": configuration()}, {"FunctionName": "my-fn"}
        )
        empty = la.show_env(ctx, ref)
    assert "no environment variables" in empty.body


def test_the_invoke_action_hands_over_a_command_it_does_not_run(ctx, ref):
    # No stub is armed: if this called AWS the test would fail.
    result = la.show_invoke_hint(ctx, ref)
    assert "lambda invoke my-fn" in result.body
    assert "--async" in result.body


# --- the preview tabs -----------------------------------------------------


def test_the_logs_tab_reads_the_functions_own_log_group(ctx, ref):
    client = ctx.client("logs")
    with Stubber(client) as stub:
        stub.add_response(
            "filter_log_events",
            {"events": [{"timestamp": 1767225600000, "message": "hello"}]},
            {"logGroupName": "/aws/lambda/my-fn", "limit": ANY, "startTime": ANY},
        )
        body = la.build_logs_tab(ctx, ref)
    assert "hello" in body
    assert "/aws/lambda/my-fn" in body


def test_a_function_that_never_ran_has_no_log_group_and_that_is_fine(ctx, ref):
    client = ctx.client("logs")
    with Stubber(client) as stub:
        stub.add_client_error(
            "filter_log_events",
            service_error_code="ResourceNotFoundException",
            http_status_code=400,
        )
        body = la.build_logs_tab(ctx, ref)
    assert "no log group yet" in body


def test_the_logs_tab_does_not_swallow_a_real_failure(ctx, ref):
    client = ctx.client("logs")
    with Stubber(client) as stub:
        stub.add_client_error(
            "filter_log_events",
            service_error_code="AccessDeniedException",
            http_status_code=403,
        )
        with pytest.raises(Exception, match="AccessDenied"):
            la.build_logs_tab(ctx, ref)


# --- the CLI --------------------------------------------------------------


def cli(monkeypatch, ctx, *args):
    """Run `clitka lambda ...` against a Context whose clients are stubbed."""
    monkeypatch.setattr("clitka.core.context.Context.from_env", staticmethod(lambda **_: ctx))
    return runner.invoke(root_app, ["lambda", *args])


def test_list_prints_a_table(monkeypatch, ctx):
    client = ctx.client("lambda")
    with Stubber(client) as stub:
        stub.add_response("list_functions", {"Functions": [configuration()]}, {"MaxItems": ANY})
        result = cli(monkeypatch, ctx, "list")
    assert result.exit_code == 0
    assert "my-fn" in result.stdout


def test_invoke_exits_non_zero_when_the_handler_raised(monkeypatch, ctx):
    """AWS answers 200; the exit code is the reason this command exists."""
    client = ctx.client("lambda")
    with Stubber(client) as stub:
        stub.add_response(
            "invoke",
            {
                "StatusCode": 200,
                "FunctionError": "Unhandled",
                "Payload": io.BytesIO(b'{"errorType": "ValueError"}'),
                "LogResult": base64.b64encode(b"Traceback").decode(),
            },
            {
                "FunctionName": "my-fn",
                "InvocationType": "RequestResponse",
                "Payload": b"{}",
                "LogType": "Tail",
            },
        )
        result = cli(monkeypatch, ctx, "invoke", "my-fn")
    assert result.exit_code == 1
    assert "ValueError" in result.stdout


def test_invoke_exits_zero_on_a_good_call(monkeypatch, ctx):
    client = ctx.client("lambda")
    with Stubber(client) as stub:
        stub.add_response(
            "invoke",
            {"StatusCode": 200, "Payload": io.BytesIO(b'{"ok": true}')},
            {
                "FunctionName": "my-fn",
                "InvocationType": "RequestResponse",
                "Payload": b'{"a": 1}',
                "LogType": "Tail",
            },
        )
        result = cli(monkeypatch, ctx, "invoke", "my-fn", "--payload", '{"a": 1}')
    assert result.exit_code == 0
    assert '{"ok": true}' in result.stdout


def test_invoke_refuses_both_payload_flags(monkeypatch, ctx, tmp_path):
    event = tmp_path / "event.json"
    event.write_text("{}", encoding="utf-8")
    result = cli(
        monkeypatch, ctx, "invoke", "my-fn", "--payload", "{}", "--payload-file", str(event)
    )
    assert result.exit_code == 1


def test_invoke_reads_a_payload_file(monkeypatch, ctx, tmp_path):
    event = tmp_path / "event.json"
    event.write_text('{"from": "file"}', encoding="utf-8")
    client = ctx.client("lambda")
    with Stubber(client) as stub:
        stub.add_response(
            "invoke",
            {"StatusCode": 200, "Payload": io.BytesIO(b"null")},
            {
                "FunctionName": "my-fn",
                "InvocationType": "RequestResponse",
                "Payload": b'{"from": "file"}',
                "LogType": "Tail",
            },
        )
        result = cli(monkeypatch, ctx, "invoke", "my-fn", "--payload-file", str(event))
    assert result.exit_code == 0


def test_invoke_complains_about_a_missing_payload_file(monkeypatch, ctx, tmp_path):
    result = cli(monkeypatch, ctx, "invoke", "my-fn", "--payload-file", str(tmp_path / "nope.json"))
    assert result.exit_code != 0
