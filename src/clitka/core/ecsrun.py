"""ECS tasks - the listing that makes `x` reachable, and the shell that follows.

The task half of `core/ecs.py`, split for the 8 kB rule; `core/ecs.py` re-exports
everything here, so nothing imports this module directly. The boto3-free `Task` /
`Container` and the four exec refusals live in `core/ecstask.py`.

Two things about `ListTasks` shaped this module:

- **It only shows RUNNING tasks.** Without `desiredStatus`, the task that crashed
  a minute ago - exactly the one worth looking at - is invisible. And
  `desiredStatus` takes *one* value, so "running plus recently stopped" is two
  calls, which is what `list_tasks(include_stopped=True)` makes.
- **A `serviceName` filter is a different parameter from a family filter**, and
  passing an ARN where the name is expected is rejected - hence `name_of_arn`.

`shell_for()` is the one place that turns a task into a `Handoff`, so the CLI, the
F9 action and the tree's `x` key all refuse an impossible exec identically.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from clitka.core import handoff as ho
from clitka.core.context import Context
from clitka.core.ecsmodel import moment, name_of_arn
from clitka.core.ecstask import Container, Task, task_id_of
from clitka.core.errors import wrap_aws_errors

__all__ = [
    "PAGE",
    "TASK_BATCH",
    "Container",
    "Task",
    "get_task",
    "iter_tasks",
    "list_tasks",
    "shell_for",
    "task_id_of",
]

PAGE = 50
TASK_BATCH = 100  # the DescribeTasks limit, which is the API's
DEFAULT_SHELL = "/bin/sh"


def _client(ctx: Context) -> Any:
    return ctx.client("ecs")


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _container_from(raw: dict[str, Any]) -> Container:
    """One `containers[]` entry, with its execute-command agent status folded in.

    The managed agent is what an exec actually talks to, and it lives in a nested
    list that is absent entirely on a task without execute-command - which is why
    `agent_status` defaults to "" rather than being assumed present.
    """
    agent = ""
    for managed in raw.get("managedAgents") or []:
        if str(managed.get("name", "")) == "ExecuteCommandAgent":
            agent = str(managed.get("lastStatus", ""))
            break
    code = raw.get("exitCode")
    return Container(
        name=str(raw.get("name", "")),
        status=str(raw.get("lastStatus", "")),
        image=str(raw.get("image", "")),
        exit_code=int(code) if isinstance(code, int) else None,
        reason=str(raw.get("reason", "")),
        agent_status=agent,
    )


def _task_from(raw: dict[str, Any]) -> Task:
    return Task(
        arn=str(raw.get("taskArn", "")),
        cluster=str(raw.get("clusterArn", "")),
        last_status=str(raw.get("lastStatus", "")),
        desired_status=str(raw.get("desiredStatus", "")),
        launch_type=str(raw.get("launchType", "")),
        task_definition=str(raw.get("taskDefinitionArn", "")),
        group=str(raw.get("group", "")),
        started_at=moment(raw.get("startedAt")),
        stopped_reason=str(raw.get("stoppedReason", "")),
        cpu=str(raw.get("cpu", "")),
        memory=str(raw.get("memory", "")),
        availability_zone=str(raw.get("availabilityZone", "")),
        platform_version=str(raw.get("platformVersion", "")),
        exec_enabled=bool(raw.get("enableExecuteCommand", False)),
        containers=tuple(_container_from(one) for one in raw.get("containers", []) or ()),
    )


def iter_tasks(
    ctx: Context,
    cluster: str,
    service: str = "",
    desired_status: str = "RUNNING",
    page_size: int = PAGE,
) -> Iterator[Task]:
    """Yield the cluster's tasks, optionally only one service's.

    `desired_status` is the one that matters - pass `"STOPPED"` for the tasks that
    died, or "" for the API's own default.
    """
    ecs = _client(ctx)
    kwargs: dict[str, Any] = {"cluster": cluster, "maxResults": page_size}
    if service:
        # ListTasks wants the service *name*; an ARN here is rejected.
        kwargs["serviceName"] = name_of_arn(service)
    if desired_status:
        kwargs["desiredStatus"] = desired_status
    token: str | None = None
    while True:
        if token:
            kwargs["nextToken"] = token
        page = _tasks_page(ctx, ecs, kwargs)
        arns = [str(one) for one in page.get("taskArns", [])]
        for batch in _chunks(arns, TASK_BATCH):
            for raw in _describe_tasks(ctx, ecs, cluster, batch).get("tasks", []):
                yield _task_from(raw)
        token = page.get("nextToken")
        if not token:
            return


@wrap_aws_errors
def _tasks_page(ctx: Context, ecs: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    return ecs.list_tasks(**kwargs)


@wrap_aws_errors
def _describe_tasks(ctx: Context, ecs: Any, cluster: str, arns: list[str]) -> dict[str, Any]:
    # TAGS is not asked for: it needs a separate permission and shows nothing here.
    return ecs.describe_tasks(cluster=cluster, tasks=arns)


def list_tasks(
    ctx: Context,
    cluster: str,
    service: str = "",
    include_stopped: bool = False,
    limit: int | None = None,
) -> list[Task]:
    """Running tasks, plus the recently stopped ones on request - running first."""
    found = list(iter_tasks(ctx, cluster, service, desired_status="RUNNING"))
    if include_stopped:
        found += list(iter_tasks(ctx, cluster, service, desired_status="STOPPED"))
    found.sort(key=lambda one: (not one.running, one.label.casefold()))
    return found if limit is None else found[:limit]


def get_task(ctx: Context, cluster: str, identifier: str) -> Task:
    """One task by id or ARN. The cluster is required - ECS scopes tasks by it."""
    for raw in _describe_tasks(ctx, _client(ctx), cluster, [identifier]).get("tasks", []):
        return _task_from(raw)
    wanted = task_id_of(identifier)
    raise LookupError(f"no ECS task {wanted!r} in cluster {name_of_arn(cluster)!r}")


def shell_for(
    ctx: Context,
    cluster: str,
    identifier: str,
    container: str = "",
    command: str = DEFAULT_SHELL,
) -> ho.Handoff:
    """The `ecs execute-command` handoff for one task, or `ValueError` with why not.

    **The task is described first, every time.** A row in the tree can be minutes
    old, and `refuses_exec()` needs the live agent status - which is the whole
    point: the complaint has to arrive *before* the app suspends, not as a wall of
    `TargetNotConnectedException` after it.
    """
    task = get_task(ctx, cluster, identifier)
    refusal = task.refuses_exec(container)
    if refusal:
        raise ValueError(refusal)
    target = task.container(container)
    name = target.name if target else ""
    return ho.ecs_exec(
        ctx,
        task.cluster_name or name_of_arn(cluster),
        task.arn or identifier,
        container=name,
        command=command,
    )


AGENT_UP = [{"name": "ExecuteCommandAgent", "lastStatus": "RUNNING"}]


def _self_check() -> None:
    """The AWS shapes that would otherwise be found at runtime."""
    # The managed agent is nested behind other agents, and absent altogether on a
    # task without execute-command.
    others = [{"name": "SomethingElse", "lastStatus": "RUNNING"}, *AGENT_UP]
    box = {"name": "app", "lastStatus": "RUNNING", "managedAgents": others}
    assert _container_from(box).can_exec
    assert _container_from({"name": "app", "lastStatus": "RUNNING"}).agent_status == ""
    # An exit code of 0 must survive as 0, not become None.
    assert _container_from({"name": "a", "exitCode": 0}).exit_code == 0
    assert _container_from({"name": "a"}).exit_code is None

    task = _task_from(
        {
            "taskArn": "arn:aws:ecs:eu-central-1:1:task/prod/abc123",
            "lastStatus": "RUNNING",
            "enableExecuteCommand": True,
            "group": "service:api",
            "containers": [{"name": "app", "lastStatus": "RUNNING", "managedAgents": AGENT_UP}],
        }
    )
    assert task.refuses_exec() == "" and task.service == "api"
    # A task described with no containers at all must not crash.
    assert _task_from({"taskArn": "a"}).containers == ()

    assert list(_chunks(["a", "b", "c"], 2)) == [["a", "b"], ["c"]]
    assert TASK_BATCH == 100 and DEFAULT_SHELL.startswith("/")
    print("[OK] ecs run self-check passed")


if __name__ == "__main__":
    _self_check()
