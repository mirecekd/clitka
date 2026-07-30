"""`clitka ctx` - inspect and choose the operating context."""

from __future__ import annotations

import typer

from clitka.core import clitkaconfig
from clitka.core.awsconfig import load_aws_config
from clitka.core.context import Context
from clitka.core.errors import ClitkaError
from clitka.core.output import OutputFormat, console, err_console, render, render_one

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
        rows = load_aws_config().summary()
    except ClitkaError as exc:
        err_console.print(f"[ERROR] {exc}")
        raise typer.Exit(1) from exc
    for row in rows:
        row["current"] = "yes" if row["profile"] == ctx.profile else ""
    render(
        rows,
        fmt=output,
        columns=["profile", "kind", "region", "account", "role", "sso_session", "current"],
        title="AWS profiles",
    )


# `list` is an alias people type without thinking; keep both.
app.command("list", hidden=True)(profiles)


@app.command("use")
def use(
    profile: str = typer.Argument(None, help="Profile to make the default for CLITKA."),
    region: str = typer.Option(None, "--region", "-r", help="Region to persist as well."),
    read_only: bool = typer.Option(None, "--read-only/--no-read-only", help="Persist the guard."),
    clear: bool = typer.Option(False, "--clear", help="Forget the persisted profile and region."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """Persist a profile/region choice in ~/.config/clitka/config.toml.

    Only CLITKA's own config is written - `~/.aws/*` is never modified.
    """
    if clear:
        saved = clitkaconfig.load()
        clitkaconfig.save(clitkaconfig.ClitkaConfig(theme=saved.theme))
        console.print(f"[OK] cleared {clitkaconfig.config_path()}")
        return
    if profile is None and region is None and read_only is None:
        err_console.print("[ERROR] nothing to do: give a profile, --region or --read-only")
        raise typer.Exit(2)
    if profile is not None:
        known = load_aws_config()
        if profile not in known.profiles:
            err_console.print(f"[ERROR] profile '{profile}' is not in ~/.aws/config")
            raise typer.Exit(1)
        if region is None:
            region = known.profiles[profile].region

    saved = clitkaconfig.update(profile=profile, region=region, read_only=read_only)
    render_one(
        {
            "config": str(clitkaconfig.config_path()),
            "profile": saved.profile or "(unset)",
            "region": saved.region or "(unset)",
            "read_only": "yes" if saved.read_only else "no",
        },
        fmt=output,
        title="[OK] saved",
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
