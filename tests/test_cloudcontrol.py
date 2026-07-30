"""Cloud Control explorer: paging, column picking, error translation, CLI."""

from __future__ import annotations

import json

import pytest
from botocore.stub import Stubber
from typer.testing import CliRunner

from clitka.cli.main import app
from clitka.core import cloudcontrol as cc
from clitka.core.context import Context
from clitka.core.errors import AwsError, ReadOnlyError

runner = CliRunner()


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    return Context(region="eu-central-1")


def _description(identifier: str, **properties):
    return {"Identifier": identifier, "Properties": json.dumps(properties)}


def test_resource_row_puts_identifier_first():
    resource = cc.Resource("AWS::S3::Bucket", "b1", {"BucketName": "b1", "Arn": "arn:x"})
    row = resource.row()
    assert next(iter(row)) == "identifier"
    assert row["BucketName"] == "b1"


def test_columns_for_prefers_common_properties():
    resources = [
        cc.Resource("T", "a", {"Name": "a", "State": "on"}),
        cc.Resource("T", "b", {"Name": "b", "State": "off"}),
        cc.Resource("T", "c", {"Name": "c", "Rare": 1}),
    ]
    columns = cc.columns_for(resources, limit=3)
    assert columns[0] == "identifier"
    assert columns[1:] == ["Name", "State"]


def test_columns_for_empty_input():
    assert cc.columns_for([]) == ["identifier"]


def test_properties_that_are_not_json_are_kept_verbatim():
    assert cc._parse_properties("not json") == {"Properties": "not json"}
    assert cc._parse_properties(None) == {}
    assert cc._parse_properties('{"a":1}') == {"a": 1}


def test_list_resources_follows_pagination(ctx):
    client = ctx.client("cloudcontrol")
    with Stubber(client) as stub:
        stub.add_response(
            "list_resources",
            {
                "TypeName": "AWS::S3::Bucket",
                "ResourceDescriptions": [_description("one", BucketName="one")],
                "NextToken": "t1",
            },
            {"TypeName": "AWS::S3::Bucket", "MaxResults": 100},
        )
        stub.add_response(
            "list_resources",
            {
                "TypeName": "AWS::S3::Bucket",
                "ResourceDescriptions": [_description("two", BucketName="two")],
            },
            {"TypeName": "AWS::S3::Bucket", "MaxResults": 100, "NextToken": "t1"},
        )
        found = cc.list_resources(ctx, "AWS::S3::Bucket")
        stub.assert_no_pending_responses()
    assert [r.identifier for r in found] == ["one", "two"]
    assert found[0].properties["BucketName"] == "one"


def test_list_resources_limit_stops_early(ctx):
    client = ctx.client("cloudcontrol")
    with Stubber(client) as stub:
        stub.add_response(
            "list_resources",
            {
                "TypeName": "AWS::S3::Bucket",
                "ResourceDescriptions": [_description("a"), _description("b")],
                "NextToken": "more",
            },
            None,
        )
        found = cc.list_resources(ctx, "AWS::S3::Bucket", limit=1)
    assert [r.identifier for r in found] == ["a"]


def test_resource_model_is_passed_as_json(ctx):
    client = ctx.client("cloudcontrol")
    with Stubber(client) as stub:
        stub.add_response(
            "list_resources",
            {"TypeName": "AWS::EC2::Subnet", "ResourceDescriptions": []},
            {
                "TypeName": "AWS::EC2::Subnet",
                "MaxResults": 100,
                "ResourceModel": json.dumps({"VpcId": "vpc-1"}),
            },
        )
        assert cc.list_resources(ctx, "AWS::EC2::Subnet", {"VpcId": "vpc-1"}) == []
        stub.assert_no_pending_responses()


def test_child_type_error_becomes_additional_inputs_error(ctx):
    client = ctx.client("cloudcontrol")
    with Stubber(client) as stub:
        stub.add_client_error(
            "list_resources",
            service_error_code="InvalidRequestException",
            service_message="Missing Or Invalid ResourceModel property: VpcId",
        )
        with pytest.raises(cc.AdditionalInputsError) as excinfo:
            cc.list_resources(ctx, "AWS::EC2::Subnet")
    assert excinfo.value.type_name == "AWS::EC2::Subnet"
    assert "VpcId" in str(excinfo.value)
    assert "parent identifier" in str(excinfo.value)


def test_other_errors_stay_aws_errors_with_the_iam_hint(ctx):
    client = ctx.client("cloudcontrol")
    with Stubber(client) as stub:
        stub.add_client_error(
            "list_resources",
            service_error_code="AccessDeniedException",
            service_message="not authorized",
        )
        with pytest.raises(AwsError) as excinfo:
            cc.list_resources(ctx, "AWS::S3::Bucket")
    assert "IAM permission" in str(excinfo.value)


def test_get_resource(ctx):
    client = ctx.client("cloudcontrol")
    with Stubber(client) as stub:
        stub.add_response(
            "get_resource",
            {
                "TypeName": "AWS::S3::Bucket",
                "ResourceDescription": _description("b1", BucketName="b1", Arn="arn:x"),
            },
            {"TypeName": "AWS::S3::Bucket", "Identifier": "b1"},
        )
        resource = cc.get_resource(ctx, "AWS::S3::Bucket", "b1")
    assert resource.identifier == "b1"
    assert resource.properties["Arn"] == "arn:x"


def test_delete_resource_reports_progress(ctx):
    client = ctx.client("cloudcontrol")
    with Stubber(client) as stub:
        stub.add_response(
            "delete_resource",
            {
                "ProgressEvent": {
                    "TypeName": "AWS::S3::Bucket",
                    "Identifier": "b1",
                    "Operation": "DELETE",
                    "OperationStatus": "IN_PROGRESS",
                    "RequestToken": "tok",
                }
            },
            {"TypeName": "AWS::S3::Bucket", "Identifier": "b1"},
        )
        result = cc.delete_resource(ctx, "AWS::S3::Bucket", "b1")
    assert result["status"] == "IN_PROGRESS"
    assert result["request_token"] == "tok"


def test_delete_is_refused_in_read_only_mode(ctx):
    ctx.read_only = True
    with pytest.raises(ReadOnlyError):
        cc.delete_resource(ctx, "AWS::S3::Bucket", "b1")


def test_list_types_sorts_and_trims(ctx):
    client = ctx.client("cloudformation")
    with Stubber(client) as stub:
        stub.add_response(
            "list_types",
            {
                "TypeSummaries": [
                    {"TypeName": "AWS::S3::Bucket", "Description": "A bucket\nsecond line"},
                    {"TypeName": "AWS::EC2::Instance", "Description": "An instance"},
                ]
            },
            {"Visibility": "PUBLIC", "Type": "RESOURCE"},
        )
        rows = cc.list_types(ctx)
    assert [row["type_name"] for row in rows] == ["AWS::EC2::Instance", "AWS::S3::Bucket"]
    assert rows[1]["description"] == "A bucket"


# --- CLI ------------------------------------------------------------------


def test_cli_resources_is_registered():
    result = runner.invoke(app, ["resources", "--help"])
    assert result.exit_code == 0
    for verb in ("types", "list", "get", "delete"):
        assert verb in result.stdout


def test_cli_rejects_bad_input_json():
    result = runner.invoke(app, ["resources", "list", "AWS::S3::Bucket", "--input", "{oops"])
    assert result.exit_code == 1
    assert "not valid JSON" in result.output


def test_cli_delete_aborts_without_confirmation(monkeypatch):
    called = []
    monkeypatch.setattr(cc, "delete_resource", lambda *a, **k: called.append(a))
    result = runner.invoke(app, ["resources", "delete", "AWS::S3::Bucket", "b1"], input="n\n")
    assert result.exit_code != 0
    assert called == []


def test_cli_delete_proceeds_with_yes(monkeypatch):
    monkeypatch.setattr(
        cc,
        "delete_resource",
        lambda _ctx, type_name, identifier: {"status": "SUCCESS", "identifier": identifier},
    )
    result = runner.invoke(
        app, ["resources", "delete", "AWS::S3::Bucket", "b1", "--yes", "-o", "json"]
    )
    assert result.exit_code == 0, result.output
    assert "SUCCESS" in result.stdout
