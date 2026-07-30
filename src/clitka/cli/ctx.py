"""`clitka ctx` - inspect and choose the operating context."""

from __future__ import annotations

import typer

from clitka.core.context import Context
from clitka.core.errors import ClitkaError
from clitka.core.output import OutputFormat, err_console, render, render_one

app = typer.Typer(no_args_is_help=True, help="Profile, region and identity context.")


def _ctx(state: dict) -> Context:
    return state["context"]


@app.command("show")
def show(
    typer_ctx: typer.Context,
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """Show the current profile, region, account and identity."""
    ctx = _ctx(typer_ctx.obj)
    render_one(ctx.describe(), fmt=output, title="clitka context")


@app.command("profiles")
def profiles(
    typer_ctx: typer.Context,
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """List the profiles available in ~/.aws/config and ~/.aws/credentials."""
    ctx = _ctx(typer_ctx.obj)
    try:
        names = sorted(ctx.session.available_profiles)
    except ClitkaError as exc:
        err_console.print(f"[ERROR] {exc}")
        raise typer.Exit(1) from exc
    current = ctx.profile
    render(
        [{"profile": name, "current": name == current} for name in names],
        fmt=output,
        columns=["profile", "current"],
        title="AWS profiles",
    )


@app.command("regions")
def regions(
    typer_ctx: typer.Context,
    service: str = typer.Option("ec2", "--service", "-s", help="Partition service to enumerate."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """List the regions known to the SDK for a service."""
    ctx = _ctx(typer_ctx.obj)
    names = sorted(ctx.session.get_available_regions(service))
    current = ctx.effective_region
    render(
        [{"region": name, "current": name == current} for name in names],
        fmt=output,
        columns=["region", "current"],
        title=f"regions for {service}",
    )
