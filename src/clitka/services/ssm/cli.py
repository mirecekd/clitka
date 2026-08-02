"""`clitka ssm` - Parameter Store, plus the document commands from `clidoc.py`.

The scriptable half. **`--decrypt` is the only way to see a `SecureString`**;
without it a secret prints as `<SecureString, hidden>`, which is what makes
`ssm params` safe to run in front of someone.

The document commands (`docs`, `doc`, `run`) live in `clidoc.py` for the 8 kB rule
and are mounted here, so the user still types `clitka ssm run`.
"""

from __future__ import annotations

from typing import Any

import typer

from clitka.core import ssm
from clitka.core.context import Context
from clitka.core.errors import ClitkaError
from clitka.core.output import OutputFormat, console, err_console, jsonable, render
from clitka.services.ssm import clidoc

app = typer.Typer(no_args_is_help=True, help="Systems Manager: parameters and documents.")

PARAM_COLUMNS = ["identifier", "type", "value", "version", "tier", "modified"]


def _ctx(typer_ctx: typer.Context) -> Context:
    state = typer_ctx.obj
    return state["context"] if state else Context.from_env()


def _fail(exc: Exception) -> typer.Exit:
    err_console.print(f"[ERROR] {exc}")
    return typer.Exit(1)


@app.command("params")
def params(
    typer_ctx: typer.Context,
    contains: str = typer.Option("", "--contains", "-c", help="Only names containing this."),
    limit: int = typer.Option(None, "--limit", "-n", help="Stop after this many."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """List the parameters. Metadata only - this call cannot return a value."""
    ctx = _ctx(typer_ctx)
    try:
        found = ssm.list_parameters(ctx, contains=contains, limit=limit)
    except ClitkaError as exc:
        raise _fail(exc) from exc
    render([one.row() for one in found], fmt=output, columns=PARAM_COLUMNS, title="parameters")


@app.command("get")
def get(
    typer_ctx: typer.Context,
    name: str = typer.Argument(..., help="Parameter name or ARN."),
    decrypt: bool = typer.Option(
        False, "--decrypt", help="Show a SecureString's real value. It goes on your screen."
    ),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """Show one parameter. A SecureString stays hidden without `--decrypt`."""
    ctx = _ctx(typer_ctx)
    try:
        one = ssm.get_parameter(ctx, name, decrypt=decrypt)
    except (ClitkaError, LookupError) as exc:
        raise _fail(exc) from exc
    detail: dict[str, Any] = {
        "name": one.name,
        "type": one.type,
        # display_value() is the only thing that may render a value anywhere.
        "value": one.display_value(),
        "version": one.version,
        "data_type": one.data_type or "-",
        "last_modified": one.last_modified,
    }
    render([jsonable(detail)], fmt=output, columns=None, title=one.name)


@app.command("path")
def path(
    typer_ctx: typer.Context,
    prefix: str = typer.Argument(..., help="Path prefix, e.g. /app/prod."),
    recursive: bool = typer.Option(True, "--recursive/--flat", help="Descend into sub-paths."),
    decrypt: bool = typer.Option(False, "--decrypt", help="Show SecureString values."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """Every parameter under a path - one app's whole config in one call."""
    ctx = _ctx(typer_ctx)
    try:
        found = ssm.by_path(ctx, prefix, recursive=recursive, decrypt=decrypt)
    except ClitkaError as exc:
        raise _fail(exc) from exc
    render([one.row() for one in found], fmt=output, columns=PARAM_COLUMNS, title=prefix)


@app.command("history")
def history_cmd(
    typer_ctx: typer.Context,
    name: str = typer.Argument(..., help="Parameter name or ARN."),
    limit: int = typer.Option(10, "--limit", "-n"),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """The recent versions of a parameter. Values are never decrypted here."""
    ctx = _ctx(typer_ctx)
    try:
        found = ssm.history(ctx, name, limit=limit)
    except (ClitkaError, LookupError) as exc:
        raise _fail(exc) from exc
    render([one.row() for one in found], fmt=output, columns=PARAM_COLUMNS, title=f"{name} history")


@app.command("put")
def put(
    typer_ctx: typer.Context,
    name: str = typer.Argument(..., help="Parameter name, e.g. /app/prod/url."),
    value: str = typer.Argument(..., help="The value to store."),
    type_name: str = typer.Option(
        "String", "--type", "-t", help="String, StringList or SecureString."
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="Update an existing parameter."),
    description: str = typer.Option("", "--description", "-d"),
    key_id: str = typer.Option("", "--key-id", help="KMS key for a SecureString."),
) -> None:
    """Create or update a parameter."""
    ctx = _ctx(typer_ctx)
    try:
        said = ssm.put_parameter(
            ctx,
            name,
            value,
            type_name=type_name,
            overwrite=overwrite,
            description=description,
            key_id=key_id,
        )
    except (ClitkaError, ValueError) as exc:
        raise _fail(exc) from exc
    console.print(f"[OK] {said}", highlight=False)


@app.command("delete")
def delete(
    typer_ctx: typer.Context,
    name: str = typer.Argument(..., help="Parameter name or ARN."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask."),
) -> None:
    """Delete a parameter. Every version goes and there is no undo."""
    ctx = _ctx(typer_ctx)
    if not yes:
        typer.confirm(f"Delete {name} and all its versions?", abort=True)
    try:
        console.print(f"[OK] {ssm.delete_parameter(ctx, name)}", highlight=False)
    except (ClitkaError, LookupError) as exc:
        raise _fail(exc) from exc


# The document commands, mounted as if they had been declared here. Typer has no
# "merge another app's commands" call, so the registered commands are copied over
# - which is exactly what `add_typer` would *not* do (it would nest them under a
# sub-name, and `clitka ssm doc run` is not the command anyone wants).
app.registered_commands.extend(clidoc.app.registered_commands)

# Re-exported so `tests/test_ssm_service.py` and the F9 hint keep one import.
parse_parameters = clidoc.parse_parameters


def _self_check() -> None:
    names = {command.name for command in app.registered_commands}
    for wanted in ("params", "get", "path", "history", "put", "delete"):
        assert wanted in names, f"{wanted} is missing: {sorted(names)}"
    # The document commands must be flat, not nested under another word.
    for wanted in ("docs", "doc", "run"):
        assert wanted in names, f"{wanted} was not mounted: {sorted(names)}"
    clidoc._self_check()
    print(f"[OK] ssm cli self-check passed ({len(names)} commands)")


if __name__ == "__main__":
    _self_check()
