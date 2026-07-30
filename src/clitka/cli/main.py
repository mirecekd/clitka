"""CLITKA root CLI. Bare `clitka` launches the TUI, subcommands stay scriptable."""

from __future__ import annotations

import typer

from clitka import __version__
from clitka.cli import ctx as ctx_cli
from clitka.core.context import Context
from clitka.core.errors import ClitkaError
from clitka.core.output import console, err_console
from clitka.core.plugins import service_apps

app = typer.Typer(
    name="clitka",
    help="CLITKA - CLI ToolKit for AWS. Run without arguments to start the TUI.",
    add_completion=True,
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(ctx_cli.app, name="ctx")

for _name, _service_app in service_apps():
    app.add_typer(_service_app, name=_name)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"clitka {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    typer_ctx: typer.Context,
    profile: str = typer.Option(None, "--profile", "-p", help="AWS profile to use."),
    region: str = typer.Option(None, "--region", "-r", help="AWS region to use."),
    read_only: bool = typer.Option(False, "--read-only", help="Refuse mutating operations."),
    _version: bool = typer.Option(
        False, "--version", "-V", callback=_version_callback, is_eager=True
    ),
) -> None:
    """Build the operating context shared by every subcommand."""
    context = Context.from_env(profile=profile, region=region)
    if read_only:
        context.read_only = True
    typer_ctx.obj = {"context": context}

    if typer_ctx.invoked_subcommand is None:
        _launch_tui(context)


def _launch_tui(context: Context) -> None:
    try:
        from clitka.tui.app import ClitkaApp
    except ImportError:
        err_console.print("[ERROR] the TUI is not available yet - use `clitka --help`")
        raise typer.Exit(1) from None
    ClitkaApp(context).run()


def run() -> None:
    """Console-script entrypoint with uniform error handling."""
    try:
        app()
    except ClitkaError as exc:
        err_console.print(f"[ERROR] {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    run()
