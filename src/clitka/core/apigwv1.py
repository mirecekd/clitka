"""The `apigateway` half: REST APIs.

The mirror of `core/apigwv2.py`, and split out for the same reason - **these are
two unrelated AWS services** behind one console page, so each gets its own module
and `core/apigw.py` only dispatches.

Two traps live here and nowhere else:

- **`get_stages` answers with `item`, singular** - the only call in this client
  that does. A caller reading `items` sees no stages and concludes the API was
  never deployed, which is the most misleading wrong answer available.
- **A REST resource is not a route.** v1 returns *resources* that each carry a
  dict of methods, so one resource is several callable things. `embed=["methods"]`
  is what makes that one call instead of one `get_method` per method.

And the reason `region` is threaded through everything: **v1 never states the
API's URL.** v2 hands back `ApiEndpoint`; here it has to be assembled from the id,
the region and the stage.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Any

from clitka.core.apigwmodel import REST, Api, Route, Stage
from clitka.core.context import Context
from clitka.core.errors import wrap_aws_errors

__all__ = ["api_from", "get_one", "integration_of", "iter_apis", "list_routes", "list_stages"]


def _moment(when: Any) -> dt.datetime | None:
    """boto3 hands back datetimes; anything else is discarded rather than trusted."""
    return when if isinstance(when, dt.datetime) else None


def api_from(raw: dict[str, Any], region: str) -> Api:
    """One `get_rest_apis` item. v1 never states an endpoint - hence `region`."""
    config = raw.get("endpointConfiguration") or {}
    types = config.get("types") or []
    return Api(
        api_id=str(raw.get("id", "")),
        name=str(raw.get("name", "")),
        kind=REST,
        description=str(raw.get("description", "")),
        created=_moment(raw.get("createdDate")),
        endpoint_type=str(types[0]) if types else "",
        execute_api_disabled=bool(raw.get("disableExecuteApiEndpoint", False)),
        version=str(raw.get("version", "")),
        region=region,
    )


def iter_apis(ctx: Context, page_size: int) -> Iterator[Api]:
    """Yield every REST API. v1 pages on `position`, not a NextToken."""
    client = ctx.client("apigateway")
    region = ctx.effective_region or ""
    kwargs: dict[str, Any] = {"limit": page_size}
    while True:
        page = _call(client, "get_rest_apis", kwargs)
        for raw in page.get("items", []):
            yield api_from(raw, region)
        position = page.get("position")
        if not position:
            return
        kwargs["position"] = position


def get_one(ctx: Context, api_id: str) -> Api:
    """One REST API by id. Raises whatever the API said - the caller decides."""
    raw = _call(ctx.client("apigateway"), "get_rest_api", {"restApiId": api_id})
    return api_from(raw, ctx.effective_region or "")


def list_routes(ctx: Context, api_id: str) -> list[Route]:
    """The methods of every resource, flattened.

    A resource with no methods is a path segment only and contributes nothing.
    """
    client = ctx.client("apigateway")
    kwargs: dict[str, Any] = {"restApiId": api_id, "limit": 500, "embed": ["methods"]}
    found: list[Route] = []
    while True:
        page = _call(client, "get_resources", kwargs)
        for raw in page.get("items", []):
            path = str(raw.get("path", ""))
            for method, detail in (raw.get("resourceMethods") or {}).items():
                found.append(
                    Route(
                        method=str(method),
                        path=path,
                        resource_id=str(raw.get("id", "")),
                        authorization=str((detail or {}).get("authorizationType", "NONE")),
                        integration=integration_of(detail),
                    )
                )
        position = page.get("position")
        if not position:
            break
        kwargs["position"] = position
    return sorted(found, key=lambda one: (one.path, one.method))


def integration_of(detail: Any) -> str:
    """The integration kind, when `embed` brought it along. Never a failure."""
    if not isinstance(detail, dict):
        return ""
    integration = detail.get("methodIntegration") or {}
    return str(integration.get("type", ""))


def list_stages(ctx: Context, api_id: str) -> list[Stage]:
    """The stages of a REST API - reading **`item`**, which is what AWS answers."""
    page = _call(ctx.client("apigateway"), "get_stages", {"restApiId": api_id})
    raws = page.get("item") or page.get("items") or []
    return [
        Stage(
            name=str(raw.get("stageName", "")),
            description=str(raw.get("description", "")),
            deployment_id=str(raw.get("deploymentId", "")),
            updated=_moment(raw.get("lastUpdatedDate")),
            tracing=bool(raw.get("tracingEnabled", False)),
            variables=dict(raw.get("variables") or {}),
        )
        for raw in raws
    ]


@wrap_aws_errors
def _call(client: Any, operation: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    return getattr(client, operation)(**kwargs)


def _self_check() -> None:
    one = api_from(
        {
            "id": "abc123",
            "name": "pets",
            "endpointConfiguration": {"types": ["REGIONAL"]},
            "description": "the pet store",
        },
        "eu-central-1",
    )
    assert one.api_id == "abc123" and one.kind == REST and one.is_rest
    assert one.endpoint_type == "REGIONAL" and one.region == "eu-central-1"
    # v1 states no endpoint, so the URL has to be built from the id and region.
    assert one.api_endpoint == ""
    assert "abc123.execute-api.eu-central-1" in one.invoke_url("prod")
    # An API with no endpointConfiguration at all must not crash.
    assert api_from({"id": "a"}, "eu-1").endpoint_type == ""
    assert api_from({}, "").api_id == ""
    # A non-datetime createdDate must not reach `.strftime()` later.
    assert api_from({"id": "a", "createdDate": "2026-08-01"}, "").created is None

    assert integration_of({"methodIntegration": {"type": "AWS_PROXY"}}) == "AWS_PROXY"
    # `embed` may not have brought the integration along, and that is not an error.
    assert integration_of(None) == "" and integration_of({}) == ""
    print("[OK] apigw v1 self-check passed")


if __name__ == "__main__":
    _self_check()
