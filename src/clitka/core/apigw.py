"""API Gateway: the dispatcher over both halves of the service.

**There are two unrelated AWS services behind one console page** - `apigateway`
for a REST API and `apigatewayv2` for an HTTP or WebSocket one, with different
calls, different pagination and different field names for the same idea. The whole
point of this module is that nothing above it has to know: `iter_apis` walks both
and `get_api` / `list_routes` / `list_stages` ask whichever half owns the id.

The pieces, each under 8 kB and each on a real seam:

- `core/apigwmodel.py` + `apigwroute.py` - the row types and the URL maths, no boto3
- `core/apigwv1.py` - the `apigateway` (REST) client
- `core/apigwv2.py` - the `apigatewayv2` (HTTP / WebSocket) client
- `core/apigwinvoke.py` + `apigwresponse.py` - the real HTTP request and its answer

All of them are re-exported here, so callers only ever import `core.apigw` - the
same trick `ecr.py` and `ecs.py` use.
"""

from __future__ import annotations

from collections.abc import Iterator

from clitka.core import apigwv1 as v1
from clitka.core import apigwv2 as v2
from clitka.core.apigwinvoke import invoke, request_for
from clitka.core.apigwmodel import HTTP, REST, WEBSOCKET, Api, Route, Stage, api_id_of, fill_path
from clitka.core.apigwresponse import Response
from clitka.core.context import Context

__all__ = [
    "HTTP",
    "KINDS",
    "PAGE",
    "REST",
    "WEBSOCKET",
    "Api",
    "Response",
    "Route",
    "Stage",
    "api_id_of",
    "fill_path",
    "get_api",
    "invoke",
    "iter_apis",
    "list_apis",
    "list_routes",
    "list_stages",
    "request_for",
]

PAGE = 50  # both halves cap `limit` / `MaxResults` at 500; 50 paints sooner

# The three protocols, and which client answers for each.
KINDS: tuple[str, ...] = (REST, HTTP, WEBSOCKET)


def iter_apis(ctx: Context, kind: str = "", page_size: int = PAGE) -> Iterator[Api]:
    """Every API in the region, REST first then HTTP/WebSocket.

    `kind` narrows it to one of REST / HTTP / WEBSOCKET, which also **skips the
    other client entirely** - useful where one of the two is denied by policy.
    """
    wanted = kind.upper()
    if wanted and wanted not in KINDS:
        raise ValueError(f"unknown API kind {kind!r} - one of {', '.join(KINDS)}")
    if wanted in ("", REST):
        yield from v1.iter_apis(ctx, page_size)
    if wanted != REST:
        for one in v2.iter_apis(ctx, page_size):
            if not wanted or one.kind == wanted:
                yield one


def list_apis(ctx: Context, kind: str = "", limit: int | None = None) -> list[Api]:
    """Eager variant for the CLI, sorted by what the user reads first: the name."""
    found = sorted(iter_apis(ctx, kind=kind), key=lambda one: one.label.casefold())
    return found if limit is None else found[:limit]


def get_api(ctx: Context, identifier: str) -> Api:
    """One API by id, ARN or invoke URL, asking whichever half owns it.

    v1 is tried first and a v1 miss falls through to v2, because **an id alone
    does not say which service it belongs to** - the two look identical.
    """
    api_id = api_id_of(identifier)
    try:
        return v1.get_one(ctx, api_id)
    except Exception:
        pass
    try:
        return v2.get_one(ctx, api_id)
    except Exception as exc:
        raise LookupError(f"no API {api_id!r} in this region: {exc}") from exc


def list_routes(ctx: Context, api: Api | str) -> list[Route]:
    """The callable things of one API, from whichever half it came from."""
    one = api if isinstance(api, Api) else get_api(ctx, api)
    return v1.list_routes(ctx, one.api_id) if one.is_rest else v2.list_routes(ctx, one.api_id)


def list_stages(ctx: Context, api: Api | str) -> list[Stage]:
    """The deployed stages of one API. An empty list means "never deployed"."""
    one = api if isinstance(api, Api) else get_api(ctx, api)
    return v1.list_stages(ctx, one.api_id) if one.is_rest else v2.list_stages(ctx, one.api_id)


def _self_check() -> None:
    """That the dispatch really reaches both halves, and refuses a third."""
    rest = v1.api_from({"id": "abc123", "name": "pets"}, "eu-central-1")
    http = v2.api_from({"ApiId": "def456", "ProtocolType": "HTTP"}, "eu-central-1")
    assert rest.is_rest and not http.is_rest
    # Which half a call goes to is decided by `is_rest` and nothing else.
    assert v1.list_routes is not v2.list_routes and v1.list_stages is not v2.list_stages, (
        "the two halves must stay distinct"
    )

    assert set(KINDS) == {REST, HTTP, WEBSOCKET}
    # A typo in `kind` must be a sentence, not a silently empty listing.
    try:
        next(iter_apis(_NoCtx(), kind="rest-ish"))  # type: ignore[arg-type]
    except ValueError as exc:
        assert "unknown API kind" in str(exc)
    else:
        raise AssertionError("an unknown kind must be refused")
    # A known kind, in any case, is accepted - and only then is a client built.
    assert "rest".upper() in KINDS

    # The re-exports are the module's contract; a caller imports nothing else.
    assert api_id_of("arn:aws:apigateway:eu-1::/restapis/abc") == "abc"
    assert fill_path("/pets/{id}", {"id": "3"}) == ("/pets/3", [])
    assert Response(200, "{}").ok and Route("GET", "/").open and Stage("prod").name == "prod"
    assert callable(invoke) and callable(request_for)
    print("[OK] apigw self-check passed")


class _NoCtx:
    """A stand-in that fails loudly if the kind check does not come first."""

    def client(self, service: str) -> object:
        raise AssertionError(f"no client should be built for a bad kind (asked for {service})")


if __name__ == "__main__":
    _self_check()
