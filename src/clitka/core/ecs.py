"""ECS clusters and services - the walk that Cloud Control cannot do at all.

Same shape as `core/ec2.py` and `core/ecr.py` - generators plus `wrap_aws_errors`,
row types in `core/ecsmodel.py`. The **task** half is `core/ecsrun.py` (8 kB rule)
and is re-exported here, so every caller only imports `core.ecs`.

**This is CLITKA's first listing Cloud Control cannot provide.** There is no
`AWS::ECS::Task` type, so an ECS task was unreachable from the tree by any route -
and the `x` handoff (`ecs execute-command`) had nothing to act on.

The one API fact that shaped both modules: **every list call returns ARNs only.**
`ListClusters` / `ListServices` / `ListTasks` hand back a page of ARNs and nothing
else, so each generator pairs the list with a `Describe*` whose batch limits are
the API's - **100 clusters, 10 services, 100 tasks**. Eleven service ARNs is a
validation error, not a slow call.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from clitka.core.context import Context
from clitka.core.ecsmodel import Cluster, Service, moment, name_of_arn
from clitka.core.ecsrun import (
    TASK_BATCH,
    Container,
    Task,
    get_task,
    iter_tasks,
    list_tasks,
    task_id_of,
)
from clitka.core.errors import wrap_aws_errors

__all__ = [
    "CLUSTER_BATCH", "PAGE", "SERVICE_BATCH", "TASK_BATCH",
    "Cluster", "Container", "Service", "Task", "chunks",
    "get_cluster", "get_service", "get_task",
    "iter_clusters", "iter_services", "iter_tasks",
    "list_clusters", "list_services", "list_tasks",
    "name_of_arn", "task_id_of",
]  # fmt: skip


PAGE = 50
# The `Describe*` batch limits, which are the API's and are not negotiable.
CLUSTER_BATCH = 100
SERVICE_BATCH = 10


def client(ctx: Context) -> Any:
    """The one ECS client both this module and `ecsrun` use."""
    return ctx.client("ecs")


def chunks(items: list[str], size: int) -> Iterator[list[str]]:
    """`items` in batches of at most `size` - the describe limits are hard."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


# --- clusters --------------------------------------------------------------


def _cluster_from(raw: dict[str, Any]) -> Cluster:
    """One `clusters[]` entry as a `Cluster`.

    A capacity provider can be named in two places (the plain list and the default
    strategy) and neither is always present, so both are merged.
    """
    providers = raw.get("capacityProviders") or []
    strategy = raw.get("defaultCapacityProviderStrategy") or []
    named = [str(one) for one in providers] + [
        str(one.get("capacityProvider", "")) for one in strategy if one.get("capacityProvider")
    ]
    return Cluster(
        name=str(raw.get("clusterName", "")),
        arn=str(raw.get("clusterArn", "")),
        status=str(raw.get("status", "")),
        running_tasks=int(raw.get("runningTasksCount", 0) or 0),
        pending_tasks=int(raw.get("pendingTasksCount", 0) or 0),
        active_services=int(raw.get("activeServicesCount", 0) or 0),
        container_instances=int(raw.get("registeredContainerInstancesCount", 0) or 0),
        capacity_providers=tuple(dict.fromkeys(one for one in named if one)),
    )


def iter_clusters(ctx: Context, page_size: int = PAGE) -> Iterator[Cluster]:
    """Yield every cluster - ARNs listed, then described in batches of 100."""
    ecs = client(ctx)
    kwargs: dict[str, Any] = {"maxResults": page_size}
    token: str | None = None
    while True:
        if token:
            kwargs["nextToken"] = token
        page = _clusters_page(ctx, ecs, kwargs)
        arns = [str(one) for one in page.get("clusterArns", [])]
        for batch in chunks(arns, CLUSTER_BATCH):
            for raw in _describe_clusters(ctx, ecs, batch).get("clusters", []):
                yield _cluster_from(raw)
        token = page.get("nextToken")
        if not token:
            return


@wrap_aws_errors
def _clusters_page(ctx: Context, ecs: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    return ecs.list_clusters(**kwargs)


@wrap_aws_errors
def _describe_clusters(ctx: Context, ecs: Any, arns: list[str]) -> dict[str, Any]:
    # ATTACHMENTS is not asked for: it is large and nothing here shows it.
    return ecs.describe_clusters(clusters=arns, include=["SETTINGS"])


def list_clusters(ctx: Context, limit: int | None = None) -> list[Cluster]:
    """Eager variant for the CLI, sorted by name."""
    found = sorted(iter_clusters(ctx), key=lambda one: one.label.casefold())
    return found if limit is None else found[:limit]


def get_cluster(ctx: Context, identifier: str) -> Cluster:
    """One cluster by name or ARN - `describe_clusters` takes either."""
    for raw in _describe_clusters(ctx, client(ctx), [identifier]).get("clusters", []):
        return _cluster_from(raw)
    raise LookupError(f"no ECS cluster {name_of_arn(identifier)!r} in this region")


# --- services --------------------------------------------------------------


def _service_from(raw: dict[str, Any]) -> Service:
    return Service(
        name=str(raw.get("serviceName", "")),
        arn=str(raw.get("serviceArn", "")),
        cluster=str(raw.get("clusterArn", "")),
        status=str(raw.get("status", "")),
        desired=int(raw.get("desiredCount", 0) or 0),
        running=int(raw.get("runningCount", 0) or 0),
        pending=int(raw.get("pendingCount", 0) or 0),
        launch_type=str(raw.get("launchType", "")),
        task_definition=str(raw.get("taskDefinition", "")),
        created=moment(raw.get("createdAt")),
        role_arn=str(raw.get("roleArn", "")),
    )


def iter_services(ctx: Context, cluster: str, page_size: int = PAGE) -> Iterator[Service]:
    """Yield the cluster's services. `DescribeServices` takes **ten** at a time."""
    ecs = client(ctx)
    kwargs: dict[str, Any] = {"cluster": cluster, "maxResults": page_size}
    token: str | None = None
    while True:
        if token:
            kwargs["nextToken"] = token
        page = _services_page(ctx, ecs, kwargs)
        arns = [str(one) for one in page.get("serviceArns", [])]
        for batch in chunks(arns, SERVICE_BATCH):
            for raw in _describe_services(ctx, ecs, cluster, batch).get("services", []):
                yield _service_from(raw)
        token = page.get("nextToken")
        if not token:
            return


@wrap_aws_errors
def _services_page(ctx: Context, ecs: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    return ecs.list_services(**kwargs)


@wrap_aws_errors
def _describe_services(ctx: Context, ecs: Any, cluster: str, arns: list[str]) -> dict[str, Any]:
    return ecs.describe_services(cluster=cluster, services=arns)


def list_services(ctx: Context, cluster: str, limit: int | None = None) -> list[Service]:
    """Eager variant for the CLI, sorted by name."""
    found = sorted(iter_services(ctx, cluster), key=lambda one: one.label.casefold())
    return found if limit is None else found[:limit]


def get_service(ctx: Context, cluster: str, identifier: str) -> Service:
    """One service by name or ARN, inside a cluster."""
    described = _describe_services(ctx, client(ctx), cluster, [identifier])
    for raw in described.get("services", []):
        return _service_from(raw)
    wanted = name_of_arn(identifier)
    raise LookupError(f"no ECS service {wanted!r} in cluster {name_of_arn(cluster)!r}")


def _self_check() -> None:
    """The AWS shapes that would otherwise be found at runtime."""
    raw = {
        "clusterName": "prod",
        "runningTasksCount": 3,
        "capacityProviders": ["FARGATE"],
        "defaultCapacityProviderStrategy": [{"capacityProvider": "FARGATE_SPOT"}],
    }
    # Both places a capacity provider can be named are merged, without duplicates.
    assert _cluster_from(raw).capacity_providers == ("FARGATE", "FARGATE_SPOT")
    assert _cluster_from(raw).name == "prod" and _cluster_from(raw).running_tasks == 3
    assert _cluster_from({"clusterName": "bare"}).capacity_providers == ()
    assert _service_from({"serviceName": "api"}).desired == 0

    # The batch limits are the API's, and a short list is still one batch.
    assert list(chunks(["a", "b", "c"], 2)) == [["a", "b"], ["c"]]
    assert list(chunks([], 10)) == []
    assert (CLUSTER_BATCH, SERVICE_BATCH, TASK_BATCH) == (100, 10, 100)
    # The task half lives in ecsrun but must stay reachable through this module.
    assert all(callable(one) for one in (iter_tasks, list_tasks, get_task, task_id_of))
    print("[OK] ecs self-check passed")


if __name__ == "__main__":
    _self_check()
