"""`core/ec2model.py` and `core/ec2.py` - the state machine and the API side.

The two things worth guarding here are the two things EC2 gets wrong if nobody
looks: `DescribeInstances` nests its results in reservations, and a power call on
the wrong state has to be refused with a sentence rather than an AWS error code.
"""

from __future__ import annotations

import datetime as dt

import pytest
from botocore.stub import ANY, Stubber

from clitka.core import ec2
from clitka.core import ec2model as em
from clitka.core.context import Context
from clitka.core.errors import ReadOnlyError

LAUNCHED = dt.datetime(2026, 7, 1, 9, 30, tzinfo=dt.UTC)


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    return Context(region="eu-central-1")


def instance(instance_id: str = "i-0abc1234", state: str = "running", **extra):
    raw = {
        "InstanceId": instance_id,
        "InstanceType": "t3.micro",
        "State": {"Name": state, "Code": 16},
        "Tags": [{"Key": "Name", "Value": "web-01"}],
        "PrivateIpAddress": "10.0.0.5",
        "Placement": {"AvailabilityZone": "eu-central-1a"},
        "LaunchTime": LAUNCHED,
    }
    raw.update(extra)
    return raw


def reservations(*instances, token: str = "") -> dict:
    """The shape DescribeInstances really answers with."""
    page: dict = {"Reservations": [{"Instances": list(instances)}]}
    if token:
        page["NextToken"] = token
    return page


def test_the_self_checks_pass():
    em._self_check()
    ec2._self_check()


# --- the model ------------------------------------------------------------


def test_the_state_decides_what_may_be_done():
    running = em.Instance("i-1", state=em.RUNNING)
    stopped = em.Instance("i-1", state=em.STOPPED)
    assert (running.can_stop, running.can_reboot, running.can_start) == (True, True, False)
    assert (stopped.can_start, stopped.can_stop, stopped.can_reboot) == (True, False, False)


def test_a_transitional_state_forbids_everything_and_says_why():
    for state in ("pending", "stopping", "shutting-down", "terminated"):
        one = em.Instance("i-1", name="web-01", state=state)
        assert not (one.can_start or one.can_stop or one.can_reboot), state
        if state in ("pending", "stopping", "shutting-down"):
            assert "wait for it to settle" in one.refuses("start"), state


def test_the_refusal_is_spelled_correctly():
    # `f"{verb}ed"` would have said "stoped" - the self-check caught it once.
    assert "cannot be stopped" in em.Instance("i-1", state=em.STOPPED).refuses("stop")
    assert "cannot be started" in em.Instance("i-1", state=em.RUNNING).refuses("start")
    assert "cannot be rebooted" in em.Instance("i-1", state=em.STOPPED).refuses("reboot")


def test_an_instance_is_called_by_its_name_tag_and_falls_back_to_the_id():
    assert em.Instance("i-1", name="web-01").label == "web-01"
    assert em.Instance("i-1").label == "i-1"


# --- the listing ----------------------------------------------------------


def test_describe_instances_is_flattened_across_reservations(ctx):
    # Two reservations with two instances each: a caller that trusts the top
    # level would see two instances instead of four.
    client = ctx.client("ec2")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_instances",
            {
                "Reservations": [
                    {"Instances": [instance("i-1"), instance("i-2")]},
                    {"Instances": [instance("i-3"), instance("i-4")]},
                ]
            },
            {"MaxResults": ANY},
        )
        found = list(ec2.iter_instances(ctx))
    assert [one.instance_id for one in found] == ["i-1", "i-2", "i-3", "i-4"]


def test_the_listing_follows_the_next_token(ctx):
    client = ctx.client("ec2")
    with Stubber(client) as stub:
        first = reservations(instance("i-1"), token="more")
        stub.add_response("describe_instances", first, {"MaxResults": ANY})

        stub.add_response(
            "describe_instances",
            reservations(instance("i-2")),
            {"MaxResults": ANY, "NextToken": "more"},
        )
        found = list(ec2.iter_instances(ctx))
    assert [one.instance_id for one in found] == ["i-1", "i-2"]


def test_a_lookup_by_id_does_not_send_max_results(ctx):
    # InstanceIds and MaxResults are mutually exclusive - AWS rejects both.
    client = ctx.client("ec2")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_instances",
            reservations(instance()),
            {"InstanceIds": ["i-0abc1234"]},
        )
        one = ec2.get_instance(ctx, "i-0abc1234")
    assert one.name == "web-01" and one.private_ip == "10.0.0.5"


def test_an_arn_is_accepted_where_an_id_is_expected(ctx):
    client = ctx.client("ec2")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_instances",
            reservations(instance()),
            {"InstanceIds": ["i-0abc1234"]},
        )
        one = ec2.get_instance(ctx, "arn:aws:ec2:eu-central-1:1:instance/i-0abc1234")
    assert one.instance_id == "i-0abc1234"


def test_a_missing_instance_is_a_lookup_error(ctx):
    client = ctx.client("ec2")
    with Stubber(client) as stub:
        stub.add_response("describe_instances", {"Reservations": []}, {"InstanceIds": ["i-gone"]})
        with pytest.raises(LookupError):
            ec2.get_instance(ctx, "i-gone")


def test_the_listing_is_sorted_by_what_the_user_reads_first(ctx):
    client = ctx.client("ec2")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_instances",
            reservations(
                instance("i-1", Tags=[{"Key": "Name", "Value": "zeta"}]),
                instance("i-2", Tags=[{"Key": "Name", "Value": "Alpha"}]),
            ),
            {"MaxResults": ANY},
        )
        found = ec2.list_instances(ctx)
    # Case-folded, so "Alpha" comes before "zeta" rather than after it.
    assert [one.label for one in found] == ["Alpha", "zeta"]


# --- the power operations -------------------------------------------------


def _armed_state(stub, state: str, instance_id: str = "i-0abc1234"):
    stub.add_response(
        "describe_instances",
        reservations(instance(instance_id, state=state)),
        {"InstanceIds": [instance_id]},
    )


def test_a_stop_reads_the_state_first_and_then_stops(ctx):
    client = ctx.client("ec2")
    with Stubber(client) as stub:
        _armed_state(stub, "running")
        stub.add_response(
            "stop_instances",
            {"StoppingInstances": []},
            {"InstanceIds": ["i-0abc1234"]},
        )
        said = ec2.power(ctx, "stop", "i-0abc1234")
    assert "web-01" in said and "stop requested" in said


def test_starting_a_running_instance_is_refused_before_aws_is_called(ctx):
    # No start_instances is armed: reaching AWS would fail the test. AWS accepts
    # this silently, which is exactly why it is refused here.
    client = ctx.client("ec2")
    with Stubber(client) as stub:
        _armed_state(stub, "running")
        with pytest.raises(ValueError, match="cannot be started"):
            ec2.power(ctx, "start", "i-0abc1234")
        stub.assert_no_pending_responses()


def test_a_transitional_instance_is_told_to_wait(ctx):
    client = ctx.client("ec2")
    with Stubber(client) as stub:
        _armed_state(stub, "pending")
        with pytest.raises(ValueError, match="settle"):
            ec2.power(ctx, "stop", "i-0abc1234")


def test_force_only_reaches_a_stop(ctx):
    client = ctx.client("ec2")
    with Stubber(client) as stub:
        _armed_state(stub, "running")
        stub.add_response(
            "stop_instances",
            {"StoppingInstances": []},
            {"InstanceIds": ["i-0abc1234"], "Force": True},
        )
        ec2.power(ctx, "stop", "i-0abc1234", force=True)
        stub.assert_no_pending_responses()

    # A reboot ignores it rather than sending an argument the API has no room for.
    with Stubber(ctx.client("ec2")) as stub:
        _armed_state(stub, "running")
        stub.add_response("reboot_instances", {}, {"InstanceIds": ["i-0abc1234"]})
        ec2.power(ctx, "reboot", "i-0abc1234", force=True)
        stub.assert_no_pending_responses()


def test_read_only_mode_refuses_every_verb(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    guarded = Context(region="eu-central-1", read_only=True)
    for verb, state in (("start", "stopped"), ("stop", "running"), ("reboot", "running")):
        with Stubber(guarded.client("ec2")) as stub:
            _armed_state(stub, state)
            with pytest.raises(ReadOnlyError):
                ec2.power(guarded, verb, "i-0abc1234")
            # The guard must bite before the API call, not after.
            stub.assert_no_pending_responses()


def test_an_unknown_verb_never_reaches_aws(ctx):
    with Stubber(ctx.client("ec2")) as stub:
        with pytest.raises(ValueError, match="unknown EC2 operation"):
            ec2.power(ctx, "terminate", "i-0abc1234")
        stub.assert_no_pending_responses()


def test_terminate_is_not_implemented_at_all():
    assert set(ec2.VERBS) == {"start", "stop", "reboot"}
