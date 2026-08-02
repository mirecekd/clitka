"""`core/ssm*.py` - Parameter Store, the documents, and the SecureString rule.

The thing worth guarding above everything else in this service: **a value that was
not explicitly asked to be decrypted must never reach a screen.** Several tests
below assert that by looking for the ciphertext, not just for the mask - a bug
that showed the blob instead of the plaintext would still be a leak of sorts, and
would certainly be confusing.
"""

from __future__ import annotations

import datetime as dt

import pytest
from botocore.stub import ANY, Stubber

from clitka.core import ssm, ssmdoc, ssmmodel, ssmparam, ssmrun, ssmrunbook
from clitka.core import ssmcommand as sc
from clitka.core.context import Context
from clitka.core.errors import ClitkaError, ReadOnlyError

MODIFIED = dt.datetime(2026, 8, 1, 9, 30, tzinfo=dt.UTC)
CIPHER = "AQICAHgcAAAA"

# A real command id, because `Stubber` validates the shape: `CommandId` has a
# minimum length of 36 and a made-up "c-1" is rejected before the call. Same
# family as Cloud Control's TypeName minimum of 10.
COMMAND_ID = "12345678-1234-1234-1234-123456789012"


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    return Context(region="eu-central-1")


def meta(name: str = "/app/prod/url", type_name: str = "String", version: int = 1) -> dict:
    """A `DescribeParameters` entry - note it carries no Value, ever."""
    return {
        "Name": name,
        "Type": type_name,
        "Version": version,
        "LastModifiedDate": MODIFIED,
        "Tier": "Standard",
    }


def valued(
    name: str = "/app/prod/url", value: str = "https://x", type_name: str = "String"
) -> dict:

    return {"Name": name, "Type": type_name, "Value": value, "Version": 1}


def test_the_self_checks_pass():
    ssmmodel._self_check()
    ssmrunbook._self_check()
    sc._self_check()
    ssmparam._self_check()
    ssmdoc._self_check()
    ssmrun._self_check()
    ssm._self_check()


# --- the SecureString rule ------------------------------------------------


def test_a_secure_string_is_masked_unless_the_call_asked_to_decrypt(ctx):
    with Stubber(ctx.client("ssm")) as stub:
        stub.add_response(
            "get_parameter",
            {"Parameter": valued("/db/pw", CIPHER, "SecureString")},
            {"Name": "/db/pw", "WithDecryption": False},
        )
        one = ssm.get_parameter(ctx, "/db/pw")
    assert one.secret and not one.decrypted
    assert one.display_value() == ssm.MASK
    # Not the ciphertext either - it is unreadable *and* looks worth copying.
    assert CIPHER not in one.display_value()
    assert CIPHER not in one.row()["value"]


def test_asking_to_decrypt_sends_with_decryption_and_shows_the_value(ctx):
    with Stubber(ctx.client("ssm")) as stub:
        stub.add_response(
            "get_parameter",
            {"Parameter": valued("/db/pw", "hunter2", "SecureString")},
            {"Name": "/db/pw", "WithDecryption": True},
        )
        one = ssm.get_parameter(ctx, "/db/pw", decrypt=True)
    assert one.decrypted and one.display_value() == "hunter2"


def test_a_listing_cannot_leak_a_value_because_the_api_returns_none(ctx):
    # DescribeParameters is metadata-only whatever the type, which is why the
    # listing is safe to run in front of someone.
    with Stubber(ctx.client("ssm")) as stub:
        stub.add_response(
            "describe_parameters",
            {"Parameters": [meta("/db/pw", "SecureString"), meta()]},
            {"MaxResults": ANY},
        )
        found = ssm.list_parameters(ctx)
    secret = next(one for one in found if one.secret)
    assert not secret.has_value and secret.display_value() == ssm.MASK
    assert all(not one.decrypted for one in found)


def test_the_history_listing_never_decrypts(ctx):
    # A browse cannot decrypt - the request must not even offer WithDecryption.
    with Stubber(ctx.client("ssm")) as stub:
        stub.add_response(
            "get_parameter_history",
            {
                "Parameters": [
                    {"Name": "/db/pw", "Type": "SecureString", "Value": CIPHER, "Version": 1},
                    {"Name": "/db/pw", "Type": "SecureString", "Value": CIPHER, "Version": 3},
                ]
            },
            {"Name": "/db/pw", "MaxResults": ANY},
        )
        found = ssm.history(ctx, "/db/pw")
    # Newest first, and nothing readable.
    assert [one.version for one in found] == [3, 1]
    assert all(one.display_value() == ssm.MASK for one in found)
    assert all(CIPHER not in one.display_value() for one in found)


def test_by_path_only_decrypts_when_told_to(ctx):
    for decrypt in (False, True):
        with Stubber(ctx.client("ssm")) as stub:
            stub.add_response(
                "get_parameters_by_path",
                {"Parameters": [valued("/app/prod/pw", "hunter2", "SecureString")]},
                {
                    "Path": "/app/prod",
                    "Recursive": True,
                    "WithDecryption": decrypt,
                    "MaxResults": ANY,
                },
            )
            found = ssm.by_path(ctx, "/app/prod", decrypt=decrypt)
        shown = found[0].display_value()
        assert (shown == "hunter2") if decrypt else (shown == ssm.MASK)


def test_a_path_without_a_leading_slash_is_fixed_up(ctx):
    # AWS matches nothing for a path without one, and does not say so.
    with Stubber(ctx.client("ssm")) as stub:
        stub.add_response(
            "get_parameters_by_path",
            {"Parameters": []},
            {"Path": "/app/prod", "Recursive": True, "WithDecryption": False, "MaxResults": ANY},
        )
        ssm.by_path(ctx, "app/prod")
        stub.assert_no_pending_responses()


# --- the parameter listing -------------------------------------------------


def test_the_listing_follows_the_next_token(ctx):
    with Stubber(ctx.client("ssm")) as stub:
        stub.add_response(
            "describe_parameters",
            {"Parameters": [meta("/a")], "NextToken": "more"},
            {"MaxResults": ANY},
        )
        stub.add_response(
            "describe_parameters",
            {"Parameters": [meta("/b")]},
            {"MaxResults": ANY, "NextToken": "more"},
        )
        found = list(ssm.iter_parameters(ctx))
    assert [one.name for one in found] == ["/a", "/b"]


def test_contains_filters_locally_and_case_insensitively(ctx):
    with Stubber(ctx.client("ssm")) as stub:
        stub.add_response(
            "describe_parameters",
            {"Parameters": [meta("/app/DB/url"), meta("/other/thing")]},
            {"MaxResults": ANY},
        )
        found = list(ssm.iter_parameters(ctx, contains="db"))
    assert [one.name for one in found] == ["/app/DB/url"]


def test_an_arn_is_accepted_where_a_name_is_expected(ctx):
    arn = "arn:aws:ssm:eu-central-1:111122223333:parameter/db/prod/pw"
    with Stubber(ctx.client("ssm")) as stub:
        # The leading slash the ARN separator ate has to be put back.
        stub.add_response(
            "get_parameter",
            {"Parameter": valued("/db/prod/pw")},
            {"Name": "/db/prod/pw", "WithDecryption": False},
        )
        one = ssm.get_parameter(ctx, arn)
    assert one.name == "/db/prod/pw"


# --- writing a parameter ---------------------------------------------------


def test_writing_a_new_parameter_reads_first_and_says_created(ctx):
    with Stubber(ctx.client("ssm")) as stub:
        stub.add_client_error(
            "get_parameter", service_error_code="ParameterNotFound", http_status_code=400
        )
        stub.add_response(
            "put_parameter",
            {"Version": 1},
            {"Name": "/app/new", "Value": "v", "Type": "String", "Overwrite": False},
        )
        said = ssm.put_parameter(ctx, "/app/new", "v")
    assert "created" in said and "version 1" in said


def test_overwriting_an_existing_parameter_is_refused_without_being_asked(ctx):
    with Stubber(ctx.client("ssm")) as stub:
        stub.add_response(
            "get_parameter",
            {"Parameter": valued("/app/prod/url")},
            {"Name": "/app/prod/url", "WithDecryption": False},
        )
        with pytest.raises(ValueError, match="already exists"):
            ssm.put_parameter(ctx, "/app/prod/url", "new")
        # The refusal must bite before PutParameter, not after.
        stub.assert_no_pending_responses()


def test_changing_the_type_of_an_existing_parameter_is_refused(ctx):
    # AWS cannot do it at all, and its own complaint does not say so.
    with Stubber(ctx.client("ssm")) as stub:
        stub.add_response(
            "get_parameter",
            {"Parameter": valued("/app/prod/url", "x", "String")},
            {"Name": "/app/prod/url", "WithDecryption": False},
        )
        with pytest.raises(ValueError, match="cannot change that"):
            ssm.put_parameter(
                ctx, "/app/prod/url", "s3cret", type_name="SecureString", overwrite=True
            )
        stub.assert_no_pending_responses()


def test_the_type_is_validated_before_anything_is_sent(ctx):
    with Stubber(ctx.client("ssm")) as stub:
        with pytest.raises(ValueError, match="unknown parameter type"):
            ssm.put_parameter(ctx, "/a", "v", type_name="Secret")
        stub.assert_no_pending_responses()


def test_read_only_mode_refuses_a_write_and_a_delete(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    guarded = Context(region="eu-central-1", read_only=True)
    with Stubber(guarded.client("ssm")) as stub:
        stub.add_client_error(
            "get_parameter", service_error_code="ParameterNotFound", http_status_code=400
        )
        with pytest.raises(ReadOnlyError):
            ssm.put_parameter(guarded, "/a", "v")
        stub.assert_no_pending_responses()
    with Stubber(guarded.client("ssm")) as stub:
        with pytest.raises(ReadOnlyError):
            ssm.delete_parameter(guarded, "/a")
        stub.assert_no_pending_responses()


# --- documents -------------------------------------------------------------


def doc_row(name: str = "my-runbook", kind: str = "Command") -> dict:
    return {
        "Name": name,
        "DocumentType": kind,
        "Owner": "111122223333",
        "DocumentVersion": "1",
        "PlatformTypes": ["Linux"],
        "DocumentFormat": "YAML",
    }


def test_the_document_listing_asks_for_only_our_own_by_default(ctx):
    with Stubber(ctx.client("ssm")) as stub:
        stub.add_response(
            "list_documents",
            {"DocumentIdentifiers": [doc_row("zeta"), doc_row("Alpha")]},
            {"MaxResults": ANY, "Filters": [{"Key": "Owner", "Values": ["Self"]}]},
        )
        found = ssm.list_documents(ctx)
    # Case-folded, so "Alpha" comes first.
    assert [one.name for one in found] == ["Alpha", "zeta"]


def test_asking_for_everything_drops_the_owner_filter(ctx):
    with Stubber(ctx.client("ssm")) as stub:
        stub.add_response("list_documents", {"DocumentIdentifiers": []}, {"MaxResults": ANY})
        ssm.list_documents(ctx, mine=False)
        stub.assert_no_pending_responses()


def test_a_required_parameter_is_one_without_a_default(ctx):
    with Stubber(ctx.client("ssm")) as stub:
        stub.add_response(
            "describe_document",
            {
                "Document": {
                    **doc_row("AWS-RunShellScript"),
                    "Parameters": [
                        {"Name": "commands", "Type": "StringList"},
                        {"Name": "workingDirectory", "Type": "String", "DefaultValue": ""},
                    ],
                }
            },
            {"Name": "AWS-RunShellScript"},
        )
        one = ssm.get_document(ctx, "AWS-RunShellScript")
    # An empty default is still a default - only an absent one means required.
    assert one.required == ("commands",)
    assert one.runnable


# --- running a document ----------------------------------------------------


def test_running_a_non_command_document_is_refused_before_aws_is_called(ctx):
    with Stubber(ctx.client("ssm")) as stub:
        stub.add_response(
            "describe_document",
            {"Document": doc_row("my-automation", "Automation")},
            {"Name": "my-automation"},
        )
        with pytest.raises(ValueError, match="not a Command one"):
            ssm.run(ctx, "my-automation", ["i-1"])
        # No send_command was armed: reaching it would fail this test.
        stub.assert_no_pending_responses()


def test_a_missing_required_parameter_is_refused_before_the_script_runs(ctx):
    with Stubber(ctx.client("ssm")) as stub:
        stub.add_response(
            "describe_document",
            {
                "Document": {
                    **doc_row("AWS-RunShellScript"),
                    "Parameters": [{"Name": "commands", "Type": "StringList"}],
                }
            },
            {"Name": "AWS-RunShellScript"},
        )
        with pytest.raises(ValueError, match="still wants commands"):
            ssm.run(ctx, "AWS-RunShellScript", ["i-1"])
        stub.assert_no_pending_responses()


def test_a_good_run_sends_the_command_and_returns_the_id(ctx):
    with Stubber(ctx.client("ssm")) as stub:
        stub.add_response("describe_document", {"Document": doc_row("mine")}, {"Name": "mine"})
        stub.add_response(
            "send_command",
            {"Command": {"CommandId": COMMAND_ID}},
            {"DocumentName": "mine", "InstanceIds": ["i-1"]},
        )
        assert ssm.run(ctx, "mine", ["i-1"]) == COMMAND_ID


def test_read_only_mode_refuses_a_run_before_it_is_sent(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    guarded = Context(region="eu-central-1", read_only=True)
    with Stubber(guarded.client("ssm")) as stub:
        stub.add_response("describe_document", {"Document": doc_row("mine")}, {"Name": "mine"})
        with pytest.raises(ReadOnlyError):
            ssm.run(guarded, "mine", ["i-1"])
        stub.assert_no_pending_responses()


def test_a_failed_script_is_not_ok_even_though_aws_delivered_it(ctx):
    # The SendCommand/GetCommandInvocation pair is the same trap as Lambda's
    # FunctionError: the transport succeeding says nothing about the work.
    with Stubber(ctx.client("ssm")) as stub:
        stub.add_response(
            "get_command_invocation",
            {"Status": "Failed", "ResponseCode": 3, "StandardErrorContent": "boom"},
            {"CommandId": COMMAND_ID, "InstanceId": "i-1"},
        )
        done = ssm.invocation(ctx, COMMAND_ID, "i-1")
    assert done.done and not done.ok and done.exit_code == 3


def test_a_command_that_never_ran_has_no_exit_code(ctx):
    # SSM answers -1 when the command was never delivered; that is not exit -1.
    with Stubber(ctx.client("ssm")) as stub:
        stub.add_response(
            "get_command_invocation",
            {"Status": "Failed", "ResponseCode": -1, "StatusDetails": "Undeliverable"},
            {"CommandId": COMMAND_ID, "InstanceId": "i-1"},
        )
        done = ssm.invocation(ctx, COMMAND_ID, "i-1")
    assert done.exit_code is None and not done.ok


def test_waiting_polls_until_the_invocation_is_finished(ctx):
    slept: list[float] = []
    with Stubber(ctx.client("ssm")) as stub:
        for status in ("Pending", "InProgress", "Success"):
            payload: dict = {"Status": status}
            if status == "Success":
                payload["ResponseCode"] = 0
            stub.add_response(
                "get_command_invocation", payload, {"CommandId": COMMAND_ID, "InstanceId": "i-1"}
            )
        done = ssm.wait_for(ctx, COMMAND_ID, "i-1", sleep=slept.append)
    assert done.ok and len(slept) == 2, slept


def test_the_first_poll_may_arrive_before_the_invocation_exists(ctx):
    """`InvocationDoesNotExist` right after SendCommand is "not yet", not a failure.

    Found on the first live run against sw-sandbox: a perfectly good command
    reported an error because the per-instance record had not propagated by the
    time the first poll landed.
    """
    slept: list[float] = []
    with Stubber(ctx.client("ssm")) as stub:
        stub.add_client_error(
            "get_command_invocation",
            service_error_code="InvocationDoesNotExist",
            http_status_code=400,
        )
        stub.add_response(
            "get_command_invocation",
            {"Status": "Success", "ResponseCode": 0, "StandardOutputContent": "up 3 days"},
            {"CommandId": COMMAND_ID, "InstanceId": "i-1"},
        )
        done = ssm.wait_for(ctx, COMMAND_ID, "i-1", sleep=slept.append)
    assert done.ok and done.stdout == "up 3 days"
    assert len(slept) == 1, "it must wait and retry, not give up"


def test_a_real_error_while_waiting_is_still_raised(ctx):
    # Only the not-yet code is swallowed; AccessDenied must not look like Pending.
    with Stubber(ctx.client("ssm")) as stub:
        stub.add_client_error("get_command_invocation", service_error_code="AccessDeniedException")
        with pytest.raises(ClitkaError):
            ssm.wait_for(ctx, COMMAND_ID, "i-1", sleep=lambda _s: None)


def test_asking_for_one_invocation_directly_still_raises(ctx):
    # `invocation()` on its own is a question about *this* invocation, so "there
    # is no such thing" really is the answer, not a state to poll on.
    with Stubber(ctx.client("ssm")) as stub:
        stub.add_client_error(
            "get_command_invocation",
            service_error_code="InvocationDoesNotExist",
            http_status_code=400,
        )
        with pytest.raises(ClitkaError):
            ssm.invocation(ctx, COMMAND_ID, "i-1")


def test_waiting_gives_up_at_the_timeout_and_returns_what_it_saw(ctx):

    with Stubber(ctx.client("ssm")) as stub:
        stub.add_response(
            "get_command_invocation",
            {"Status": "InProgress"},
            {"CommandId": COMMAND_ID, "InstanceId": "i-1"},
        )
        # timeout=0 means "ask once, then give up" - still running is an answer,
        # not an exception.
        done = ssm.wait_for(ctx, COMMAND_ID, "i-1", timeout=0.0, sleep=lambda _s: None)
    assert not done.done and not done.ok
