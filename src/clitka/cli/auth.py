"""`clitka auth` - IAM Identity Center login, logout and token status.

The token cache is shared with aws CLI v2, so `clitka auth login` and
`aws sso login` are interchangeable.
"""

from __future__ import annotations

import typer

from clitka.core import sso, ssotargets
from clitka.core.awsconfig import load_aws_config
from clitka.core.context import Context
from clitka.core.errors import ClitkaError
from clitka.core.output import OutputFormat, console, err_console, render

app = typer.Typer(no_args_is_help=True, help="IAM Identity Center (SSO) authentication.")


def _ctx(state: dict | None) -> Context:
    if not state:
        return Context.from_env()
    return state["context"]


def _resolve(profile: str | None, session: str | None) -> ssotargets.SsoTarget:
    """Pick the login target from an explicit sso-session or from a profile."""
    cfg = load_aws_config()
    if session:
        known = cfg.sso_sessions.get(session)
        if known is None:
            raise ClitkaError(f"sso-session '{session}' is not in ~/.aws/config")
        return ssotargets.target_from_session(known)
    if profile:
        return ssotargets.target_for_profile(cfg, profile)
    available = ssotargets.targets(cfg)
    if len(available) == 1:
        return available[0]
    names = ", ".join(t.key for t in available) or "(none)"
    raise ClitkaError(f"specify --profile or --sso-session; sessions in ~/.aws/config: {names}")


@app.command("login")
def login(
    typer_ctx: typer.Context,
    profile: str = typer.Option(None, "--profile", "-p", help="Log in for this profile."),
    session: str = typer.Option(None, "--sso-session", "-s", help="Log in for this sso-session."),
    open_browser: bool = typer.Option(False, "--open", help="Open the URL in a browser."),
    force: bool = typer.Option(False, "--force", help="Log in even if a valid token exists."),
) -> None:
    """Run the SSO device authorization flow and cache the token."""
    ctx = _ctx(typer_ctx.obj)
    try:
        target = _resolve(profile or ctx.profile, session)
        sso.login(
            target,
            open_browser=open_browser,
            report=console.print,
            force=force,
        )
    except ClitkaError as exc:
        err_console.print(f"[ERROR] {exc}")
        raise typer.Exit(1) from exc


@app.command("logout")
def logout(
    typer_ctx: typer.Context,
    profile: str = typer.Option(None, "--profile", "-p", help="Log out for this profile."),
    session: str = typer.Option(None, "--sso-session", "-s", help="Log out for this sso-session."),
    all_sessions: bool = typer.Option(False, "--all", help="Log out of every sso-session."),
    forget_client: bool = typer.Option(
        False, "--forget-client", help="Also drop the cached client registration."
    ),
) -> None:
    """Remove cached SSO tokens (aws CLI v2 sees this too)."""
    ctx = _ctx(typer_ctx.obj)
    try:
        chosen = (
            ssotargets.targets(load_aws_config())
            if all_sessions
            else [_resolve(profile or ctx.profile, session)]
        )
    except ClitkaError as exc:
        err_console.print(f"[ERROR] {exc}")
        raise typer.Exit(1) from exc
    for target in chosen:
        removed = sso.logout(target, forget_registration=forget_client)
        state = "signed out" if removed else "was not signed in"
        console.print(f"[OK] {target.key}: {state}")


@app.command("status")
def status(
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """Show, per sso-session, whether a cached token exists and when it expires."""
    try:
        rows = ssotargets.status(load_aws_config())
    except ClitkaError as exc:
        err_console.print(f"[ERROR] {exc}")
        raise typer.Exit(1) from exc
    render(
        rows,
        fmt=output,
        columns=["sso_session", "region", "valid", "expires_at", "expires_in", "start_url"],
        title="SSO sessions",
    )
