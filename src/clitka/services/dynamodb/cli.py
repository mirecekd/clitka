# src/clitka/services/dynamodb/cli.py
"""`clitka dynamodb` - PartiQL against a table.

The scriptable half, and it is deliberately **one command**. A `tables` listing was
written and then deleted: `AWS::DynamoDB::Table` is a real Cloud Control type (PoC
Q1), so `clitka resources AWS::DynamoDB::Table` already lists them and the TUI tree
already has the branch. A second listing would only be a second thing to keep true.

`aws dynamodb execute-statement` already exists, so what `ql` adds is CLITKA's
shape: the `NextToken` walk done for you (the AWS CLI hands you the token and lets
you loop), flat rows instead of nested type descriptors, and exit code 1 when the
answer was **capped** rather than a silent partial result - the `s3 ls` rule.
"""

from __future__ import annotations

import typer

from clitka.core import ddbql
from clitka.core.context import Context
from clitka.core.errors import ClitkaError
from clitka.core.output import OutputFormat, console, err_console, render

app = typer.Typer(no_args_is_help=True, help="DynamoDB: PartiQL.")


def _ctx(typer_ctx: typer.Context) -> Context:
    state = typer_ctx.obj
    return state["context"] if state else Context.from_env()


def _fail(exc: Exception) -> typer.Exit:
    err_console.print(f"[ERROR] {exc}")
    return typer.Exit(1)


@app.command("ql")
def ql(
    typer_ctx: typer.Context,
    statement: str = typer.Argument(..., help='e.g. SELECT * FROM "my-table"'),
    max_rows: int = typer.Option(ddbql.MAX_ROWS, "--max-rows", help="Stop after this many."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """Run a PartiQL statement.

    Quote the table name: PartiQL wants `FROM "my-table"`, and a bare name with a
    hyphen in it is a syntax error rather than a lookup failure.

    Exits 1 when the answer was cut short at a cap, for the same reason `s3 ls`
    does - a partial answer that looks complete is worse than an error. A write
    statement (INSERT / UPDATE / DELETE) is refused in read-only mode.
    """
    ctx = _ctx(typer_ctx)
    try:
        page = ddbql.run(ctx, statement, max_rows=max_rows)
    except (ClitkaError, ValueError) as exc:
        raise _fail(exc) from exc

    render(page.rows, fmt=output, columns=ddbql.columns_of(page.rows), title=page.summary())
    if not page.rows:
        console.print("[dim](no rows)[/dim]")
    if page.capped:
        err_console.print(f"[ERROR] {page.summary()} - narrow it or raise --max-rows")
        raise typer.Exit(1)
