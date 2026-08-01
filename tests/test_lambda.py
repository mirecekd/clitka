"""Lambda: the model, the payload checks and the paginated API layer.

No network: the API layer goes through `botocore.stub.Stubber`, the same way
`test_logs.py` does.
"""

from __future__ import annotations

import base64
import io

import pytest
from botocore.stub import ANY, Stubber

from clitka.core import lambdafn
from clitka.core import lambdamodel as lm
from clitka.core import lambdapayload as lp
from clitka.core.context import Context
from clitka.core.errors import AwsError, ReadOnlyError

ARN = "arn:aws:lambda:eu-central-1:111122223333:function:my-fn"


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    return Context(region="eu-central-1")


def config(name: str = "my-fn", **extra):
    """A FunctionConfiguration as ListFunctions returns it."""
    raw = {
        "FunctionName": name,
        "FunctionArn": ARN.replace("my-fn", name),
        "Runtime": "python3.13",
        "Handler": "app.handler",
        "MemorySize": 512,
        "Timeout": 30,
        "CodeSize": 1024,
        "LastModified": "2026-08-01T06:00:00.000+0000",
    }
    raw.update(extra)
    return raw


# --- the self-checks ------------------------------------------------------


def test_the_self_checks_pass():
    lp._self_check()
    lm._self_check()
    lambdafn._self_check()


# --- the model ------------------------------------------------------------


def test_the_log_group_follows_from_the_name():
    """This is what lets the logs plugin show a function's events."""
    assert lm.Function("my-fn").log_group == "/aws/lambda/my-fn"


def test_the_region_comes_out_of_the_arn():
    assert lm.Function("my-fn", arn=ARN).region == "eu-central-1"
    # A Function built from a listing that omitted the ARN must not explode.
    assert lm.Function("my-fn").region == ""


def test_an_image_function_shows_its_package_type_as_the_runtime():
    # A container-image function has no Runtime at all, and an empty column in
    # the explorer looks like a bug.
    assert lm.Function("x", package_type="Image").row()["runtime"] == "Image"
    assert lm.Function("x", runtime="python3.13").row()["runtime"] == "python3.13"


def test_a_state_only_matters_when_aws_reported_one():
    # ListFunctions does not return State, so "" must not read as unhealthy.
    assert lm.Function("x").healthy
    assert lm.Function("x", state="Active").healthy
    assert not lm.Function("x", state="Failed").healthy


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("my-fn", "my-fn"),
        ("my-fn:1", "my-fn"),
        (ARN, "my-fn"),
        (f"{ARN}:PROD", "my-fn"),
    ],
)
def test_function_name_of(identifier, expected):
    assert lm.function_name_of(identifier) == expected


def test_moment_survives_lambdas_offset_format():
    # Lambda says +0000, not +00:00, and nonsense must not raise.
    when = lm.moment("2026-08-01T06:00:00.000+0000")
    assert when is not None and when.year == 2026
    assert lm.moment("nonsense") is None
    assert lm.moment(None) is None


# --- the invocation verdict ----------------------------------------------


def test_status_200_with_a_function_error_is_not_a_success():
    """The classic Lambda trap: the call worked, the handler did not."""
    raised = lm.Invocation(status=200, function_error="Unhandled", payload='{"errorType":"X"}')
    assert not raised.ok
    assert "Unhandled" in raised.summary()
    assert raised.data() == {"errorType": "X"}


def test_a_good_invocation_parses_its_payload():
    good = lm.Invocation(status=200, payload='{"ok": true}')
    assert good.ok and good.data() == {"ok": True}
    assert good.summary().startswith("[OK]")


def test_a_payload_that_is_not_json_comes_back_as_the_string_it_is():
    assert lm.Invocation(status=200, payload="hello").data() == "hello"
    assert lm.Invocation().data() is None


def test_an_async_invocation_says_where_the_result_went():
    accepted = lm.Invocation(status=202)
    assert accepted.async_accepted and accepted.ok
    assert "log group" in accepted.summary()


def test_the_log_tail_drops_the_blank_lines():
    assert lm.Invocation(status=200, log_tail="a\n\n b \n").log_lines() == ["a", " b "]


# --- the payload checks ---------------------------------------------------


def test_an_empty_payload_becomes_an_empty_object():
    # Lambda refuses an empty body; `{}` is what "no payload" means.
    assert lp.payload_bytes(None) == b"{}"
    assert lp.payload_bytes("") == b"{}"


def test_the_async_payload_limit_is_much_smaller():
    big = "x" * (lp.MAX_ASYNC_PAYLOAD + 1)
    assert "asynchronous" in lp.too_big(big, asynchronous=True)
    assert lp.too_big(big) == ""


def test_malformed_json_is_the_more_useful_complaint():
    both_wrong = "{" + "x" * lp.MAX_ASYNC_PAYLOAD
    assert "not valid JSON" in lp.complaint(both_wrong, asynchronous=True)


def test_an_undecodable_log_tail_loses_the_tail_not_the_call():
    assert lp.decode_log_tail(base64.b64encode(b"one\ntwo").decode()) == "one\ntwo"
    assert lp.decode_log_tail("not base64 at all!!") == ""


# --- the API layer --------------------------------------------------------


def test_iter_functions_follows_the_marker(ctx):
    client = ctx.client("lambda")
    with Stubber(client) as stub:
        stub.add_response(
            "list_functions",
            {"Functions": [config("one")], "NextMarker": "more"},
            {"MaxItems": lambdafn.PAGE},
        )
        stub.add_response(
            "list_functions",
            {"Functions": [config("two")]},
            {"MaxItems": lambdafn.PAGE, "Marker": "more"},
        )
        names = [fn.name for fn in lambdafn.iter_functions(ctx)]
    assert names == ["one", "two"]


def test_list_functions_stops_at_the_limit_without_asking_for_more(ctx):
    client = ctx.client("lambda")
    with Stubber(client) as stub:
        stub.add_response(
            "list_functions",
            {"Functions": [config("one"), config("two")], "NextMarker": "more"},
            {"MaxItems": lambdafn.PAGE},
        )
        found = lambdafn.list_functions(ctx, limit=1)
    assert [fn.name for fn in found] == ["one"]


def test_get_function_carries_the_environment(ctx):
    client = ctx.client("lambda")
    with Stubber(client) as stub:
        stub.add_response(
            "get_function",
            {"Configuration": config(Environment={"Variables": {"STAGE": "dev"}})},
            {"FunctionName": "my-fn"},
        )
        fn = lambdafn.get_function(ctx, "my-fn")
    assert fn.env == {"STAGE": "dev"}
    assert fn.handler == "app.handler" and fn.memory == 512


def test_an_environment_that_is_an_error_is_not_a_crash(ctx):
    # A function whose KMS key is gone returns Environment.Error, no Variables.
    client = ctx.client("lambda")
    with Stubber(client) as stub:
        stub.add_response(
            "get_function",
            {"Configuration": config(Environment={"Error": {"Message": "kms"}})},
            {"FunctionName": "my-fn"},
        )
        fn = lambdafn.get_function(ctx, "my-fn")
    assert fn.env == {}


def test_a_denied_listing_becomes_an_aws_error_with_a_hint(ctx):
    client = ctx.client("lambda")
    with Stubber(client) as stub:
        stub.add_client_error(
            "list_functions", service_error_code="AccessDeniedException", http_status_code=403
        )
        with pytest.raises(AwsError) as caught:
            list(lambdafn.iter_functions(ctx))
    assert caught.value.code == "AccessDeniedException"


def test_invoke_reads_the_payload_and_the_log_tail(ctx):
    client = ctx.client("lambda")
    with Stubber(client) as stub:
        stub.add_response(
            "invoke",
            {
                "StatusCode": 200,
                "Payload": io.BytesIO(b'{"ok": true}'),
                "LogResult": base64.b64encode(b"START\nEND").decode(),
                "ExecutedVersion": "$LATEST",
            },
            {
                "FunctionName": "my-fn",
                "InvocationType": "RequestResponse",
                "Payload": b'{"a": 1}',
                "LogType": "Tail",
            },
        )
        result = lambdafn.invoke(ctx, "my-fn", '{"a": 1}')
    assert result.ok and result.data() == {"ok": True}
    assert result.log_lines() == ["START", "END"]
    assert result.executed_version == "$LATEST"


def test_an_async_invoke_asks_for_no_log_tail(ctx):
    # LogType=Tail is meaningless for an Event invocation, and AWS rejects it.
    client = ctx.client("lambda")
    with Stubber(client) as stub:
        stub.add_response(
            "invoke",
            {"StatusCode": 202},
            {"FunctionName": "my-fn", "InvocationType": "Event", "Payload": ANY},
        )
        result = lambdafn.invoke(ctx, "my-fn", "{}", asynchronous=True)
    assert result.async_accepted


def test_a_handler_that_raised_is_reported_as_a_failure(ctx):
    client = ctx.client("lambda")
    with Stubber(client) as stub:
        stub.add_response(
            "invoke",
            {
                "StatusCode": 200,
                "FunctionError": "Unhandled",
                "Payload": io.BytesIO(b'{"errorType": "ValueError"}'),
            },
            {
                "FunctionName": "my-fn",
                "InvocationType": "RequestResponse",
                "Payload": b"{}",
                "LogType": "Tail",
            },
        )
        result = lambdafn.invoke(ctx, "my-fn")
    assert not result.ok
    assert result.data()["errorType"] == "ValueError"


def test_a_malformed_payload_never_reaches_aws(ctx):
    client = ctx.client("lambda")
    with Stubber(client) as stub:  # no response queued: a call would fail the test
        with pytest.raises(ValueError, match="not valid JSON"):
            lambdafn.invoke(ctx, "my-fn", "{oops")
        stub.assert_no_pending_responses()


def test_read_only_mode_refuses_to_invoke(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    read_only = Context(region="eu-central-1", read_only=True)
    with pytest.raises(ReadOnlyError, match="invoke my-fn"):
        lambdafn.invoke(read_only, ARN, "{}")


def test_list_aliases_flattens_what_matters(ctx):
    client = ctx.client("lambda")
    with Stubber(client) as stub:
        stub.add_response(
            "list_aliases",
            {"Aliases": [{"Name": "prod", "FunctionVersion": "3", "AliasArn": "a"}]},
            {"FunctionName": "my-fn", "MaxItems": 50},
        )
        aliases = lambdafn.list_aliases(ctx, "my-fn")
    assert aliases == [{"name": "prod", "version": "3", "description": ""}]
