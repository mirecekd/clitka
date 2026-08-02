"""`clitka ssm docs|doc|run` - the document half of the CLI.

Split from `cli.py` for the 8 kB rule, on the same seam the core modules split
on: a parameter and a document have nothing in common but the service name.
`cli.py` mounts these commands, so the user still types `clitka ssm run`.

**`run` waits by default and exits 1 when the script did not succeed.** That exit
code is the whole reason this exists rather than `aws ssm send-command`, which
exits 0 as soon as AWS has *accepted* the request - before anything has run.
"""

from __future__ import annotations

from typing import Any

import typer

from clitka.core import ssm
from clitka.core.context import Context
from clitka.core.errors import ClitkaError
from clitka.core.output import OutputFormat, console, err_console, jsonable, render

app = typer.Typer(no_args_is_help=True)

DOC_COLUMNS = ["identifier", "type", "owner", "version", "platforms", "format"]


def _ctx(typer_ctx: typer.Context) -> Context:
    state = typer_ctx.obj
    return state["context"] if state else Context.from_env()


def _fail(exc: Exception) -> typer.Exit:
    err_console.print(f"[ERROR] {exc}")
    return typer.Exit(1)


@app.command("docs")
def docs(
    typer_ctx: typer.Context,
    everything: bool = typer.Option(
        False, "--all", help="Include the hundreds AWS ships. Off by default."
    ),
    kind: str = typer.Option("", "--type", "-t", help="Command, Automation, Session, ..."),
    limit: int = typer.Option(None, "--limit", "-n"),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """List the documents in this account."""
    ctx = _ctx(typer_ctx)
    try:
        found = ssm.list_documents(ctx, mine=not everything, kind=kind, limit=limit)
    except ClitkaError as exc:
        raise _fail(exc) from exc
    render([one.row() for one in found], fmt=output, columns=DOC_COLUMNS, title="documents")


@app.command("doc")
def doc(
    typer_ctx: typer.Context,
    name: str = typer.Argument(..., help="Document name, e.g. AWS-RunShellScript."),
    content: bool = typer.Option(False, "--content", help="Print the document body too."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """Show one document, including the parameters it insists on."""
    ctx = _ctx(typer_ctx)
    try:
        one = ssm.get_document(ctx, name, with_content=content)
    except ClitkaError as exc:
        raise _fail(exc) from exc
    detail: dict[str, Any] = {
        "name": one.name,
        "type": one.document_type,
        "owner": one.owner or "-",
        "version": one.version or "-",
        "status": one.status or "-",
        "platforms": one.platforms or "any",
        "runnable": "yes" if one.runnable else f"no - it is {one.document_type or 'unknown'}",
        "parameters": [param.line() for param in one.parameters] or ["(none)"],
    }
    if content:
        detail["content"] = one.content
    render([jsonable(detail)], fmt=output, columns=None, title=one.name)


@app.command("run")
def run_cmd(
    typer_ctx: typer.Context,
    name: str = typer.Argument(..., help="Command document, e.g. AWS-RunShellScript."),
    instances: list[str] = typer.Argument(..., help="One or more instance ids."),
    param: list[str] = typer.Option(
        None, "--param", "-p", help="A document parameter as key=value. Repeatable."
    ),
    comment: str = typer.Option("", "--comment"),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for the result."),
    timeout: float = typer.Option(60.0, "--timeout", help="Seconds to wait."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask."),
) -> None:
    """Run a Command document on instances, and report what it did."""
    ctx = _ctx(typer_ctx)
    try:
        parameters = parse_parameters(param or [])
    except ValueError as exc:
        raise _fail(exc) from exc
    if not yes:
        typer.confirm(f"Run {name} on {', '.join(instances)}?", abort=True)
    try:
        command_id = ssm.run(ctx, name, list(instances), parameters, comment=comment)
    except (ClitkaError, ValueError) as exc:
        raise _fail(exc) from exc
    console.print(f"[OK] sent, command id {command_id}", highlight=False)
    if not wait:
        return
    if _collect(ctx, command_id, list(instances), timeout):
        raise typer.Exit(1)


def _collect(ctx: Context, command_id: str, instances: list[str], timeout: float) -> bool:
    """Print each instance's result. True when any of them did not succeed."""
    failed = False
    for instance_id in instances:
        try:
            done = ssm.wait_for(ctx, command_id, instance_id, timeout=timeout)
        except ClitkaError as exc:
            raise _fail(exc) from exc
        console.print(f"\n[{'OK' if done.ok else 'ERROR'}] {done.summary()}", highlight=False)
        # markup=False: a script's output is not Rich markup, and a stray
        # bracket in a log line would otherwise blow up or vanish.
        if done.stdout:
            console.print(done.stdout.rstrip(), highlight=False, markup=False)
        if done.stderr:
            err_console.print(done.stderr.rstrip(), highlight=False, markup=False)
        failed = failed or not done.ok
    return failed


def parse_parameters(pairs: list[str]) -> dict[str, list[str]]:
    """`key=value` strings as SendCommand's `Parameters` shape.

    Every value is a list because that is what the API takes, whatever the
    document declared. A repeated key adds to its list, which is how a
    multi-line `commands` is given.
    """
    out: dict[str, list[str]] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"--param wants key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        if not key:
            raise ValueError(f"--param wants a name before the =, got {pair!r}")
        out.setdefault(key, []).append(value)
    return out


def _self_check() -> None:
    assert parse_parameters([]) == {}
    assert parse_parameters(["commands=uptime"]) == {"commands": ["uptime"]}
    # A repeated key builds a list - that is how several commands are sent.
    assert parse_parameters(["commands=a", "commands=b"]) == {"commands": ["a", "b"]}
    # A value may contain '=' itself, so only the first one splits.
    assert parse_parameters(["commands=echo a=b"]) == {"commands": ["echo a=b"]}
    # An empty value is a value.
    assert parse_parameters(["workingDirectory="]) == {"workingDirectory": [""]}
    for bad in ("commands", "=uptime"):
        try:
            parse_parameters([bad])
        except ValueError as exc:
            assert "--param wants" in str(exc), exc
        else:  # pragma: no cover
            raise AssertionError(f"{bad!r} was accepted")
    print("[OK] ssm document cli self-check passed")


if __name__ == "__main__":
    _self_check()
