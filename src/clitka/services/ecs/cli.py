"""`clitka ecs` - the clusters -> services -> tasks walk, and a shell in a task.

The scriptable half, and the reason this plugin exists: **Cloud Control has no
`AWS::ECS::Task`**, so `clitka resources list` cannot show a task at all.

`exec` is the one command here that hands the terminal over, and it is not `aws ecs
execute-command` in a trench coat: it describes the task first and refuses with a
sentence when an exec cannot work, rather than letting the session-manager plugin
print `TargetNotConnectedException` at the user.
"""

from __future__ import annotations

from typing import Any

import typer

from clitka.core import ecs
from clitka.core.context import Context
from clitka.core.ecsrun import DEFAULT_SHELL, shell_for
from clitka.core.errors import ClitkaError
from clitka.core.output import OutputFormat, console, err_console, jsonable, render

app = typer.Typer(no_args_is_help=True, help="ECS: clusters, services, tasks and exec.")

CLUSTERS = ["identifier", "status", "services", "running", "pending", "kind"]
SERVICES = ["identifier", "desired", "running", "pending", "launch_type", "health"]
TASKS = ["identifier", "name", "status", "task_definition", "launch_type", "exec"]
CONTAINERS = ["identifier", "status", "image", "exec", "exit_code"]


def _ctx(typer_ctx: typer.Context) -> Context:
    state = typer_ctx.obj
    return state["context"] if state else Context.from_env()


def _fail(exc: Exception) -> typer.Exit:
    err_console.print(f"[ERROR] {exc}")
    return typer.Exit(1)


@app.command("clusters")
def clusters(
    typer_ctx: typer.Context,
    limit: int = typer.Option(None, "--limit", "-n", help="Stop after this many."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """List the clusters in the current region."""
    try:
        found = ecs.list_clusters(_ctx(typer_ctx), limit=limit)
    except ClitkaError as exc:
        raise _fail(exc) from exc
    render([one.row() for one in found], fmt=output, columns=CLUSTERS, title="clusters")


@app.command("services")
def services(
    typer_ctx: typer.Context,
    cluster: str = typer.Argument(..., help="Cluster name or ARN."),
    unhealthy: bool = typer.Option(False, "--unhealthy", help="Only those not fully running."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """List a cluster's services, with how many tasks really run."""
    try:
        found = ecs.list_services(_ctx(typer_ctx), cluster)
    except ClitkaError as exc:
        raise _fail(exc) from exc
    if unhealthy:
        found = [one for one in found if not one.healthy]
    render([one.row() for one in found], fmt=output, columns=SERVICES, title="services")


@app.command("tasks")
def tasks(
    typer_ctx: typer.Context,
    cluster: str = typer.Argument(..., help="Cluster name or ARN."),
    service: str = typer.Option("", "--service", "-s", help="Only this service's tasks."),
    stopped: bool = typer.Option(False, "--stopped", help="Include recently stopped ones."),
    limit: int = typer.Option(None, "--limit", "-n", help="Stop after this many."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """List a cluster's tasks. Running only, unless `--stopped` asks for both."""
    try:
        found = ecs.list_tasks(
            _ctx(typer_ctx), cluster, service, include_stopped=stopped, limit=limit
        )
    except ClitkaError as exc:
        raise _fail(exc) from exc
    render([one.row() for one in found], fmt=output, columns=TASKS, title="tasks")


@app.command("get")
def get(
    typer_ctx: typer.Context,
    cluster: str = typer.Argument(..., help="Cluster name or ARN."),
    task: str = typer.Argument(..., help="Task id or ARN."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """Show one task in full, including why an exec would or would not work."""
    try:
        one = ecs.get_task(_ctx(typer_ctx), cluster, task)
    except (ClitkaError, LookupError) as exc:
        raise _fail(exc) from exc
    detail: dict[str, Any] = {
        "task_id": one.task_id,
        "cluster": one.cluster_name,
        "service": one.service or "-",
        "status": one.last_status,
        "desired_status": one.desired_status,
        "stopped_reason": one.stopped_reason or "-",
        "task_definition": one.task_definition_label,
        "launch_type": one.launch_type,
        "cpu": one.cpu or "-",
        "memory": one.memory or "-",
        "availability_zone": one.availability_zone or "-",
        "platform_version": one.platform_version or "-",
        "started": one.started_at,
        "exec_enabled": one.exec_enabled,
        # The useful field: "" means a shell would open right now.
        "exec": one.refuses_exec() or "ready",
        "containers": [box.row() for box in one.containers],
    }
    render([jsonable(detail)], fmt=output, columns=None, title=one.label)


@app.command("containers")
def containers(
    typer_ctx: typer.Context,
    cluster: str = typer.Argument(..., help="Cluster name or ARN."),
    task: str = typer.Argument(..., help="Task id or ARN."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """List a task's containers and which of them an exec could land in."""
    try:
        one = ecs.get_task(_ctx(typer_ctx), cluster, task)
    except (ClitkaError, LookupError) as exc:
        raise _fail(exc) from exc
    rows = [box.row() for box in one.containers]
    render(rows, fmt=output, columns=CONTAINERS, title=f"{one.label} containers")


@app.command("exec")
def exec_(
    typer_ctx: typer.Context,
    cluster: str = typer.Argument(..., help="Cluster name or ARN."),
    task: str = typer.Argument(..., help="Task id or ARN."),
    container: str = typer.Option("", "--container", "-c", help="Which container to enter."),
    command: str = typer.Option(DEFAULT_SHELL, "--command", help="What to run inside it."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the command, do not run it."),
) -> None:
    """Open an interactive shell inside a running task.

    The task is described first, so an impossible exec is a sentence rather than
    the session-manager plugin's own error - which is the point of the command.
    """
    ctx = _ctx(typer_ctx)
    try:
        handoff = shell_for(ctx, cluster, task, container=container, command=command)
    except (ClitkaError, LookupError, ValueError) as exc:
        raise _fail(exc) from exc
    gone = handoff.unavailable()
    if gone:
        raise _fail(RuntimeError(gone))
    if dry_run:
        console.print(handoff.command(), highlight=False)
        return
    outcome = handoff.run()
    if not outcome.ok:
        raise _fail(RuntimeError(outcome.summary()))
