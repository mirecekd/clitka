"""`clitka lambda` - list, describe and invoke functions from the command line.

The scriptable half of M4. `invoke` is the interesting one: it prints the payload
on stdout and everything else on stderr, so `clitka lambda invoke f | jq .` works,
and it **exits non-zero when the handler raised** even though AWS answered 200.
That exit code is the whole point of having this rather than `aws lambda invoke`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from clitka.core import lambdafn as lb
from clitka.core.context import Context
from clitka.core.errors import ClitkaError
from clitka.core.output import OutputFormat, console, err_console, jsonable, render

app = typer.Typer(no_args_is_help=True, help="Lambda: list, describe and invoke functions.")

COLUMNS = ["identifier", "runtime", "memory", "timeout", "modified"]


def _ctx(typer_ctx: typer.Context) -> Context:
    state = typer_ctx.obj
    return state["context"] if state else Context.from_env()


def _fail(exc: Exception) -> typer.Exit:
    err_console.print(f"[ERROR] {exc}")
    return typer.Exit(1)


@app.command("list")
def list_(
    typer_ctx: typer.Context,
    limit: int = typer.Option(None, "--limit", "-n", help="Stop after this many functions."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """List the functions in the current region."""
    ctx = _ctx(typer_ctx)
    try:
        found = lb.list_functions(ctx, limit=limit)
    except ClitkaError as exc:
        raise _fail(exc) from exc
    render([fn.row() for fn in found], fmt=output, columns=COLUMNS, title="functions")


@app.command("get")
def get(
    typer_ctx: typer.Context,
    name: str = typer.Argument(..., help="Function name or ARN."),
    qualifier: str = typer.Option(None, "--qualifier", "-q", help="A version or an alias."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """Show one function's configuration, including its environment variables."""
    ctx = _ctx(typer_ctx)
    try:
        fn = lb.get_function(ctx, name, qualifier=qualifier or "")
    except ClitkaError as exc:
        raise _fail(exc) from exc
    detail: dict[str, Any] = {
        "name": fn.name,
        "arn": fn.arn,
        "runtime": fn.runtime or fn.package_type,
        "handler": fn.handler,
        "memory": fn.memory,
        "timeout": fn.timeout,
        "code_size": fn.code_size,
        "version": fn.version,
        "architectures": list(fn.architectures),
        "role": fn.role,
        "log_group": fn.log_group,
        "modified": fn.modified,
        "env": fn.env,
        "layers": list(fn.layers),
        "state": fn.state or "(not reported)",
    }
    render([jsonable(detail)], fmt=output, columns=None, title=fn.name)


@app.command("aliases")
def aliases(
    typer_ctx: typer.Context,
    name: str = typer.Argument(..., help="Function name or ARN."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """List a function's aliases and which version each points at."""
    ctx = _ctx(typer_ctx)
    try:
        found = lb.list_aliases(ctx, name)
    except ClitkaError as exc:
        raise _fail(exc) from exc
    render(found, fmt=output, columns=["name", "version", "description"], title=f"{name} aliases")


def _payload(payload: str | None, payload_file: Path | None) -> str | None:
    """The payload from `--payload` or `--payload-file`, never both."""
    if payload and payload_file:
        raise ValueError("give either --payload or --payload-file, not both")
    if payload_file:
        try:
            return payload_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"cannot read {payload_file}: {exc}") from exc
    return payload


@app.command("invoke")
def invoke(
    typer_ctx: typer.Context,
    name: str = typer.Argument(..., help="Function name or ARN."),
    payload: str = typer.Option(None, "--payload", "-d", help="The event, as JSON."),
    payload_file: Path = typer.Option(
        None, "--payload-file", "-D", help="Read the event from this file."
    ),
    asynchronous: bool = typer.Option(
        False, "--async", help="Fire and forget (InvocationType=Event)."
    ),
    qualifier: str = typer.Option(None, "--qualifier", "-q", help="A version or an alias."),
    logs: bool = typer.Option(True, "--logs/--no-logs", help="Show the tail of the log output."),
) -> None:
    """Invoke a function and print what it returned.

    The payload goes to stdout, everything else to stderr, so this can be piped.
    A handler that raised exits 1 even though AWS answered 200.
    """
    ctx = _ctx(typer_ctx)
    try:
        body = _payload(payload, payload_file)
        result = lb.invoke(
            ctx,
            name,
            payload=body,
            asynchronous=asynchronous,
            qualifier=qualifier or "",
            with_logs=logs,
        )
    except (ClitkaError, ValueError) as exc:
        raise _fail(exc) from exc

    if logs and result.log_tail:
        for line in result.log_lines():
            err_console.print(f"[dim]{line}[/dim]", highlight=False, markup=False)
    if result.payload:
        # No markup, no highlighting, no wrapping: the payload is data on its way
        # into a pipe, and a JSON body full of brackets must survive intact.
        console.print(result.payload, highlight=False, markup=False, soft_wrap=True)
    err_console.print(f"[dim]{result.summary()}[/dim]")
    if not result.ok:
        raise typer.Exit(1)
