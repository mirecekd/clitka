"""What F9 offers on an ECS cluster, service or task, and the preview tabs.

Three resource types, which makes this the first plugin whose actions have to ask
*which* type they are looking at - and the first that can be reached both from a
Cloud Control branch (`AWS::ECS::Cluster` / `::Service`, which the tree lists) and
from `clitka ecs tasks` (a task, which Cloud Control cannot list at all).

**Opening a shell is not an F9 action** - it is the `x` key, for the same reason as
the live tail: `x` already owns the terminal handoff on both resource screens and
an `Action` returns one finished result. F9's `Shell (how to)` prints the command,
and on a task it prints *why not* first when `refuses_exec()` has an opinion.

The keys are checked against the baseline `resources.*` (which claims `y j i d` on
anything with an identifier) and against `ec2.*`/`ecr.*` - see the test.
"""

from __future__ import annotations

from clitka.core import ecs
from clitka.core import preview as pv
from clitka.core.actions import Action, ActionResult, ResourceRef
from clitka.core.context import Context
from clitka.core.ecsmodel import cluster_of_arn, name_of_arn
from clitka.core.ecsrun import DEFAULT_SHELL, shell_for
from clitka.core.ecstask import task_id_of

CLUSTER = "AWS::ECS::Cluster"
SERVICE = "AWS::ECS::Service"
TASK = "AWS::ECS::Task"
"""Not a real Cloud Control type - CLITKA's own, so a task can carry a ResourceRef."""


def is_cluster(ref: ResourceRef) -> bool:
    return ref.type_name == CLUSTER


def is_service(ref: ResourceRef) -> bool:
    return ref.type_name == SERVICE


def is_task(ref: ResourceRef) -> bool:
    return ref.type_name == TASK


def cluster_name(ref: ResourceRef) -> str:
    """The cluster this ref is about, whichever of the three types it is.

    A cluster names itself; a service or a task carries its cluster in the row, or
    failing that inside its own ARN (`.../service/<cluster>/<name>`).
    """
    if is_cluster(ref):
        return name_of_arn(ref.identifier or str(ref.row.get("ClusterName", "")))
    named = ref.row.get("Cluster") or ref.row.get("cluster") or ref.row.get("ClusterName")
    if named:
        return name_of_arn(str(named))
    # Last resort: the cluster is inside the resource's own ARN.
    return cluster_of_arn(ref.identifier)


def _lines(pairs: list[tuple[str, str]]) -> str:
    """Label/value pairs as an aligned block - the shape every tab here uses."""
    if not pairs:
        return "[dim](nothing to show)[/dim]"
    width = max(len(label) for label, _ in pairs)
    return "\n".join(f"[dim]{label:<{width}}[/dim]  {value}" for label, value in pairs)


def _table(rows: list[list[str]]) -> str:
    """A fixed-width block, because a preview tab is plain text."""
    if not rows:
        return "[dim](none)[/dim]"
    widths = [max(len(row[col]) for row in rows) for col in range(len(rows[0]))]
    out = ["  ".join(cell.ljust(widths[col]) for col, cell in enumerate(row)) for row in rows]
    out.insert(1, "  ".join("-" * one for one in widths))
    return "\n".join(out)


# --- clusters --------------------------------------------------------------


def show_cluster(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: the cluster as `DescribeClusters` reports it right now."""
    one = ecs.get_cluster(ctx, cluster_name(ref))
    pairs = [
        ("status", one.status),
        ("kind", one.kind or "-"),
        ("services", str(one.active_services)),
        ("running tasks", str(one.running_tasks)),
        ("pending tasks", str(one.pending_tasks)),
        ("instances", str(one.container_instances)),
        ("providers", ", ".join(one.capacity_providers) or "-"),
    ]
    return ActionResult(f"{one.label} - cluster", _lines(pairs))


def show_services(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: every service in the cluster, and whether it is fully up."""
    cluster = cluster_name(ref)
    found = ecs.list_services(ctx, cluster)
    rows = [["SERVICE", "DESIRED", "RUNNING", "LAUNCH", "HEALTH"]]
    rows += [
        [one.label, str(one.desired), str(one.running), one.launch_type.lower(), one.health]
        for one in found
    ]
    return ActionResult(f"{cluster} - {len(found)} services", _table(rows))


def show_tasks(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: the running tasks of a cluster (or of one service), and their exec state.

    This is the only route to a task in the whole app: Cloud Control has no
    `AWS::ECS::Task`, so nothing else can list one.
    """
    cluster = cluster_name(ref)
    service = ref.identifier if is_service(ref) else ""
    found = ecs.list_tasks(ctx, cluster, name_of_arn(service))
    rows = [["TASK", "NAME", "STATUS", "DEFINITION", "EXEC"]]
    rows += [
        [one.short_id, one.service or one.family, one.last_status.lower(),
         one.task_definition_label, "yes" if not one.refuses_exec() else "no"]
        for one in found
    ]  # fmt: skip
    body = _table(rows)
    if not found:
        body += "\n\n[dim]Nothing running. `clitka ecs tasks <cluster> --stopped` "
        body += "shows the ones that died.[/dim]"
    return ActionResult(f"{cluster} - {len(found)} running tasks", body)


# --- services --------------------------------------------------------------


def show_service(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: one service - the desired count against what really runs."""
    cluster = cluster_name(ref)
    one = ecs.get_service(ctx, cluster, ref.identifier)
    pairs = [
        ("cluster", one.cluster_name or cluster),
        ("status", one.status),
        ("health", one.health),
        ("desired", str(one.desired)),
        ("running", str(one.running)),
        ("pending", str(one.pending)),
        ("launch type", one.launch_type or "-"),
        ("definition", one.task_definition_label or "-"),
    ]
    return ActionResult(f"{one.label} - service", _lines(pairs))


# --- tasks ----------------------------------------------------------------


def show_task(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: one task, its containers, and whether a shell would open in it."""
    cluster = cluster_name(ref)
    one = ecs.get_task(ctx, cluster, ref.identifier)
    refusal = one.refuses_exec()
    pairs = [
        ("cluster", one.cluster_name or cluster),
        ("service", one.service or "-"),
        ("status", one.last_status),
        ("definition", one.task_definition_label or "-"),
        ("launch type", one.launch_type or "-"),
        ("cpu / memory", f"{one.cpu or '-'} / {one.memory or '-'}"),
        ("zone", one.availability_zone or "-"),
        ("exec", f"[red]{refusal}[/red]" if refusal else "[green]ready[/green]"),
    ]
    if one.stopped_reason:
        pairs.insert(3, ("reason", one.stopped_reason))
    rows = [["CONTAINER", "STATUS", "IMAGE", "EXEC", "EXIT"]]
    rows += [
        [box.name, box.status.lower(), box.image_label, "yes" if box.can_exec else "no",
         "" if box.exit_code is None else str(box.exit_code)]
        for box in one.containers
    ]  # fmt: skip
    return ActionResult(f"{one.label} - task", f"{_lines(pairs)}\n\n{_table(rows)}")


def show_shell_hint(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: how to open a shell here - and why it would not work, if it would not.

    Opening it is `x`, not this. This hands over the command, exactly as
    `lambda.invoke` and the logs tail do.
    """
    cluster = cluster_name(ref)
    try:
        handoff = shell_for(ctx, cluster, ref.identifier)
    except ValueError as exc:  # refuses_exec had an opinion - that IS the answer
        body = f"[red]No shell here:[/red] {exc}"
        return ActionResult(f"shell in {task_id_of(ref.identifier)[:12]}", body)
    lines = [
        "Press [b]x[/b] on this task to open it here, or run:",
        "",
        f"  {handoff.command()}",
        "",
        f"[dim]{handoff.note}[/dim]",
        f"[dim]The default command is {DEFAULT_SHELL} - use `clitka ecs exec ... "
        "--command /bin/bash` for another.[/dim]",
    ]
    return ActionResult(f"shell in {task_id_of(ref.identifier)[:12]}", "\n".join(lines))


# The ids are namespaced; the *keys* are not, so every one here was checked
# against `resources.*` (y j i d), `ec2.*` (e 1 0 b) and `ecr.*` (c m u g).
ACTIONS: tuple[Action, ...] = (
    Action(id="ecs.cluster", label="Cluster", run=show_cluster, key="k", applies_to=is_cluster),
    Action(id="ecs.services", label="Services", run=show_services, key="v", applies_to=is_cluster),
    Action(
        id="ecs.tasks",
        label="Running tasks",
        run=show_tasks,
        key="a",
        # On a service this lists just that service's tasks - the same question,
        # one level down, so it is the same action rather than a second one.
        applies_to=lambda ref: is_cluster(ref) or is_service(ref),
    ),
    Action(id="ecs.service", label="Service", run=show_service, key="w", applies_to=is_service),
    Action(id="ecs.task", label="Task details", run=show_task, key="t", applies_to=is_task),
    Action(
        id="ecs.shell",
        label="Shell (how to)",
        run=show_shell_hint,
        key="h",
        applies_to=is_task,
    ),
)


def build_cluster_tab(ctx: Context, ref: ResourceRef) -> str:
    return show_tasks(ctx, ref).body


def build_service_tab(ctx: Context, ref: ResourceRef) -> str:
    return show_service(ctx, ref).body


def build_task_tab(ctx: Context, ref: ResourceRef) -> str:
    return show_task(ctx, ref).body


PREVIEWS: tuple[pv.PreviewTab, ...] = (
    pv.PreviewTab(
        id="ecs.tasks",
        label="Tasks",
        build=build_cluster_tab,
        applies_to=lambda ref: is_cluster(ref) or is_service(ref),
        lazy=True,  # it calls ListTasks + DescribeTasks
    ),
    pv.PreviewTab(
        id="ecs.service",
        label="Service",
        build=build_service_tab,
        applies_to=is_service,
        lazy=True,
    ),
    pv.PreviewTab(
        id="ecs.task",
        label="Task",
        build=build_task_tab,
        applies_to=is_task,
        lazy=True,
    ),
)


def _self_check() -> None:
    cluster = ResourceRef.from_row(CLUSTER, {"identifier": "prod"})
    service = ResourceRef(SERVICE, "arn:aws:ecs:x:1:service/prod/api", {})
    task = ResourceRef(TASK, "arn:aws:ecs:x:1:task/prod/abc123", {})
    assert is_cluster(cluster) and is_service(service) and is_task(task)
    assert not is_cluster(service) and not is_task(service)

    # The cluster has to be findable from all three, or an exec cannot be built.
    assert cluster_name(cluster) == "prod"
    assert cluster_name(service) == "prod", cluster_name(service)
    assert cluster_name(task) == "prod", cluster_name(task)
    # And from a row that names it instead of an ARN that carries it.
    assert cluster_name(ResourceRef(TASK, "abc", {"Cluster": "dev"})) == "dev"

    ids = [action.id for action in ACTIONS]
    assert len(set(ids)) == len(ids), ids
    # Nothing here mutates, so nothing here is destructive.
    assert not any(action.destructive for action in ACTIONS)

    # The F9 key namespace is global per resource: check every type this plugin
    # claims against everything else that applies to the same ref.
    from clitka.services.ec2.actions import ACTIONS as EC2
    from clitka.services.ecr.actions import ACTIONS as ECR
    from clitka.services.resources.actions import ACTIONS as BASELINE

    for ref in (cluster, service, task):
        keys = [
            one.key for one in (*ACTIONS, *BASELINE, *EC2, *ECR) if one.key and one.applies_to(ref)
        ]
        assert len(set(keys)) == len(keys), (ref.type_name, sorted(keys))

    assert _lines([("a", "1"), ("bbb", "2")]).count("\n") == 1
    assert "nothing to show" in _lines([])
    assert "(none)" in _table([])
    # A table gets its rule row between the head and the body.
    assert _table([["A", "B"], ["1", "2"]]).count("\n") == 2

    assert [tab.id for tab in PREVIEWS] == ["ecs.tasks", "ecs.service", "ecs.task"]
    assert all(tab.lazy for tab in PREVIEWS)
    print("[OK] ecs actions self-check passed")


if __name__ == "__main__":
    _self_check()
