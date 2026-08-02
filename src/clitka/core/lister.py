"""Child listings: what a plugin can hang UNDERNEATH a resource in the tree.

The hole this fills, reported by the owner (2026-08-01): **an ECS task could not be
reached by clicking**. Cloud Control has no `AWS::ECS::Task`, so `BranchLoader` -
which fetches every branch through `cloudcontrol.iter_resources` - could never fill
one, and `x` / F9 on a task were only reachable from the CLI. The F9 `Running
tasks` action printed them as text, which is not something you can put a cursor on.

So a plugin may now publish a `ChildLister`: "for a resource of type X, I can list
these children of type Y". The tree turns each one into a sub-branch under the
resource leaf, and because the children are ordinary `cloudcontrol.Resource`
objects, everything that already works on a leaf - the preview pane, F3, F9 and the
`x` handoff - works on them with no further plumbing.

Deliberately the same shape as `core/actions.Action` and `core/preview.PreviewTab`:
a filter predicate plus a plain synchronous callable the TUI runs on a thread.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from clitka.core.actions import ResourceRef
from clitka.core.cloudcontrol import Resource


@dataclass(frozen=True)
class ChildLister:
    """One sub-branch under a resource leaf.

    `list` may call AWS - the tree always runs it on a worker, and only when the
    sub-branch is actually expanded. It returns `cloudcontrol.Resource` objects
    even for a type Cloud Control knows nothing about, because that is the shape
    the whole tree, preview pane and action model already speak.
    """

    id: str
    label: str
    """What the sub-branch is called, e.g. "Tasks"."""
    child_type: str
    """The type name of what comes out, e.g. `AWS::ECS::Task`."""
    list: Callable[[Any, ResourceRef], list[Resource]]
    applies_to: Callable[[ResourceRef], bool] = lambda _ref: True


def available(listers: Sequence[ChildLister], ref: ResourceRef | None) -> list[ChildLister]:
    """The listers that apply to `ref`, in registration order.

    A broken `applies_to` is skipped rather than allowed to break the branch -
    the same rule as `actions.available` and `preview.available`.
    """
    if ref is None:
        return []
    out: list[ChildLister] = []
    for one in listers:
        try:
            if one.applies_to(ref):
                out.append(one)
        except Exception:
            continue
    return out


def registered() -> list[ChildLister]:
    """Every child lister published by every plugin."""
    from clitka.core import plugins

    return [one for one in plugins.listers() if isinstance(one, ChildLister)]


def _self_check() -> None:
    cluster = ResourceRef.from_row("AWS::ECS::Cluster", {"identifier": "prod"})
    bucket = ResourceRef.from_row("AWS::S3::Bucket", {"identifier": "b"})

    tasks = ChildLister(
        id="ecs.tasks",
        label="Tasks",
        child_type="AWS::ECS::Task",
        list=lambda _ctx, _ref: [Resource("AWS::ECS::Task", "abc", {})],
        applies_to=lambda ref: ref.type_name == "AWS::ECS::Cluster",
    )

    def explode(_ref: ResourceRef) -> bool:
        raise RuntimeError("applies_to is broken")

    broken = ChildLister("b", "B", "T", lambda _c, _r: [], applies_to=explode)

    assert [one.id for one in available([tasks, broken], cluster)] == ["ecs.tasks"]
    assert available([tasks], bucket) == []
    assert available([tasks], None) == []
    # The children are plain Cloud Control resources, even for a type it cannot list.
    made = tasks.list(None, cluster)
    assert made[0].type_name == "AWS::ECS::Task" and made[0].identifier == "abc"
    print("[OK] child lister self-check passed")


if __name__ == "__main__":
    _self_check()
