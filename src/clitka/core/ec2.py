"""EC2: the instance listing, and the three power operations.

Same shape as `core/logs.py`, `core/lambdafn.py` and `core/ecr.py` - generators
plus `wrap_aws_errors`, with the boto3-free row types in `core/ec2model.py`.

Two things about EC2 shaped this module:

- **`DescribeInstances` nests.** The response is `Reservations[].Instances[]`,
  not a flat list, and a reservation can hold several instances. Every caller
  that forgets this silently sees one instance per reservation.
- **A power call is checked against the state first.** `StartInstances` on a
  running instance is a silent no-op and `StopInstances` on a `pending` one is an
  error, so `power()` reads the instance, asks `Instance.refuses()` and complains
  in a sentence instead of letting AWS do it in a code. Terminate is deliberately
  absent - see the decision log.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Any

from clitka.core.context import Context
from clitka.core.ec2model import Instance, instance_id_of
from clitka.core.errors import wrap_aws_errors
from clitka.core.resname import name_from_tags

__all__ = [
    "PAGE",
    "VERBS",
    "Instance",
    "get_instance",
    "instance_id_of",
    "iter_instances",
    "list_instances",
    "power",
]

PAGE = 50  # DescribeInstances allows up to 1000, but 50 paints sooner

# The three verbs, and the API call each one makes. Terminate is not here on
# purpose: it destroys data and there is no undo, so it stays a console job.
VERBS: dict[str, str] = {
    "start": "start_instances",
    "stop": "stop_instances",
    "reboot": "reboot_instances",
}


def _client(ctx: Context) -> Any:
    return ctx.client("ec2")


def _instance_from(raw: dict[str, Any]) -> Instance:
    """One `Instances[]` entry as an `Instance`.

    Every field is optional in practice: a `pending` instance has no IP yet, a
    `stopped` one has lost its public one, and only some have a placement.
    """
    placement = raw.get("Placement") or {}
    state = (raw.get("State") or {}).get("Name", "")
    reason = (raw.get("StateReason") or {}).get("Message", "")
    return Instance(
        instance_id=str(raw.get("InstanceId", "")),
        name=name_from_tags(raw.get("Tags")),
        state=str(state),
        instance_type=str(raw.get("InstanceType", "")),
        private_ip=str(raw.get("PrivateIpAddress", "")),
        public_ip=str(raw.get("PublicIpAddress", "")),
        availability_zone=str(placement.get("AvailabilityZone", "")),
        launched=_moment(raw.get("LaunchTime")),
        key_name=str(raw.get("KeyName", "")),
        vpc_id=str(raw.get("VpcId", "")),
        subnet_id=str(raw.get("SubnetId", "")),
        platform=str(raw.get("PlatformDetails", "") or raw.get("Platform", "")),
        state_reason=str(reason),
    )


def _moment(when: Any) -> dt.datetime | None:
    """EC2 timestamps arrive as datetimes already; anything else is discarded."""
    return when if isinstance(when, dt.datetime) else None


# --- listing ---------------------------------------------------------------


def iter_instances(
    ctx: Context, ids: list[str] | None = None, page_size: int = PAGE
) -> Iterator[Instance]:
    """Yield every instance in the region, page by page.

    `Reservations[].Instances[]` is flattened here, which is the whole reason
    this generator exists rather than a paginator call at each call site.
    """
    client = _client(ctx)
    kwargs: dict[str, Any] = {"MaxResults": page_size}
    if ids:
        # MaxResults and InstanceIds are mutually exclusive in this API.
        kwargs = {"InstanceIds": [instance_id_of(one) for one in ids]}
    token: str | None = None
    while True:
        if token:
            kwargs["NextToken"] = token
        page = _instances_page(ctx, client, kwargs)
        for reservation in page.get("Reservations", []):
            for raw in reservation.get("Instances", []):
                yield _instance_from(raw)
        token = page.get("NextToken")
        if not token:
            return


@wrap_aws_errors
def _instances_page(ctx: Context, client: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    return client.describe_instances(**kwargs)


def list_instances(ctx: Context, limit: int | None = None) -> list[Instance]:
    """Eager variant for the CLI, sorted by what the user sees first: the name."""
    found = sorted(iter_instances(ctx), key=lambda one: one.label.casefold())
    return found if limit is None else found[:limit]


def get_instance(ctx: Context, identifier: str) -> Instance:
    """One instance by id or ARN. Raises `LookupError` when there is no such one."""
    for one in iter_instances(ctx, ids=[identifier]):
        return one
    raise LookupError(f"no EC2 instance {instance_id_of(identifier)!r} in this region")


# --- the power operations --------------------------------------------------


def power(ctx: Context, verb: str, identifier: str, force: bool = False) -> str:
    """Start, stop or reboot an instance. Returns a sentence about what happened.

    The state is read first and `Instance.refuses()` is asked, so "it is already
    running" is a sentence rather than a silent no-op, and "it is still starting"
    is a sentence rather than an `IncorrectInstanceState` code.

    `force` is only meaningful for a stop, where it is the equivalent of pulling
    the plug - it can corrupt a filesystem, so it is never the default.
    """
    if verb not in VERBS:
        raise ValueError(f"unknown EC2 operation {verb!r} - one of {', '.join(VERBS)}")
    one = get_instance(ctx, identifier)
    refusal = one.refuses(verb)
    if refusal:
        raise ValueError(refusal)
    ctx.require_write(f"{verb} {one.label}")
    kwargs: dict[str, Any] = {"InstanceIds": [one.instance_id]}
    if verb == "stop" and force:
        kwargs["Force"] = True
    _power_call(ctx, VERBS[verb], kwargs)
    return f"{one.label} ({one.instance_id}): {verb} requested"


@wrap_aws_errors
def _power_call(ctx: Context, operation: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    return getattr(_client(ctx), operation)(**kwargs)


def _self_check() -> None:
    """The AWS shapes that would otherwise be found at runtime."""
    raw = {
        "InstanceId": "i-0abc1234",
        "InstanceType": "t3.micro",
        "State": {"Name": "running", "Code": 16},
        "Tags": [{"Key": "Name", "Value": "web-01"}],
        "PrivateIpAddress": "10.0.0.5",
        "Placement": {"AvailabilityZone": "eu-central-1a"},
    }
    one = _instance_from(raw)
    assert one.instance_id == "i-0abc1234" and one.name == "web-01"
    assert one.running and one.availability_zone == "eu-central-1a"
    # A stopped instance has no public IP and no placement worth reading, and a
    # pending one has no IP at all - neither may crash.
    bare = _instance_from({"InstanceId": "i-1", "State": {"Name": "pending"}})
    assert bare.public_ip == "" and bare.availability_zone == "" and not bare.settled
    assert _instance_from({}).instance_id == ""

    # Terminate is deliberately not offered.
    assert set(VERBS) == {"start", "stop", "reboot"}, VERBS
    assert "terminate" not in VERBS

    assert _moment("2026-08-01") is None and _moment(None) is None
    print("[OK] ec2 self-check passed")


if __name__ == "__main__":
    _self_check()
