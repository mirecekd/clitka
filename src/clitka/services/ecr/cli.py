"""`clitka ecr` - repositories, images, a delete and the login one-liner.

The scriptable half. `images` is the one worth having: `--untagged` lists exactly
what a cleanup wants to remove, and its digests pipe straight into `delete`.
"""

from __future__ import annotations

from typing import Any

import typer

from clitka.core import ecr
from clitka.core.context import Context
from clitka.core.errors import ClitkaError
from clitka.core.output import OutputFormat, console, err_console, jsonable, render

app = typer.Typer(no_args_is_help=True, help="ECR: repositories, images and cleanup.")

REPO_COLUMNS = ["identifier", "tags", "scan_on_push", "created"]
IMAGE_COLUMNS = ["identifier", "size", "pushed", "scan", "digest"]


def _ctx(typer_ctx: typer.Context) -> Context:
    state = typer_ctx.obj
    return state["context"] if state else Context.from_env()


def _fail(exc: Exception) -> typer.Exit:
    err_console.print(f"[ERROR] {exc}")
    return typer.Exit(1)


@app.command("repos")
def repos(
    typer_ctx: typer.Context,
    limit: int = typer.Option(None, "--limit", "-n", help="Stop after this many repositories."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """List the repositories in the current region."""
    ctx = _ctx(typer_ctx)
    try:
        found = ecr.list_repositories(ctx, limit=limit)
    except ClitkaError as exc:
        raise _fail(exc) from exc
    render([repo.row() for repo in found], fmt=output, columns=REPO_COLUMNS, title="repositories")


@app.command("get")
def get(
    typer_ctx: typer.Context,
    name: str = typer.Argument(..., help="Repository name, ARN or URI."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """Show one repository's configuration."""
    ctx = _ctx(typer_ctx)
    try:
        repo = ecr.get_repository(ctx, name)
    except (ClitkaError, LookupError) as exc:
        raise _fail(exc) from exc
    detail: dict[str, Any] = {
        "name": repo.name,
        "arn": repo.arn,
        "uri": repo.uri,
        "registry": repo.registry,
        "tag_mutability": repo.tag_mutability or "(not reported)",
        "scan_on_push": repo.scan_on_push,
        "encryption": repo.encryption or "(default)",
        "created": repo.created,
    }
    render([jsonable(detail)], fmt=output, columns=None, title=repo.name)


@app.command("images")
def images(
    typer_ctx: typer.Context,
    repository: str = typer.Argument(..., help="Repository name, ARN or URI."),
    untagged: bool = typer.Option(False, "--untagged", "-u", help="Only untagged images."),
    limit: int = typer.Option(None, "--limit", "-n", help="Stop after this many images."),
    digests: bool = typer.Option(
        False, "--digests", help="Print bare digests only - for piping into `delete`."
    ),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """List a repository's images, newest push first."""
    ctx = _ctx(typer_ctx)
    try:
        found = ecr.list_images(ctx, repository, limit=limit)
    except ClitkaError as exc:
        raise _fail(exc) from exc
    if untagged:
        found = [image for image in found if image.untagged]
    if digests:
        for image in found:
            console.print(image.digest, highlight=False, markup=False)
        return
    title = f"{ecr.repo_name_of(repository)} images"
    render([image.row() for image in found], fmt=output, columns=IMAGE_COLUMNS, title=title)


@app.command("delete")
def delete(
    typer_ctx: typer.Context,
    repository: str = typer.Argument(..., help="Repository name, ARN or URI."),
    digest: list[str] = typer.Argument(None, help="One or more image digests."),
    untagged: bool = typer.Option(
        False, "--untagged", "-u", help="Delete every untagged image instead."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask."),
) -> None:
    """Delete images by digest. Always by digest - a tag would take its siblings."""
    ctx = _ctx(typer_ctx)
    wanted = list(digest or [])
    try:
        if untagged:
            wanted += [one.digest for one in ecr.list_images(ctx, repository) if one.untagged]
        if not wanted:
            raise ValueError("nothing to delete: give a digest, or --untagged")
        if not yes:
            where = ecr.repo_name_of(repository)
            typer.confirm(f"Delete {len(wanted)} image(s) from {where}?", abort=True)
        result = ecr.delete_images(ctx, repository, wanted)
    except (ClitkaError, ValueError) as exc:
        raise _fail(exc) from exc

    for one in result["deleted"]:
        console.print(f"[OK] deleted {one}", highlight=False)
    for problem in result["failures"]:
        err_console.print(f"[ERROR] {problem}")
    if result["failures"]:
        raise typer.Exit(1)


@app.command("login")
def login(
    typer_ctx: typer.Context,
    repository: str = typer.Argument(None, help="Any repository in the registry to log in to."),
) -> None:
    """Print the `docker login` command for this account's registry."""
    ctx = _ctx(typer_ctx)
    registry = ""
    if repository:
        try:
            registry = ecr.get_repository(ctx, repository).registry
        except (ClitkaError, LookupError) as exc:
            raise _fail(exc) from exc
    console.print(ecr.login_command(ctx, registry), highlight=False, markup=False, soft_wrap=True)
