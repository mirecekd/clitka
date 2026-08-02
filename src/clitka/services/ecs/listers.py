"""What hangs UNDERNEATH an ECS cluster or service in the tree.

The owner's report (2026-08-01): **"I cannot get to any of the tasks by clicking."**
Quite right - Cloud Control has no `AWS::ECS::Task`, so `BranchLoader` could never
fill such a branch, and the F9 `Running tasks` action only ever *printed* them.
Printed text has no cursor, so `x` and the task's own F9 entries were unreachable
from the TUI entirely.

`core/lister.ChildLister` is the seam that fixes it, and this module is its first
user: a cluster grows `Services` and `Tasks` sub-branches, a service grows `Tasks`.
The children come out as ordinary `cloudcontrol.Resource` objects, so the preview
pane, F3, F9 and the `x` handoff all work on them without another line of plumbing.

Two things the rows must carry, or the rest of the plugin cannot do its job:

- **`Cluster`**, because `actions.cluster_name()` and `shellhost.task_and_cluster()`
  both read it, and every ECS call is scoped by cluster.
- **`Name`**, because `resname.name_of` is what the leaf leads with - and
  `abc123def456` is exactly as unhelpful as `i-0abc...` was on EC2.
"""

from __future__ import annotations

from typing import Any

from clitka.core import ecs
from clitka.core.actions import ResourceRef
from clitka.core.cloudcontrol import Resource
from clitka.core.context import Context
from clitka.core.ecsmodel import name_of_arn
from clitka.core.lister import ChildLister
from clitka.services.ecs.actions import CLUSTER, SERVICE, TASK, cluster_name, is_cluster, is_service


def task_resource(task: ecs.Task, cluster: str) -> Resource:
    """One `Task` as the tree's own row type.

    The identifier is the **ARN**, not the short id: it carries the cluster, so a
    row that somehow loses the `Cluster` property is still enough to exec into.
    """
    row: dict[str, Any] = dict(task.row())
    # Both are the Resource's own job now: `identifier` is a field and `name` is
    # derived from `Name`. Left in, they would be shown twice on the leaf.
    row.pop("identifier", None)
    row.pop("name", None)
    row["Name"] = f"{task.service or task.family or 'task'}  {task.short_id}"
    row["Cluster"] = task.cluster_name or name_of_arn(cluster)
    refusal = task.refuses_exec()
    row["exec"] = refusal or "ready"
    row["Containers"] = ", ".join(one.name for one in task.containers)
    return Resource(type_name=TASK, identifier=task.arn, properties=row)


def service_resource(service: ecs.Service, cluster: str) -> Resource:
    """One `Service` as a tree row - same type Cloud Control uses, filled by us.

    Cloud Control *can* list `AWS::ECS::Service`, but only as a flat top-level
    branch with no idea which cluster it belongs to. Under its own cluster it is
    both findable and already scoped, which is what makes the `Tasks` sub-branch
    underneath it work.
    """
    row: dict[str, Any] = dict(service.row())
    row.pop("identifier", None)
    row.pop("name", None)
    row["Name"] = service.label
    row["Cluster"] = service.cluster_name or name_of_arn(cluster)
    return Resource(type_name=SERVICE, identifier=service.arn or service.name, properties=row)


def list_services(ctx: Context, ref: ResourceRef) -> list[Resource]:
    """Every service in the cluster under the cursor."""
    cluster = cluster_name(ref)
    return [service_resource(one, cluster) for one in ecs.list_services(ctx, cluster)]


def list_tasks(ctx: Context, ref: ResourceRef) -> list[Resource]:
    """The cluster's running tasks - or just one service's, one level down.

    Running only. A stopped task is worth looking at but has no shell and no live
    state, so `clitka ecs tasks <cluster> --stopped` stays the way to see those
    rather than filling the tree with corpses.
    """
    cluster = cluster_name(ref)
    service = ref.identifier if is_service(ref) else ""
    found = ecs.list_tasks(ctx, cluster, name_of_arn(service))
    return [task_resource(one, cluster) for one in found]


LISTERS: tuple[ChildLister, ...] = (
    ChildLister(
        id="ecs.services",
        label="Services",
        child_type=SERVICE,
        list=list_services,
        applies_to=is_cluster,
    ),
    ChildLister(
        id="ecs.tasks",
        label="Tasks",
        child_type=TASK,
        # On a service this is that service's tasks - the same question one level
        # down, so it is the same lister rather than a second one.
        list=list_tasks,
        applies_to=lambda ref: is_cluster(ref) or is_service(ref),
    ),
)


def _self_check() -> None:
    from clitka.core.ecstask import RUNNING, Container

    arn = "arn:aws:ecs:eu-central-1:1:task/prod/abc123def456"
    up = Container("app", status=RUNNING, image="1.dkr.ecr.x/my-app:3", agent_status=RUNNING)
    task = ecs.Task(
        arn, last_status=RUNNING, exec_enabled=True, containers=(up,), group="service:api"
    )

    made = task_resource(task, "prod")
    assert made.type_name == TASK
    # The ARN, not the short id: it carries the cluster all on its own.
    assert made.identifier == arn
    # The three properties the rest of the plugin reads off a tree row.
    assert made.properties["Cluster"] == "prod"
    assert made.properties["exec"] == "ready"
    assert "abc123def456" in made.properties["Name"] and "api" in made.properties["Name"]
    # `identifier` must not survive as a property - the Resource owns it.
    assert "identifier" not in made.properties
    # And the leaf must lead with something a human recognises.
    assert made.name() == made.properties["Name"]

    # A ref built from this row is enough to find the cluster and the task again.
    ref = ResourceRef.from_row(TASK, made.row())
    assert cluster_name(ref) == "prod"
    from clitka.tui.shellhost import task_and_cluster

    assert task_and_cluster(ref) == (arn, "prod")

    # A refusal travels with the row rather than becoming a green "ready".
    dead = ecs.Task(arn, last_status="STOPPED")
    assert task_resource(dead, "prod").properties["exec"] != "ready"

    service = ecs.Service(name="api", arn="arn:aws:ecs:x:1:service/prod/api", cluster="prod")
    assert service_resource(service, "prod").properties["Cluster"] == "prod"

    cluster_ref = ResourceRef.from_row(CLUSTER, {"identifier": "prod"})
    assert all(one.applies_to(cluster_ref) for one in LISTERS)
    service_ref = ResourceRef(SERVICE, "arn:aws:ecs:x:1:service/prod/api", {})
    applies = [one.id for one in LISTERS if one.applies_to(service_ref)]
    assert applies == ["ecs.tasks"], applies
    # A task is a leaf: nothing hangs under it.
    assert not any(one.applies_to(ref) for one in LISTERS)
    print("[OK] ecs listers self-check passed")


if __name__ == "__main__":
    _self_check()
