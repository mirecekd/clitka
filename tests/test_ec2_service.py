"""The `ec2` plugin: the hooks, the F9 actions, the preview tab and the CLI.

The fifth plugin, and the seam held again: registering this package changed
exactly one line outside it (`plugins.BUILTIN_SERVICES`).

It is also the first plugin whose F9 actions mutate, so two of these tests exist
purely to guard that: every mutating action must be `destructive` (which is what
puts the confirm dialog in front of it), and no key here may collide with the
baseline `resources.*` actions - `d` for "Details" must never be `d` for "Delete".
"""

from __future__ import annotations

import datetime as dt

import pytest
from botocore.stub import ANY, Stubber
from typer.testing import CliRunner

from clitka.cli.main import app as root_app
from clitka.core import plugins
from clitka.core.actions import ResourceRef
from clitka.core.context import Context
from clitka.services.ec2 import actions as ea

runner = CliRunner()
LAUNCHED = dt.datetime(2026, 7, 1, 9, 30, tzinfo=dt.UTC)


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    return Context(region="eu-central-1")


@pytest.fixture
def ref():
    return ResourceRef.from_row(ea.TYPE_NAME, {"identifier": "i-0abc1234"})


def instance(state: str = "running", **extra):
    raw = {
        "InstanceId": "i-0abc1234",
        "InstanceType": "t3.micro",
        "State": {"Name": state, "Code": 16},
        "Tags": [{"Key": "Name", "Value": "web-01"}],
        "PrivateIpAddress": "10.0.0.5",
        "Placement": {"AvailabilityZone": "eu-central-1a"},
        "VpcId": "vpc-1",
        "SubnetId": "subnet-1",
        "LaunchTime": LAUNCHED,
    }
    raw.update(extra)
    return raw


def _armed_state(stub, state: str = "running"):
    stub.add_response(
        "describe_instances",
        {"Reservations": [{"Instances": [instance(state)]}]},
        {"InstanceIds": ["i-0abc1234"]},
    )


def test_the_self_check_passes():
    ea._self_check()


# --- the plugin seam ------------------------------------------------------


def test_the_plugin_publishes_its_cli_group():
    assert "ec2" in dict(plugins.service_apps())


def test_the_actions_and_the_preview_reach_the_registry():
    ids = {action.id for action in plugins.actions()}
    assert {"ec2.details", "ec2.start", "ec2.stop", "ec2.reboot"} <= ids
    assert "ec2.details" in {tab.id for tab in plugins.previews()}


def test_the_ec2_group_is_in_the_root_help():
    result = runner.invoke(root_app, ["--help"])
    assert result.exit_code == 0
    assert "ec2" in result.stdout


def test_no_action_or_tab_id_collides_across_five_plugins():
    ids = [action.id for action in plugins.actions()]
    assert len(set(ids)) == len(ids), ids
    tabs = [tab.id for tab in plugins.previews()]
    assert len(set(tabs)) == len(tabs), tabs


def test_no_single_key_is_claimed_twice_on_an_instance(ref):
    """The menu runs the FIRST action whose key matches - so `d` must be unique.

    This is the trap: `resources.delete` applies to every type with an identifier
    and owns `d`, so an `ec2.details` on `d` would have deleted the instance.
    """
    keys = [
        action.key
        for action in plugins.actions()
        if action.key and action.applies_to(ref)  # everything offered on an instance
    ]
    assert len(set(keys)) == len(keys), sorted(keys)


def test_the_actions_only_offer_themselves_on_an_instance():
    bucket = ResourceRef.from_row("AWS::S3::Bucket", {"identifier": "b"})
    assert not any(action.applies_to(bucket) for action in ea.ACTIONS)


def test_every_mutating_action_is_marked_destructive():
    for action in ea.ACTIONS:
        assert action.destructive == (action.id != "ec2.details"), action.id


# --- the F9 actions -------------------------------------------------------


def test_the_details_action_shows_what_matters(ctx, ref):
    with Stubber(ctx.client("ec2")) as stub:
        _armed_state(stub)
        result = ea.show_details(ctx, ref)
    assert result.title == "web-01 - details"
    for expected in ("running", "t3.micro", "10.0.0.5", "eu-central-1a", "vpc-1"):
        assert expected in result.body


def test_a_stop_reports_what_it_asked_for_and_asks_for_a_reload(ctx, ref):
    with Stubber(ctx.client("ec2")) as stub:
        _armed_state(stub, "running")
        stub.add_response("stop_instances", {}, {"InstanceIds": ["i-0abc1234"]})
        result = ea.do_stop(ctx, ref)
    assert "stop requested" in result.body
    # The branch must refetch, or the tree keeps showing the old state.
    assert result.reload


def test_a_refusal_is_a_result_not_a_crash(ctx, ref):
    # No start_instances armed: it must not be called, and the user must get a
    # readable answer rather than a traceback in the F9 result screen.
    with Stubber(ctx.client("ec2")) as stub:
        _armed_state(stub, "running")
        result = ea.do_start(ctx, ref)
        stub.assert_no_pending_responses()
    assert "Not done" in result.body and "cannot be started" in result.body
    assert not result.reload


def test_an_f9_stop_never_forces(ctx, ref):
    # `Force` would appear in the expected params and the stub would reject it.
    with Stubber(ctx.client("ec2")) as stub:
        _armed_state(stub, "running")
        stub.add_response("stop_instances", {}, {"InstanceIds": ["i-0abc1234"]})
        ea.do_stop(ctx, ref)
        stub.assert_no_pending_responses()


# --- the preview tab ------------------------------------------------------


def test_the_instance_tab_is_the_same_block_as_the_action(ctx, ref):
    with Stubber(ctx.client("ec2")) as stub:
        _armed_state(stub)
        body = ea.build_details_tab(ctx, ref)
    assert "t3.micro" in body and "running" in body


# --- the CLI --------------------------------------------------------------


def cli(monkeypatch, ctx, *args, **kwargs):
    """Run `clitka ec2 ...` against a Context whose clients are stubbed."""
    monkeypatch.setattr("clitka.core.context.Context.from_env", staticmethod(lambda **_: ctx))
    return runner.invoke(root_app, ["ec2", *args], **kwargs)


def test_list_prints_the_name_and_the_state(monkeypatch, ctx):
    with Stubber(ctx.client("ec2")) as stub:
        stub.add_response(
            "describe_instances",
            {"Reservations": [{"Instances": [instance()]}]},
            {"MaxResults": ANY},
        )
        result = cli(monkeypatch, ctx, "list")
    assert result.exit_code == 0
    assert "web-01" in result.stdout and "running" in result.stdout


def test_list_can_filter_by_state(monkeypatch, ctx):
    with Stubber(ctx.client("ec2")) as stub:
        stub.add_response(
            "describe_instances",
            {"Reservations": [{"Instances": [instance("running")]}]},
            {"MaxResults": ANY},
        )
        result = cli(monkeypatch, ctx, "list", "--state", "stopped")
    assert result.exit_code == 0
    assert "web-01" not in result.stdout


def test_get_shows_one_instance(monkeypatch, ctx):
    with Stubber(ctx.client("ec2")) as stub:
        _armed_state(stub)
        result = cli(monkeypatch, ctx, "get", "i-0abc1234")
    assert result.exit_code == 0
    assert "subnet-1" in result.stdout


def test_stop_asks_first_and_an_answer_of_no_stops_nothing(monkeypatch, ctx):
    with Stubber(ctx.client("ec2")) as stub:  # nothing armed at all
        result = runner.invoke(
            root_app, ["ec2", "stop", "i-0abc1234"], input="n\n", obj={"context": ctx}
        )
        stub.assert_no_pending_responses()
    assert result.exit_code != 0


def test_stop_with_yes_goes_through(monkeypatch, ctx):
    with Stubber(ctx.client("ec2")) as stub:
        _armed_state(stub, "running")
        stub.add_response("stop_instances", {}, {"InstanceIds": ["i-0abc1234"]})
        result = cli(monkeypatch, ctx, "stop", "i-0abc1234", "--yes")
    assert result.exit_code == 0
    assert "[OK]" in result.stdout


def test_starting_a_running_instance_exits_non_zero(monkeypatch, ctx):
    with Stubber(ctx.client("ec2")) as stub:
        _armed_state(stub, "running")
        result = cli(monkeypatch, ctx, "start", "i-0abc1234", "--yes")
        stub.assert_no_pending_responses()
    assert result.exit_code == 1


def test_the_cli_offers_no_terminate(monkeypatch, ctx):
    result = runner.invoke(root_app, ["ec2", "--help"])
    assert result.exit_code == 0
    assert "terminate" not in result.stdout.lower()
