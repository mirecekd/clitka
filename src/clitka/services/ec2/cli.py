"""`clitka ec2` - the instance listing and the three power operations.

The scriptable half. `list` is what `aws ec2 describe-instances` should have
printed: the `Name` tag first, the state next, and nothing else in the way.
"""

from __future__ import annotations

from typing import Any

import typer

from clitka.core import ec2
from clitka.core.context import Context
from clitka.core.errors import ClitkaError
from clitka.core.output import OutputFormat, console, err_console, jsonable, render

app = typer.Typer(no_args_is_help=True, help="EC2: instances, start, stop and reboot.")

COLUMNS = ["identifier", "name", "state", "type", "private_ip", "public_ip", "az"]


def _ctx(typer_ctx: typer.Context) -> Context:
    state = typer_ctx.obj
    return state["context"] if state else Context.from_env()


def _fail(exc: Exception) -> typer.Exit:
    err_console.print(f"[ERROR] {exc}")
    return typer.Exit(1)


@app.command("list")
def list_instances(
    typer_ctx: typer.Context,
    state: str = typer.Option(None, "--state", "-s", help="Only instances in this state."),
    limit: int = typer.Option(None, "--limit", "-n", help="Stop after this many instances."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """List the instances in the current region, by name."""
    ctx = _ctx(typer_ctx)
    try:
        found = ec2.list_instances(ctx, limit=limit)
    except ClitkaError as exc:
        raise _fail(exc) from exc
    if state:
        found = [one for one in found if one.state == state]
    render([one.row() for one in found], fmt=output, columns=COLUMNS, title="instances")


@app.command("get")
def get(
    typer_ctx: typer.Context,
    identifier: str = typer.Argument(..., help="Instance id or ARN."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """Show one instance in full."""
    ctx = _ctx(typer_ctx)
    try:
        one = ec2.get_instance(ctx, identifier)
    except (ClitkaError, LookupError) as exc:
        raise _fail(exc) from exc
    detail: dict[str, Any] = {
        "instance_id": one.instance_id,
        "name": one.name or "(no Name tag)",
        "state": one.state,
        "state_reason": one.state_reason or "-",
        "type": one.instance_type,
        "private_ip": one.private_ip or "-",
        "public_ip": one.public_ip or "-",
        "availability_zone": one.availability_zone,
        "vpc_id": one.vpc_id,
        "subnet_id": one.subnet_id,
        "key_name": one.key_name or "-",
        "platform": one.platform or "-",
        "launched": one.launched,
    }
    render([jsonable(detail)], fmt=output, columns=None, title=one.label)


def _power(typer_ctx: typer.Context, verb: str, identifier: str, yes: bool, force: bool) -> None:
    """The body all three power commands share - including the confirm."""
    ctx = _ctx(typer_ctx)
    if not yes:
        typer.confirm(f"{verb.capitalize()} {identifier}?", abort=True)
    try:
        console.print(f"[OK] {ec2.power(ctx, verb, identifier, force=force)}", highlight=False)
    except (ClitkaError, LookupError, ValueError) as exc:
        raise _fail(exc) from exc


@app.command("start")
def start(
    typer_ctx: typer.Context,
    identifier: str = typer.Argument(..., help="Instance id or ARN."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask."),
) -> None:
    """Start a stopped instance."""
    _power(typer_ctx, "start", identifier, yes, force=False)


@app.command("stop")
def stop(
    typer_ctx: typer.Context,
    identifier: str = typer.Argument(..., help="Instance id or ARN."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask."),
    force: bool = typer.Option(
        False, "--force", help="Pull the plug instead of asking the OS. Can corrupt a filesystem."
    ),
) -> None:
    """Stop a running instance."""
    _power(typer_ctx, "stop", identifier, yes, force=force)


@app.command("reboot")
def reboot(
    typer_ctx: typer.Context,
    identifier: str = typer.Argument(..., help="Instance id or ARN."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask."),
) -> None:
    """Reboot a running instance."""
    _power(typer_ctx, "reboot", identifier, yes, force=False)
