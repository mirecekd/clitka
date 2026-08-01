"""The `apigw` plugin: the hooks, the F9 actions, the preview tabs and the CLI.

The seventh plugin, and the seam held again: registering this package changed
exactly one line outside it (`plugins.BUILTIN_SERVICES`).

Two tests here exist for reasons the earlier plugins established the hard way:

- **the F9 single-key namespace is global per resource** (`ec2.details` on `d` was
  nearly a delete), so every key is checked against everything else that applies
  to the same ref, and
- **invoking must not be an F9 action** - a request is not reversible, so F9 hands
  over the command exactly as `lambda.invoke` does. A test asserts nobody adds it.
"""

from __future__ import annotations

import datetime as dt
import urllib.error
import urllib.request

import pytest
from botocore.stub import ANY, Stubber
from typer.testing import CliRunner

from clitka.cli.main import app as root_app
from clitka.core import plugins
from clitka.core.actions import ResourceRef
from clitka.core.context import Context
from clitka.services.apigw import actions as aa

runner = CliRunner()
CREATED = dt.datetime(2026, 7, 1, 9, 30, tzinfo=dt.UTC)


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    return Context(region="eu-central-1")


@pytest.fixture
def rest_ref():
    return ResourceRef.from_row(aa.REST_API, {"identifier": "abc123"})


def raw_rest(**extra) -> dict:
    out = {
        "id": "abc123",
        "name": "pets",
        "createdDate": CREATED,
        "endpointConfiguration": {"types": ["REGIONAL"]},
    }
    out.update(extra)
    return out


def armed_rest(stub, **extra) -> None:
    stub.add_response("get_rest_api", raw_rest(**extra), {"restApiId": "abc123"})


# --- the plugin seam ------------------------------------------------------


def test_the_self_check_passes():
    aa._self_check()


def test_the_plugin_is_registered_and_publishes_its_cli_group():
    names = dict(plugins.service_apps())
    assert "apigw" in names, sorted(names)


def test_the_plugin_is_the_seventh_builtin():
    assert "clitka.services.apigw" in plugins.BUILTIN_SERVICES
    assert len(plugins.BUILTIN_SERVICES) == 7


def test_the_actions_and_previews_reach_the_registry():
    ids = {action.id for action in plugins.actions()}
    assert {"apigw.routes", "apigw.stages", "apigw.invoke"} <= ids
    tabs = {tab.id for tab in plugins.previews()}
    assert {"apigw.routes", "apigw.stages"} <= tabs


def test_no_two_plugins_publish_the_same_action_id():
    ids = [action.id for action in plugins.actions()]
    assert len(set(ids)) == len(ids), sorted(ids)


def test_no_two_plugins_publish_the_same_preview_id():
    ids = [tab.id for tab in plugins.previews()]
    assert len(set(ids)) == len(ids), sorted(ids)


# --- the F9 contract ------------------------------------------------------


def test_both_kinds_of_api_are_the_same_thing_to_f9(rest_ref):
    http_ref = ResourceRef.from_row(aa.HTTP_API, {"identifier": "def456"})
    for ref in (rest_ref, http_ref):
        assert all(action.applies_to(ref) for action in aa.ACTIONS)
    # And nothing else claims to be an API.
    assert not aa.is_api(ResourceRef.from_row("AWS::S3::Bucket", {}))


def test_no_f9_key_collides_with_anything_else_on_the_same_type(rest_ref):
    # `ActionMenu.on_key` runs the FIRST match, so a shared key silently runs
    # somebody else's action - which is how `d` for "Details" nearly deleted an
    # EC2 instance. Every plugin must repeat this check.
    for action in plugins.actions():
        if not action.key or not action.applies_to(rest_ref):
            continue
        others = [
            one
            for one in plugins.actions()
            if one is not action and one.key == action.key and one.applies_to(rest_ref)
        ]
        assert not others, (action.id, [one.id for one in others])


def test_invoking_is_deliberately_not_an_action():
    # F9 hands over the command; it never sends the request. A POST is not
    # reversible, so this is a stronger rule than the EC2 power actions'.
    for action in aa.ACTIONS:
        assert not action.destructive, action.id
    labels = [action.label for action in aa.ACTIONS]
    assert "Invoke (how to)" in labels and "Invoke" not in labels


def test_an_api_id_is_found_however_cloud_control_reported_it():
    assert aa.api_id(ResourceRef(aa.REST_API, "", {"RestApiId": "one"})) == "one"
    assert aa.api_id(ResourceRef(aa.HTTP_API, "", {"ApiId": "two"})) == "two"
    arn = "arn:aws:apigateway:eu-central-1::/restapis/three"
    assert aa.api_id(ResourceRef(aa.REST_API, arn, {})) == "three"


def test_the_preview_tabs_are_lazy_because_they_call_aws():
    # A tab that calls AWS must only be built when it is actually shown.
    assert all(tab.lazy for tab in aa.PREVIEWS)


# --- what the actions actually render -------------------------------------


def test_the_routes_action_lists_the_methods_and_warns_when_none_are_open(ctx, rest_ref):
    with Stubber(ctx.client("apigateway")) as stub:
        armed_rest(stub)
        stub.add_response(
            "get_resources",
            {
                "items": [
                    {
                        "id": "r1",
                        "path": "/pets",
                        "resourceMethods": {"GET": {"authorizationType": "AWS_IAM"}},
                    }
                ]
            },
            {"restApiId": "abc123", "limit": ANY, "embed": ANY},
        )
        result = aa.show_routes(ctx, rest_ref)
    assert "1 routes" in result.title and "GET" in result.body
    # Every route behind an authorizer is worth saying out loud.
    assert "will 403" in result.body


def test_the_stages_action_shows_the_url_of_each_stage(ctx, rest_ref):
    with Stubber(ctx.client("apigateway")) as stub:
        armed_rest(stub)
        stub.add_response(
            "get_stages",
            {"item": [{"stageName": "prod", "deploymentId": "d1"}]},
            {"restApiId": "abc123"},
        )
        result = aa.show_stages(ctx, rest_ref)
    assert "abc123.execute-api.eu-central-1.amazonaws.com/prod" in result.body


def test_no_stage_is_reported_as_never_deployed_not_as_an_empty_table(ctx, rest_ref):
    # This is the answer to "why does my API 403 on everything".
    with Stubber(ctx.client("apigateway")) as stub:
        armed_rest(stub)
        stub.add_response("get_stages", {}, {"restApiId": "abc123"})
        result = aa.show_stages(ctx, rest_ref)
    assert "never been deployed" in result.body


def test_the_invoke_hint_hands_over_the_command(ctx, rest_ref):
    with Stubber(ctx.client("apigateway")) as stub:
        armed_rest(stub)
        stub.add_response("get_stages", {"item": [{"stageName": "prod"}]}, {"restApiId": "abc123"})
        result = aa.show_invoke_hint(ctx, rest_ref)
    assert "clitka apigw invoke abc123 prod" in result.body
    assert "--dry-run" in result.body


def test_the_invoke_hint_refuses_an_unreachable_api_before_listing_stages(ctx, rest_ref):
    # A PRIVATE endpoint cannot be called from here, so the stages are not even
    # fetched - nothing is armed for `get_stages`.
    with Stubber(ctx.client("apigateway")) as stub:
        armed_rest(stub, endpointConfiguration={"types": ["PRIVATE"]})
        result = aa.show_invoke_hint(ctx, rest_ref)
        stub.assert_no_pending_responses()
    assert "Not reachable" in result.body and "VPC" in result.body


# --- the CLI --------------------------------------------------------------


def test_the_cli_group_is_reachable_from_the_root_app():
    result = runner.invoke(root_app, ["apigw", "--help"])
    assert result.exit_code == 0
    for command in ("list", "get", "routes", "stages", "invoke"):
        assert command in result.stdout


def test_every_cli_command_has_its_own_help():
    for command in ("list", "get", "routes", "stages", "invoke"):
        result = runner.invoke(root_app, ["apigw", command, "--help"])
        assert result.exit_code == 0, (command, result.stdout)


def test_a_malformed_param_is_a_sentence_not_a_traceback(monkeypatch):
    # `--param petId` without a value cannot fill anything, and the complaint
    # must arrive before any AWS call.
    def never(*_args, **_kwargs):
        raise AssertionError("AWS must not be called for a malformed --param")

    monkeypatch.setattr("clitka.core.apigw.get_api", never)
    result = runner.invoke(root_app, ["apigw", "invoke", "abc", "prod", "--param", "petId"])
    assert result.exit_code == 1
    assert "not name=value" in result.output


def test_the_invoke_command_exits_1_on_a_non_2xx(monkeypatch):
    """The reason this command exists rather than `curl`: a usable exit code."""

    def fake_get_api(_ctx, _identifier):
        from clitka.core.apigwmodel import Api

        return Api("abc123", name="pets", region="eu-central-1")

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 500, "boom", {}, None)

    monkeypatch.setattr("clitka.core.apigw.get_api", fake_get_api)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = runner.invoke(root_app, ["apigw", "invoke", "abc123", "prod"])
    assert result.exit_code == 1


def test_the_dry_run_prints_the_request_and_sends_nothing(monkeypatch):
    def fake_get_api(_ctx, _identifier):
        from clitka.core.apigwmodel import Api

        return Api("abc123", name="pets", region="eu-central-1")

    def never(*_args, **_kwargs):
        raise AssertionError("--dry-run must not open a socket")

    monkeypatch.setattr("clitka.core.apigw.get_api", fake_get_api)
    monkeypatch.setattr(urllib.request, "urlopen", never)
    result = runner.invoke(
        root_app, ["apigw", "invoke", "abc123", "prod", "--path", "/pets", "--dry-run"]
    )
    assert result.exit_code == 0
    assert "GET https://abc123.execute-api.eu-central-1.amazonaws.com/prod/pets" in result.output
