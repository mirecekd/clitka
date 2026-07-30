"""Core smoke tests: errors, output, context, plugin registry, CLI wiring."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from typer.testing import CliRunner

from clitka import __version__
from clitka.cli.main import app
from clitka.core import output, plugins
from clitka.core.context import Context
from clitka.core.errors import AwsError, ReadOnlyError, wrap_aws_errors

runner = CliRunner()


def test_client_error_is_translated():
    @wrap_aws_errors
    def boom():
        raise ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "nope"}, "ResponseMetadata": {}},
            "ListBuckets",
        )

    with pytest.raises(AwsError) as excinfo:
        boom()
    assert excinfo.value.code == "AccessDenied"
    assert excinfo.value.operation == "ListBuckets"
    assert "IAM permission" in str(excinfo.value)


def test_jsonable_handles_aws_types():
    out = output._jsonable(
        {
            "when": dt.datetime(2026, 1, 2, 3, 4, 5),
            "num": Decimal("2.25"),
            "tags": {"b", "a"},
            "blob": b"hi",
        }
    )
    assert out == {
        "when": "2026-01-02T03:04:05",
        "num": 2.25,
        "tags": ["a", "b"],
        "blob": "hi",
    }


def test_read_only_context_refuses_writes():
    ctx = Context(read_only=True)
    with pytest.raises(ReadOnlyError):
        ctx.require_write("delete a bucket")
    Context(read_only=False).require_write("delete a bucket")


def test_identity_is_cached(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    ctx = Context(region="eu-central-1")
    sts = ctx.client("sts")
    with Stubber(sts) as stub:
        stub.add_response(
            "get_caller_identity",
            {
                "Account": "111122223333",
                "Arn": "arn:aws:iam::111122223333:user/mirek",
                "UserId": "AIDA",
            },
        )
        first = ctx.identity()
        second = ctx.identity()  # served from cache, no second stubbed response
        stub.assert_no_pending_responses()
    assert first is second
    assert first.account == "111122223333"
    assert first.display == "mirek"


def test_client_is_cached():
    ctx = Context(region="eu-central-1")
    assert ctx.client("sts") is ctx.client("sts")


def test_plugin_manager_starts_empty_but_valid():
    pm = plugins.get_manager()
    assert pm.project_name == "clitka"
    assert isinstance(plugins.resource_kinds(), list)
    assert isinstance(plugins.actions(), list)


def test_cli_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_cli_ctx_show_reports_profile(monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    result = runner.invoke(app, ["--region", "eu-central-1", "ctx", "show", "-o", "json"])
    assert result.exit_code == 0
    assert "eu-central-1" in result.stdout
