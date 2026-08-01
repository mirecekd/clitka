"""The `apigatewayv2` half: HTTP and WebSocket APIs.

Split out of `core/apigw.py` for the 8 kB rule, and the seam is not arbitrary -
**this is a different AWS service from the one behind a REST API**, with its own
client, its own pagination and its own spelling for every field:

| | REST (`apigateway`) | here (`apigatewayv2`) |
|---|---|---|
| list | `get_rest_apis` | `get_apis` |
| items | `items` | `Items` |
| paging | `position` | `NextToken` |
| the URL | not returned at all | `ApiEndpoint` |

`core/apigw.py` owns the v1 half and dispatches to this one; nothing else imports
this module directly.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Any

from clitka.core.apigwmodel import HTTP, Api, Route, Stage
from clitka.core.context import Context
from clitka.core.errors import wrap_aws_errors

__all__ = ["api_from", "get_one", "iter_apis", "list_routes", "list_stages", "split_route_key"]


def _moment(when: Any) -> dt.datetime | None:
    """boto3 hands back datetimes; anything else is discarded rather than trusted."""
    return when if isinstance(when, dt.datetime) else None


def api_from(raw: dict[str, Any], region: str) -> Api:
    """One `get_apis` item. v2 states its own endpoint, so no arithmetic is needed."""
    return Api(
        api_id=str(raw.get("ApiId", "")),
        name=str(raw.get("Name", "")),
        kind=str(raw.get("ProtocolType", HTTP)),
        description=str(raw.get("Description", "")),
        created=_moment(raw.get("CreatedDate")),
        api_endpoint=str(raw.get("ApiEndpoint", "")),
        execute_api_disabled=bool(raw.get("DisableExecuteApiEndpoint", False)),
        version=str(raw.get("Version", "")),
        region=region,
    )


def iter_apis(ctx: Context, page_size: int) -> Iterator[Api]:
    """Yield every HTTP and WebSocket API in the region."""
    client = ctx.client("apigatewayv2")
    region = ctx.effective_region or ""
    kwargs: dict[str, Any] = {"MaxResults": str(page_size)}  # v2 wants it as a string
    while True:
        page = _call(client, "get_apis", kwargs)
        for raw in page.get("Items", []):
            yield api_from(raw, region)
        token = page.get("NextToken")
        if not token:
            return
        kwargs["NextToken"] = token


def get_one(ctx: Context, api_id: str) -> Api:
    """One API by id. Raises whatever the API said - the caller decides."""
    raw = _call(ctx.client("apigatewayv2"), "get_api", {"ApiId": api_id})
    return api_from(raw, ctx.effective_region or "")


def list_routes(ctx: Context, api_id: str) -> list[Route]:
    """The routes of an HTTP or WebSocket API.

    A route key is `"GET /pets"` - or `"$default"` / `"$connect"`, which have no
    method at all. Those keep the key as the path so nothing is silently dropped.
    """
    client = ctx.client("apigatewayv2")
    kwargs: dict[str, Any] = {"ApiId": api_id, "MaxResults": "500"}
    found: list[Route] = []
    while True:
        page = _call(client, "get_routes", kwargs)
        for raw in page.get("Items", []):
            method, path = split_route_key(str(raw.get("RouteKey", "")))
            found.append(
                Route(
                    method=method,
                    path=path,
                    route_id=str(raw.get("RouteId", "")),
                    authorization=str(raw.get("AuthorizationType", "NONE")),
                    integration=str(raw.get("Target", "")),
                )
            )
        token = page.get("NextToken")
        if not token:
            break
        kwargs["NextToken"] = token
    return sorted(found, key=lambda one: (one.path, one.method))


def split_route_key(key: str) -> tuple[str, str]:
    """`"GET /pets"` as `("GET", "/pets")`; `"$default"` keeps its key as the path."""
    if " " in key:
        method, _, path = key.partition(" ")
        return method, path
    return ("ANY", key) if key.startswith("$") else ("ANY", key or "/")


def list_stages(ctx: Context, api_id: str) -> list[Stage]:
    """The stages of an HTTP or WebSocket API. `AutoDeploy` is a v2-only idea."""
    page = _call(ctx.client("apigatewayv2"), "get_stages", {"ApiId": api_id})
    return [
        Stage(
            name=str(raw.get("StageName", "")),
            description=str(raw.get("Description", "")),
            deployment_id=str(raw.get("DeploymentId", "")),
            updated=_moment(raw.get("LastUpdatedDate")),
            auto_deploy=bool(raw.get("AutoDeploy", False)),
            variables=dict(raw.get("StageVariables") or {}),
        )
        for raw in page.get("Items", [])
    ]


@wrap_aws_errors
def _call(client: Any, operation: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    return getattr(client, operation)(**kwargs)


def _self_check() -> None:
    one = api_from(
        {
            "ApiId": "def456",
            "Name": "orders",
            "ProtocolType": "HTTP",
            "ApiEndpoint": "https://def456.execute-api.eu-central-1.amazonaws.com",
        },
        "eu-central-1",
    )
    assert one.kind == HTTP and not one.is_rest
    # v2 states the endpoint, so no host has to be assembled.
    assert one.invoke_url("$default", "/orders").endswith(".amazonaws.com/orders")
    assert api_from({"ApiId": "a", "ProtocolType": "WEBSOCKET"}, "").kind == "WEBSOCKET"
    assert api_from({}, "").kind == HTTP  # the default when AWS omits it
    # A non-datetime CreatedDate must not reach `.strftime()` later.
    assert api_from({"ApiId": "a", "CreatedDate": "2026-08-01"}, "").created is None

    # A route key is a method and a path - except when it is neither.
    assert split_route_key("GET /pets") == ("GET", "/pets")
    assert split_route_key("$default") == ("ANY", "$default")
    assert split_route_key("$connect") == ("ANY", "$connect")
    assert split_route_key("") == ("ANY", "/")
    assert split_route_key("ANY /{proxy+}") == ("ANY", "/{proxy+}")
    print("[OK] apigw v2 self-check passed")


if __name__ == "__main__":
    _self_check()
