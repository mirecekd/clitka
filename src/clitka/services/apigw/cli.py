"""`clitka apigw` - list APIs, their routes and stages, and **invoke** one.

The scriptable half. `invoke` is the reason this plugin exists: `aws apigateway
test-invoke-method` bypasses the whole edge (no authorizer, no stage variable, no
WAF, REST only), so it answers a different question from "does my API work".

`clitka apigw invoke` sends a real request to the real URL and **exits 1 on a
non-2xx**, which is what makes it usable in a script or a pipeline - the same
reason `clitka lambda invoke` exits 1 on a `FunctionError`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from clitka.core import apigw
from clitka.core.context import Context
from clitka.core.errors import ClitkaError
from clitka.core.output import OutputFormat, console, err_console, jsonable, render

app = typer.Typer(no_args_is_help=True, help="API Gateway: REST and HTTP APIs, and invoke.")

APIS = ["identifier", "name", "kind", "endpoint", "created"]
ROUTES = ["method", "path", "auth", "integration", "identifier"]
STAGES = ["identifier", "description", "deployment", "auto_deploy", "updated"]


def _ctx(typer_ctx: typer.Context) -> Context:
    state = typer_ctx.obj
    return state["context"] if state else Context.from_env()


def _fail(exc: Exception) -> typer.Exit:
    err_console.print(f"[ERROR] {exc}")
    return typer.Exit(1)


def _pairs(given: list[str] | None, what: str) -> dict[str, str]:
    """`["a=1", "b=2"]` as a dict. A value may contain `=`; a name may not."""
    out: dict[str, str] = {}
    for one in given or []:
        name, sep, value = one.partition("=")
        if not sep or not name:
            raise ClitkaError(f"{what} {one!r} is not name=value")
        out[name] = value
    return out


@app.command("list")
def list_apis(
    typer_ctx: typer.Context,
    kind: str = typer.Option("", "--kind", "-k", help="REST, HTTP or WEBSOCKET only."),
    limit: int = typer.Option(None, "--limit", "-n", help="Stop after this many."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """List every API in the region - both the REST ones and the HTTP ones."""
    try:
        found = apigw.list_apis(_ctx(typer_ctx), kind=kind, limit=limit)
    except (ClitkaError, ValueError) as exc:
        raise _fail(exc) from exc
    render([one.row() for one in found], fmt=output, columns=APIS, title="apis")


@app.command("get")
def get(
    typer_ctx: typer.Context,
    identifier: str = typer.Argument(..., help="API id, ARN or invoke URL."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """Show one API in full, including the URL a request would go to."""
    ctx = _ctx(typer_ctx)
    try:
        one = apigw.get_api(ctx, identifier)
        stages = apigw.list_stages(ctx, one)
    except (ClitkaError, LookupError) as exc:
        raise _fail(exc) from exc
    detail: dict[str, Any] = {
        "api_id": one.api_id,
        "name": one.label,
        "kind": one.kind,
        "description": one.description or "-",
        "endpoint_type": one.endpoint_type or "-",
        "version": one.version or "-",
        "created": one.created,
        "stages": [stage.name for stage in stages] or ["(never deployed)"],
        # The useful field: "" means a request would reach it right now.
        "invoke": one.refuses_invoke() or "reachable",
        "base_url": one.invoke_url(stages[0].name if stages else ""),
    }
    render([jsonable(detail)], fmt=output, columns=None, title=one.label)


@app.command("routes")
def routes(
    typer_ctx: typer.Context,
    identifier: str = typer.Argument(..., help="API id, ARN or invoke URL."),
    open_only: bool = typer.Option(False, "--open", help="Only routes with no authorizer."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """List what an API can be called with - its methods and paths."""
    try:
        found = apigw.list_routes(_ctx(typer_ctx), identifier)
    except (ClitkaError, LookupError) as exc:
        raise _fail(exc) from exc
    if open_only:
        found = [one for one in found if one.open]
    render([one.row() for one in found], fmt=output, columns=ROUTES, title="routes")


@app.command("stages")
def stages(
    typer_ctx: typer.Context,
    identifier: str = typer.Argument(..., help="API id, ARN or invoke URL."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """List an API's deployed stages. Nothing listed means nothing is reachable."""
    try:
        found = apigw.list_stages(_ctx(typer_ctx), identifier)
    except (ClitkaError, LookupError) as exc:
        raise _fail(exc) from exc
    if not found:
        err_console.print("[dim]No stages - this API has never been deployed.[/dim]")
    render([one.row() for one in found], fmt=output, columns=STAGES, title="stages")


@app.command("invoke")
def invoke(
    typer_ctx: typer.Context,
    identifier: str = typer.Argument(..., help="API id, ARN or invoke URL."),
    stage: str = typer.Argument(..., help="Stage name, or $default on an HTTP API."),
    path: str = typer.Option("/", "--path", "-P", help="Path, with {name} placeholders."),
    method: str = typer.Option("GET", "--method", "-X", help="HTTP method."),
    param: list[str] = typer.Option(None, "--param", help="Fill a {name} - name=value."),
    query: list[str] = typer.Option(None, "--query", "-q", help="Query string - name=value."),
    header: list[str] = typer.Option(None, "--header", "-H", help="Header - Name=value."),
    body: str = typer.Option("", "--body", "-b", help="Request body, inline."),
    body_file: Path = typer.Option(None, "--body-file", help="Request body, from a file."),
    sign: bool = typer.Option(False, "--sign", help="SigV4-sign it (an AWS_IAM route)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the request, do not send it."),
    output: OutputFormat = typer.Option(OutputFormat.AUTO, "--output", "-o"),
) -> None:
    """Call an API for real, through its edge. Exits 1 on any non-2xx answer."""
    ctx = _ctx(typer_ctx)
    try:
        payload = body_file.read_text() if body_file else body
        params, queries, headers = (
            _pairs(param, "--param"),
            _pairs(query, "--query"),
            _pairs(header, "--header"),
        )
        one = apigw.get_api(ctx, identifier)
        if dry_run:
            url, sent, raw = apigw.request_for(
                one, stage, method, path, params, queries, payload, headers
            )
            console.print(f"{method.upper()} {url}", highlight=False)
            for name, value in sent.items():
                console.print(f"{name}: {value}", highlight=False)
            if raw:
                console.print(f"\n{raw.decode('utf-8', errors='replace')}", highlight=False)
            return
        answer = apigw.invoke(
            ctx, one, stage, method, path, params, queries, payload, headers, sign=sign
        )
    except (ClitkaError, LookupError, OSError) as exc:
        raise _fail(exc) from exc
    _report(answer, output)
    if not answer.ok:
        raise typer.Exit(1)


def _report(answer: apigw.Response, output: OutputFormat) -> None:
    """The body on stdout, everything about it on stderr - so a pipe stays clean."""
    err_console.print(f"[dim]{answer.summary()}  {answer.url}[/dim]")
    hint = answer.hint()
    if hint:
        err_console.print(f"[yellow]{hint}[/yellow]")
    if output in (OutputFormat.JSON, OutputFormat.YAML):
        render([jsonable({"status": answer.status, "body": answer.body})], fmt=output, columns=None)
        return
    console.print(answer.pretty(), highlight=False)
