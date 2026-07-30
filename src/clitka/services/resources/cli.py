"""`clitka resources` - the generic Cloud Control explorer from the CLI.

Every TUI action has a scriptable equivalent; this is that equivalent for the
resource explorer.
"""

from __future__ import annotations

import json

import typer

from clitka.core import cloudcontrol as cc
from clitka.core.context import Context
from clitka.core.errors import ClitkaError
from clitka.core.output import OutputFormat, console, err_console, render, render_one

app = typer.Typer(
    no_args_is_help=True,
    help="Generic resource explorer over the Cloud Control API.",
)


def _ctx(typer_ctx: typer.Context) -> Context:
    state = typer_ctx.obj
    return state["context"] if state else Context.from_env()


def _parse_input(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClitkaError(f"--input is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ClitkaError("--input must be a JSON object")
    return parsed


def _fail(exc: Exception) -> typer.Exit:
    err_console.print(f"[ERROR] {exc}")
    return typer.Exit(1)


@app.command("types")
def types(
    typer_ctx: typer.Context,
    contains: str = typer.Option(None, "--contains", "-c", help="Substring filter on the name."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """List the resource types available in this region (needs ListTypes)."""
    ctx = _ctx(typer_ctx)
    try:
        rows = cc.list_types(ctx)
    except ClitkaError as exc:
        raise _fail(exc) from exc
    if contains:
        needle = contains.lower()
        rows = [row for row in rows if needle in row["type_name"].lower()]
    render(rows, fmt=output, columns=["type_name", "description"], title="resource types")


@app.command("list")
def list_resources(
    typer_ctx: typer.Context,
    type_name: str = typer.Argument(..., help="e.g. AWS::S3::Bucket"),
    resource_input: str = typer.Option(
        None, "--input", "-i", help='JSON with parent identifiers, e.g. \'{"VpcId":"vpc-1"}\'.'
    ),
    limit: int = typer.Option(None, "--limit", "-n", help="Stop after this many resources."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """List resources of one type."""
    ctx = _ctx(typer_ctx)
    try:
        found = cc.list_resources(ctx, type_name, _parse_input(resource_input), limit=limit)
    except ClitkaError as exc:
        raise _fail(exc) from exc
    render(
        [resource.row() for resource in found],
        fmt=output,
        columns=cc.columns_for(found),
        title=type_name,
    )


@app.command("get")
def get_resource(
    typer_ctx: typer.Context,
    type_name: str = typer.Argument(..., help="e.g. AWS::S3::Bucket"),
    identifier: str = typer.Argument(..., help="The resource identifier."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """Show all properties of one resource."""
    ctx = _ctx(typer_ctx)
    try:
        resource = cc.get_resource(ctx, type_name, identifier)
    except ClitkaError as exc:
        raise _fail(exc) from exc
    render_one(
        {"identifier": resource.identifier, **resource.properties},
        fmt=output,
        title=f"{type_name} {identifier}",
    )


@app.command("delete")
def delete_resource(
    typer_ctx: typer.Context,
    type_name: str = typer.Argument(..., help="e.g. AWS::S3::Bucket"),
    identifier: str = typer.Argument(..., help="The resource identifier."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """Delete a resource. Always names the target before doing anything."""
    ctx = _ctx(typer_ctx)
    if not yes:
        console.print(f"About to DELETE {type_name} '{identifier}'")
        console.print(f"  profile: {ctx.profile or '(default)'}  region: {ctx.effective_region}")
        typer.confirm("Continue?", abort=True)
    try:
        result = cc.delete_resource(ctx, type_name, identifier)
    except ClitkaError as exc:
        raise _fail(exc) from exc
    render_one(result, fmt=output, title="delete requested")
