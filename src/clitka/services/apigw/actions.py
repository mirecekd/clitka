"""What F9 offers on an API Gateway API, and what the preview pane shows.

**Invoking is not an F9 action.** Same call as `lambda.invoke`: a keystroke must
not send a POST to someone's production endpoint, and unlike an EC2 stop a request
is not reversible - it may have created an order or charged a card. F9 shows the
routes, the stages and the *command* to call one; `clitka apigw invoke` sends it.

Two resource types, both of which Cloud Control does list:
`AWS::ApiGateway::RestApi` (already in `TREE_TYPES`) and `AWS::ApiGatewayV2::Api`.
`applies_to` accepts either, because from the user's side they are one service.

Keys: `u` (routes), `g` (stages), `n` (how to invoke). Checked against the
baseline `resources.*` (`y j i d`) - see the test.
"""

from __future__ import annotations

from clitka.core import apigw
from clitka.core import preview as pv
from clitka.core.actions import Action, ActionResult, ResourceRef
from clitka.core.context import Context

REST_API = "AWS::ApiGateway::RestApi"
HTTP_API = "AWS::ApiGatewayV2::Api"
TYPES = (REST_API, HTTP_API)


def is_api(ref: ResourceRef) -> bool:
    return ref.type_name in TYPES


def api_id(ref: ResourceRef) -> str:
    """The API id. Cloud Control identifies both types *by* their id."""
    raw = ref.identifier or str(ref.row.get("ApiId") or ref.row.get("RestApiId", ""))
    return apigw.api_id_of(raw)


def _lines(pairs: list[tuple[str, str]]) -> str:
    """Label/value pairs as an aligned block - the shape every tab here uses."""
    if not pairs:
        return "[dim](nothing to show)[/dim]"
    width = max(len(label) for label, _ in pairs)
    return "\n".join(f"[dim]{label:<{width}}[/dim]  {value}" for label, value in pairs)


def _table(rows: list[list[str]]) -> str:
    """A fixed-width block, because a preview tab is plain text."""
    if len(rows) < 2:
        return "[dim](none)[/dim]"
    widths = [max(len(row[col]) for row in rows) for col in range(len(rows[0]))]
    out = ["  ".join(cell.ljust(widths[col]) for col, cell in enumerate(row)) for row in rows]
    out.insert(1, "  ".join("-" * one for one in widths))
    return "\n".join(out)


def show_routes(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: what this API can be called with, and what guards each route."""
    one = apigw.get_api(ctx, api_id(ref))
    found = apigw.list_routes(ctx, one)
    rows = [["METHOD", "PATH", "AUTH", "INTEGRATION"]]
    rows += [[r.method, r.path, r.authorization, r.integration or "-"] for r in found]
    body = _table(rows)
    if found and not any(r.open for r in found):
        body += "\n\n[dim]Every route has an authorizer - an unsigned call will 403.[/dim]"
    return ActionResult(f"{one.label} - {len(found)} routes", body)


def show_stages(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: the deployed stages, and the URL each one serves from.

    An empty list is the answer to "why does my API 403 on everything": it was
    never deployed, so no stage exists to route the request.
    """
    one = apigw.get_api(ctx, api_id(ref))
    found = apigw.list_stages(ctx, one)
    rows = [["STAGE", "URL", "DEPLOYMENT", "AUTO"]]
    rows += [
        [s.name, one.invoke_url(s.name), s.deployment_id or "-", "yes" if s.auto_deploy else ""]
        for s in found
    ]
    body = _table(rows)
    if not found:
        body = "[dim]No stages - this API has never been deployed, so nothing is reachable.[/dim]"
    return ActionResult(f"{one.label} - {len(found)} stages", body)


def show_invoke_hint(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: how to call this API - the command, not the call.

    Hands over `clitka apigw invoke`, exactly as `lambda.invoke` and the logs tail
    do, and says up front when the endpoint cannot be reached at all.
    """
    one = apigw.get_api(ctx, api_id(ref))
    refusal = one.refuses_invoke()
    if refusal:
        return ActionResult(f"invoke {one.label}", f"[red]Not reachable:[/red] {refusal}")
    stages = apigw.list_stages(ctx, one)
    stage = stages[0].name if stages else ("$default" if not one.is_rest else "<stage>")
    lines = [
        f"[dim]Base URL[/dim]  {one.invoke_url(stage)}",
        "",
        "Call it with:",
        "",
        f"  clitka apigw invoke {one.api_id} {stage} --path / -X GET",
        "",
        "[dim]--param name=value fills a {placeholder}, -q name=value adds a query,",
        "-H Name=value a header, -b '{...}' a body, --sign signs it for an AWS_IAM",
        "route, and --dry-run prints the request without sending it.[/dim]",
    ]
    if not stages:
        lines.insert(0, "[yellow]No stage is deployed - the URL below is a guess.[/yellow]\n")
    return ActionResult(f"invoke {one.label}", "\n".join(lines))


# The ids are namespaced; the *keys* are not. On a RestApi only the baseline
# `resources.*` (y j i d) also applies, so u / g / n are free - and a test says so.
ACTIONS: tuple[Action, ...] = (
    Action(id="apigw.routes", label="Routes", run=show_routes, key="u", applies_to=is_api),
    Action(id="apigw.stages", label="Stages", run=show_stages, key="g", applies_to=is_api),
    Action(
        id="apigw.invoke",
        label="Invoke (how to)",
        run=show_invoke_hint,
        key="n",
        applies_to=is_api,
    ),
)


def build_routes_tab(ctx: Context, ref: ResourceRef) -> str:
    return show_routes(ctx, ref).body


def build_stages_tab(ctx: Context, ref: ResourceRef) -> str:
    return show_stages(ctx, ref).body


PREVIEWS: tuple[pv.PreviewTab, ...] = (
    pv.PreviewTab(
        id="apigw.routes",
        label="Routes",
        build=build_routes_tab,
        applies_to=is_api,
        lazy=True,  # it calls GetResources / GetRoutes
    ),
    pv.PreviewTab(
        id="apigw.stages",
        label="Stages",
        build=build_stages_tab,
        applies_to=is_api,
        lazy=True,
    ),
)


def _self_check() -> None:
    rest = ResourceRef.from_row(REST_API, {"identifier": "abc123"})
    http = ResourceRef.from_row(HTTP_API, {"identifier": "def456"})
    assert is_api(rest) and is_api(http)
    assert not is_api(ResourceRef.from_row("AWS::S3::Bucket", {}))
    assert api_id(rest) == "abc123"
    # Cloud Control sometimes reports the id as a property, or as an ARN.
    assert api_id(ResourceRef(REST_API, "", {"RestApiId": "other"})) == "other"
    assert api_id(ResourceRef(HTTP_API, "", {"ApiId": "v2id"})) == "v2id"
    arn = "arn:aws:apigateway:eu-central-1::/restapis/from-arn"
    assert api_id(ResourceRef(REST_API, arn, {})) == "from-arn"

    ids = [action.id for action in ACTIONS]
    keys = [action.key for action in ACTIONS]
    assert len(set(ids)) == len(ids) and len(set(keys)) == len(keys), keys
    assert all(action.applies_to(rest) and action.applies_to(http) for action in ACTIONS)
    # Nothing here mutates - invoking is deliberately not an action.
    assert not any(action.destructive for action in ACTIONS)
    assert not any(action.id == "apigw.call" for action in ACTIONS)

    # The F9 key namespace is global per resource: `ActionMenu.on_key` runs the
    # FIRST match, so a shared key would silently run somebody else's action.
    from clitka.services.ec2.actions import ACTIONS as EC2
    from clitka.services.ecr.actions import ACTIONS as ECR
    from clitka.services.ecs.actions import ACTIONS as ECS
    from clitka.services.resources.actions import ACTIONS as BASELINE

    for ref in (rest, http):
        taken = [
            one.key
            for one in (*ACTIONS, *BASELINE, *EC2, *ECR, *ECS)
            if one.key and one.applies_to(ref)
        ]
        assert len(set(taken)) == len(taken), (ref.type_name, sorted(taken))

    assert _lines([("a", "1"), ("bbb", "2")]).count("\n") == 1
    assert "nothing to show" in _lines([])
    # A table with only its header row has no rows to show.
    assert "(none)" in _table([["A", "B"]]) and "(none)" in _table([])
    assert _table([["A", "B"], ["1", "2"]]).count("\n") == 2

    assert [tab.id for tab in PREVIEWS] == ["apigw.routes", "apigw.stages"]
    assert all(tab.lazy and tab.matches_type(REST_API) for tab in PREVIEWS)
    print("[OK] apigw actions self-check passed")


if __name__ == "__main__":
    _self_check()
