"""`clitka s3` - buckets, and one level of a bucket at a time.

The scriptable half. `ls` is the one worth having: it takes a bucket, an
`s3://` URI or a `bucket/prefix/` and answers with the folders and the files at
that level, which is the same call the tree makes when a node is opened.

`aws s3 ls` already exists, so what this adds is CLITKA's own shape: one row type
across the TUI and the CLI, `--json` that matches the preview pane, and an exit
code of 1 for a listing that was cut short rather than a silent partial answer.
"""

from __future__ import annotations

from typing import Any

import typer

from clitka.core import s3
from clitka.core.context import Context
from clitka.core.errors import ClitkaError
from clitka.core.output import (
    OutputFormat,
    console,
    err_console,
    jsonable,
    render,
    render_one,
)

app = typer.Typer(no_args_is_help=True, help="S3: buckets, prefixes and objects.")

BUCKET_COLUMNS = ["identifier", "created", "region"]
ENTRY_COLUMNS = ["identifier", "kind", "size", "modified", "storage"]


def _ctx(typer_ctx: typer.Context) -> Context:
    state = typer_ctx.obj
    return state["context"] if state else Context.from_env()


def _fail(exc: Exception) -> typer.Exit:
    err_console.print(f"[ERROR] {exc}")
    return typer.Exit(1)


@app.command("buckets")
def buckets(
    typer_ctx: typer.Context,
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """List every bucket in the account.

    `ListBuckets` is global, so this ignores the region entirely - and it reports
    no region per bucket, because that would be one extra call each (98 buckets on
    the owner's sandbox). `clitka s3 get <bucket>` says where one lives.
    """
    ctx = _ctx(typer_ctx)
    try:
        found = s3.list_buckets(ctx)
    except ClitkaError as exc:
        raise _fail(exc) from exc
    render([one.row() for one in found], fmt=output, columns=BUCKET_COLUMNS, title="buckets")


@app.command("ls")
def ls(
    typer_ctx: typer.Context,
    target: str = typer.Argument(..., help="Bucket, bucket/prefix/ or an s3:// URI."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """List one level: the folders, then the files.

    Exits 1 when the level was **cut short** at the display cap, because a partial
    listing that looks complete is worse than an error - the same reason
    `clitka lambda invoke` exits 1 on a handler that raised.
    """
    ctx = _ctx(typer_ctx)
    try:
        found = s3.browse(ctx, target)
    except ClitkaError as exc:
        raise _fail(exc) from exc

    rows: list[dict[str, Any]] = []
    for folder in found.folders:
        rows.append(
            {
                "identifier": folder.label,
                "kind": "folder",
                "size": "",
                "modified": "",
                "storage": "",
            }
        )
    for obj in found.files:
        row = obj.row()
        rows.append(
            {
                "identifier": obj.location.label,
                "kind": "object",
                "size": row["size"],
                "modified": row["modified"],
                "storage": row["storage"],
            }
        )
    render(rows, fmt=output, columns=ENTRY_COLUMNS, title=found.location.uri)
    if found.capped:
        err_console.print(
            f"[ERROR] cut off at {s3.MAX_CHILDREN} entries - narrow the prefix to see the rest"
        )
        raise typer.Exit(1)
    if not rows:
        console.print("[dim](empty)[/dim]")


@app.command("get")
def get(
    typer_ctx: typer.Context,
    name: str = typer.Argument(..., help="Bucket name or an s3:// URI."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """Show one bucket, including the region it lives in.

    The region is the reason this command exists: a listing cannot report it
    without one call per bucket, and a bucket in another region is invisible in the
    console's regional view even though every API call still reaches it.
    """
    ctx = _ctx(typer_ctx)
    try:
        bucket = s3.get_bucket(ctx, name)
    except (ClitkaError, LookupError) as exc:
        raise _fail(exc) from exc
    detail: dict[str, Any] = {
        "name": bucket.name,
        "uri": bucket.location.uri,
        "region": bucket.region,
    }
    # `render_one`, not `render`: `render` takes a SEQUENCE of records and iterates
    # a dict as its keys, so a live `s3 get` printed ["name", "uri", "region"] and
    # no values at all. Found by running it, not by reading it.
    render_one(jsonable(detail), fmt=output, title=bucket.name)
