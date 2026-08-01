"""What F9 offers on an EC2 instance, and what the preview pane shows.

Both are published through pluggy hooks, so the tree and the F9 menu never import
this module - they only ever see `Action` and `PreviewTab` objects.

**These are the first F9 actions that really change something.** Every earlier
plugin either read (`ecr.config`) or handed over a command (`lambda.invoke`,
`logs` tail, `ecr.cleanup`); start / stop / reboot do the thing. That is only
acceptable because all three are reversible, they are marked `destructive` so the
confirm dialog runs first (where "no" is the default), `Context.require_write`
still has the last word, and `core/ec2.power` refuses on the live state before
anything is sent. **Terminate is deliberately absent** - it is not reversible.

`applies_to` only asks "is this an instance?", not "is it running?": the row in
the tree is whatever Cloud Control returned, possibly minutes old, and hiding
`Stop` because a stale row said `stopped` would be worse than offering it and
having `power()` explain. The state check belongs where the state is fresh.
"""

from __future__ import annotations

from clitka.core import ec2
from clitka.core import preview as pv
from clitka.core.actions import Action, ActionResult, ResourceRef
from clitka.core.context import Context
from clitka.core.ec2model import instance_id_of, stamp

TYPE_NAME = "AWS::EC2::Instance"


def is_instance(ref: ResourceRef) -> bool:
    return ref.type_name == TYPE_NAME


def instance_id(ref: ResourceRef) -> str:
    """The instance id. Cloud Control identifies an instance *by* its id."""
    raw = ref.identifier or str(ref.row.get("InstanceId", ""))
    return instance_id_of(raw)


def _lines(pairs: list[tuple[str, str]]) -> str:
    """Label/value pairs as an aligned block - the shape every tab here uses."""
    if not pairs:
        return "[dim](nothing to show)[/dim]"
    width = max(len(label) for label, _ in pairs)
    return "\n".join(f"[dim]{label:<{width}}[/dim]  {value}" for label, value in pairs)


def show_details(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: the instance as `DescribeInstances` reports it right now."""
    one = ec2.get_instance(ctx, instance_id(ref))
    pairs = [
        ("state", one.state),
        ("type", one.instance_type),
        ("private ip", one.private_ip or "-"),
        ("public ip", one.public_ip or "-"),
        ("zone", one.availability_zone or "-"),
        ("vpc", one.vpc_id or "-"),
        ("subnet", one.subnet_id or "-"),
        ("key", one.key_name or "-"),
        ("platform", one.platform or "-"),
        ("launched", stamp(one.launched) or "-"),
    ]
    if one.state_reason:
        pairs.insert(1, ("reason", one.state_reason))
    return ActionResult(f"{one.label} - details", _lines(pairs))


def _run(ctx: Context, ref: ResourceRef, verb: str) -> ActionResult:
    """One power operation, as an F9 result.

    A refusal is a *result*, not an exception: "it is already running" is an
    answer to the user's question, not a failure of the app. `reload=True` so the
    branch refetches and the new state is visible.
    """
    who = instance_id(ref)
    try:
        return ActionResult(f"{verb} {who}", ec2.power(ctx, verb, who), reload=True)
    except ValueError as exc:  # the state refused it - see core/ec2model.refuses
        return ActionResult(f"{verb} {who}", f"[dim]Not done: {exc}[/dim]")


def do_start(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: start this instance."""
    return _run(ctx, ref, "start")


def do_stop(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: stop this instance. The OS is asked politely - never `Force`."""
    return _run(ctx, ref, "stop")


def do_reboot(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: reboot this instance."""
    return _run(ctx, ref, "reboot")


# The ids are namespaced, so two plugins can never collide in the F9 menu.
ACTIONS: tuple[Action, ...] = (
    Action(
        id="ec2.details",
        label="Details",
        run=show_details,
        # NOT `d`: the baseline `resources.delete` already claims it on every type
        # with an identifier, and `ActionMenu.on_key` runs the *first* action whose
        # key matches. A test asserts no key here collides with that baseline.
        key="e",
        applies_to=is_instance,
    ),
    Action(
        id="ec2.start",
        label="Start",
        run=do_start,
        key="1",
        applies_to=is_instance,
        destructive=True,  # it costs money and it changes state - ask first
    ),
    Action(
        id="ec2.stop",
        label="Stop",
        run=do_stop,
        key="0",
        applies_to=is_instance,
        destructive=True,
    ),
    Action(
        id="ec2.reboot",
        label="Reboot",
        run=do_reboot,
        key="b",
        applies_to=is_instance,
        destructive=True,
    ),
)


def build_details_tab(ctx: Context, ref: ResourceRef) -> str:
    """The `Instance` preview tab - the same block F9 shows, beside the tree."""
    return show_details(ctx, ref).body


PREVIEWS: tuple[pv.PreviewTab, ...] = (
    pv.PreviewTab(
        id="ec2.details",
        label="Instance",
        build=build_details_tab,
        applies_to=is_instance,
        lazy=True,  # it calls DescribeInstances
    ),
)


def _self_check() -> None:
    ref = ResourceRef.from_row(TYPE_NAME, {"identifier": "i-0abc"})
    assert is_instance(ref)
    assert not is_instance(ResourceRef.from_row("AWS::S3::Bucket", {}))
    assert instance_id(ref) == "i-0abc"
    # Cloud Control sometimes reports the id as a property, or as an ARN.
    assert instance_id(ResourceRef(TYPE_NAME, "", {"InstanceId": "i-other"})) == "i-other"
    arn = "arn:aws:ec2:eu-central-1:1:instance/i-from-arn"
    assert instance_id(ResourceRef(TYPE_NAME, arn, {})) == "i-from-arn"

    ids = [action.id for action in ACTIONS]
    keys = [action.key for action in ACTIONS]
    assert len(set(ids)) == len(ids) and len(set(keys)) == len(keys), keys
    assert all(action.applies_to(ref) for action in ACTIONS)

    # Every action that changes the instance must go through the confirm dialog,
    # and the read-only one must not.
    changes = {"ec2.start", "ec2.stop", "ec2.reboot"}
    for action in ACTIONS:
        assert action.destructive == (action.id in changes), action.id
    # Terminate must never appear here, whatever else does.
    assert not any("terminate" in action.id for action in ACTIONS)
    # The three verbs offered must be exactly the three core/ec2 implements.
    assert {one.split(".", 1)[1] for one in changes} == set(ec2.VERBS)

    # The baseline `resources.*` actions apply to an instance too, and the menu
    # runs the FIRST action whose key matches - so a shared key would mean
    # pressing `d` for "Details" deleted the instance instead. This is the whole
    # reason `ec2.details` is on `e`.
    from clitka.services.resources.actions import ACTIONS as BASELINE

    taken = {action.key for action in BASELINE if action.applies_to(ref)}
    assert not (set(keys) & taken), f"key collision with resources.*: {set(keys) & taken}"

    assert _lines([("a", "1"), ("bbb", "2")]).count("\n") == 1
    assert "nothing to show" in _lines([])

    assert [tab.id for tab in PREVIEWS] == ["ec2.details"]
    assert all(tab.lazy and tab.matches_type(TYPE_NAME) for tab in PREVIEWS)
    print("[OK] ec2 actions self-check passed")


if __name__ == "__main__":
    _self_check()
