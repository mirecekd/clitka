"""`core/apigw*` - the two halves, the URL maths and the invoke.

The things worth guarding are the things API Gateway gets wrong if nobody looks:
the two clients spell everything differently, `get_stages` on v1 answers with
`item` (singular), a REST API never states its own URL, and a 403 saying "Missing
Authentication Token" does not mean what it says.
"""

from __future__ import annotations

import datetime as dt
import urllib.error
import urllib.request

import pytest
from botocore.stub import ANY, Stubber

from clitka.core import apigw
from clitka.core import apigwinvoke as inv
from clitka.core import apigwmodel as am
from clitka.core import apigwresponse as ar
from clitka.core import apigwroute as art
from clitka.core import apigwsign as asign
from clitka.core import apigwv1 as v1
from clitka.core import apigwv2 as v2
from clitka.core.context import Context
from clitka.core.errors import ClitkaError, ReadOnlyError

CREATED = dt.datetime(2026, 7, 1, 9, 30, tzinfo=dt.UTC)


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    return Context(region="eu-central-1")


def rest_item(api_id: str = "abc123", name: str = "pets", **extra) -> dict:
    raw = {
        "id": api_id,
        "name": name,
        "createdDate": CREATED,
        "endpointConfiguration": {"types": ["REGIONAL"]},
    }
    raw.update(extra)
    return raw


def v2_item(api_id: str = "def456", name: str = "orders", protocol: str = "HTTP", **extra) -> dict:
    raw = {
        "ApiId": api_id,
        "Name": name,
        "ProtocolType": protocol,
        "CreatedDate": CREATED,
        "ApiEndpoint": f"https://{api_id}.execute-api.eu-central-1.amazonaws.com",
        # `Stubber` validates the *response* against the shape, and `GetApis`
        # declares this one required - CLITKA never reads it, but a fixture
        # without it is rejected before the call is even made.
        "RouteSelectionExpression": "$request.method $request.path",
    }
    raw.update(extra)
    return raw


def test_the_self_checks_pass():
    for module in (art, am, v1, v2, ar, asign, inv, apigw):
        module._self_check()


# --- the URL maths, which is the whole model ------------------------------


def test_a_rest_url_is_built_because_v1_never_states_one():
    one = am.Api("abc123", region="eu-central-1", endpoint_type="REGIONAL")
    assert one.api_endpoint == ""
    assert one.invoke_url("prod", "/pets") == (
        "https://abc123.execute-api.eu-central-1.amazonaws.com/prod/pets"
    )


def test_an_http_api_uses_the_endpoint_aws_gave_it():
    one = am.Api("def456", kind=am.HTTP, api_endpoint="https://custom.example.com/")
    # The stated endpoint wins, trailing slash and all, and is not re-derived.
    assert one.invoke_url("dev", "/orders") == "https://custom.example.com/dev/orders"


def test_the_default_stage_serves_from_the_root():
    # $default is the one stage that is NOT a path segment - the trap here.
    one = am.Api("def456", kind=am.HTTP, api_endpoint="https://x.example.com")
    assert one.invoke_url("$default", "/orders") == "https://x.example.com/orders"
    assert one.invoke_url("dev", "/orders") == "https://x.example.com/dev/orders"


def test_a_stage_root_grows_no_trailing_slash():
    one = am.Api("abc", region="eu-1")
    assert one.invoke_url("prod").endswith("/prod")
    assert not one.invoke_url("prod").endswith("/")


@pytest.mark.parametrize(
    ("api", "says"),
    [
        (am.Api("", region="eu-1"), "no API id"),
        (am.Api("a", region="eu-1", execute_api_disabled=True), "custom domain"),
        (am.Api("a", region="eu-1", endpoint_type="PRIVATE"), "VPC"),
        (am.Api("a"), "no region"),
    ],
)
def test_an_unreachable_api_says_why_before_anything_is_sent(api, says):
    assert says in api.refuses_invoke()


def test_a_reachable_api_refuses_nothing():
    assert am.Api("a", region="eu-1").refuses_invoke() == ""


@pytest.mark.parametrize(
    ("given", "wanted"),
    [
        ("abc123", "abc123"),
        ("arn:aws:apigateway:eu-central-1::/restapis/abc123", "abc123"),
        ("https://abc123.execute-api.eu-central-1.amazonaws.com/prod", "abc123"),
        ("http://abc123.execute-api.eu-central-1.amazonaws.com", "abc123"),
    ],
)
def test_an_id_is_recovered_from_an_arn_or_a_pasted_url(given, wanted):
    assert am.api_id_of(given) == wanted


def test_a_path_template_is_filled_and_the_missing_names_are_named():
    assert art.fill_path("/pets/{id}", {"id": "3"}) == ("/pets/3", [])
    filled, missing = art.fill_path("/pets/{petId}/toys/{toyId}", {"petId": "3"})
    # The template is left alone, so the caller can show what it could not fill.
    assert missing == ["toyId"] and "{toyId}" in filled


def test_a_greedy_proxy_parameter_does_not_double_the_slashes():
    assert art.fill_path("/{proxy+}", {"proxy": "/a/b/"})[0] == "/a/b"


# --- the REST half --------------------------------------------------------


def test_the_rest_listing_pages_on_position_not_a_token(ctx):
    client = ctx.client("apigateway")
    with Stubber(client) as stub:
        stub.add_response(
            "get_rest_apis",
            {"items": [rest_item("a1")], "position": "more"},
            {"limit": ANY},
        )
        stub.add_response(
            "get_rest_apis",
            {"items": [rest_item("a2")]},
            {"limit": ANY, "position": "more"},
        )
        found = list(v1.iter_apis(ctx, 50))
    assert [one.api_id for one in found] == ["a1", "a2"]
    assert all(one.kind == apigw.REST for one in found)


def test_a_rest_resource_contributes_one_route_per_method(ctx):
    # One resource with two methods is two callable things, and a resource with
    # none is a path segment that contributes nothing.
    client = ctx.client("apigateway")
    with Stubber(client) as stub:
        stub.add_response(
            "get_resources",
            {
                "items": [
                    {"id": "r0", "path": "/"},  # no methods at all
                    {
                        "id": "r1",
                        "path": "/pets",
                        "resourceMethods": {
                            "GET": {
                                "authorizationType": "NONE",
                                "methodIntegration": {"type": "AWS_PROXY"},
                            },
                            "POST": {"authorizationType": "AWS_IAM"},
                        },
                    },
                ]
            },
            {"restApiId": "abc123", "limit": ANY, "embed": ["methods"]},
        )
        found = v1.list_routes(ctx, "abc123")
    assert [one.label for one in found] == ["GET /pets", "POST /pets"]
    assert found[0].open and not found[1].open
    assert found[0].integration == "AWS_PROXY" and found[1].integration == ""


def test_rest_stages_are_read_from_item_singular(ctx):
    # `get_stages` is the only call in this client that answers with `item`.
    # Reading `items` here would report "never deployed" for a live API.
    client = ctx.client("apigateway")
    with Stubber(client) as stub:
        stub.add_response(
            "get_stages",
            {"item": [{"stageName": "prod", "deploymentId": "d1", "tracingEnabled": True}]},
            {"restApiId": "abc123"},
        )
        found = v1.list_stages(ctx, "abc123")
    assert [one.name for one in found] == ["prod"]
    assert found[0].deployment_id == "d1" and found[0].tracing


def test_no_stages_is_an_empty_list_not_an_error(ctx):
    with Stubber(ctx.client("apigateway")) as stub:
        stub.add_response("get_stages", {}, {"restApiId": "abc123"})
        assert v1.list_stages(ctx, "abc123") == []


# --- the HTTP half --------------------------------------------------------


def test_the_v2_listing_follows_a_next_token(ctx):
    client = ctx.client("apigatewayv2")
    with Stubber(client) as stub:
        stub.add_response(
            "get_apis",
            {"Items": [v2_item("b1")], "NextToken": "more"},
            {"MaxResults": ANY},
        )
        stub.add_response(
            "get_apis",
            {"Items": [v2_item("b2", protocol="WEBSOCKET")]},
            {"MaxResults": ANY, "NextToken": "more"},
        )
        found = list(v2.iter_apis(ctx, 50))
    assert [one.api_id for one in found] == ["b1", "b2"]
    assert [one.kind for one in found] == [apigw.HTTP, apigw.WEBSOCKET]


def test_a_route_key_without_a_method_keeps_its_key_as_the_path(ctx):
    # `$default` and `$connect` are real route keys with no method in them.
    with Stubber(ctx.client("apigatewayv2")) as stub:
        stub.add_response(
            "get_routes",
            {
                "Items": [
                    {"RouteId": "r1", "RouteKey": "GET /orders", "AuthorizationType": "NONE"},
                    {"RouteId": "r2", "RouteKey": "$default"},
                ]
            },
            {"ApiId": "def456", "MaxResults": ANY},
        )
        found = v2.list_routes(ctx, "def456")
    assert {one.label for one in found} == {"GET /orders", "ANY $default"}


# --- the dispatcher -------------------------------------------------------


def test_a_listing_walks_both_halves(ctx):
    with Stubber(ctx.client("apigateway")) as one, Stubber(ctx.client("apigatewayv2")) as two:
        one.add_response("get_rest_apis", {"items": [rest_item(name="zeta")]}, {"limit": ANY})
        two.add_response("get_apis", {"Items": [v2_item(name="Alpha")]}, {"MaxResults": ANY})
        found = apigw.list_apis(ctx)
    # Sorted case-folded by name, so the two halves interleave rather than stack.
    assert [one.label for one in found] == ["Alpha", "zeta"]


def test_narrowing_to_rest_never_touches_the_v2_client(ctx):
    with Stubber(ctx.client("apigateway")) as one, Stubber(ctx.client("apigatewayv2")) as two:
        one.add_response("get_rest_apis", {"items": [rest_item()]}, {"limit": ANY})
        found = list(apigw.iter_apis(ctx, kind="REST"))
        # Nothing was armed on v2, so reaching it would have failed the test.
        two.assert_no_pending_responses()
    assert [one.kind for one in found] == [apigw.REST]


def test_narrowing_to_http_filters_out_a_websocket(ctx):
    with Stubber(ctx.client("apigatewayv2")) as stub:
        stub.add_response(
            "get_apis",
            {"Items": [v2_item("b1"), v2_item("b2", protocol="WEBSOCKET")]},
            {"MaxResults": ANY},
        )
        found = list(apigw.iter_apis(ctx, kind="HTTP"))
    assert [one.api_id for one in found] == ["b1"]


def test_an_unknown_kind_is_refused_before_any_client_is_built(ctx):
    with pytest.raises(ValueError, match="unknown API kind"):
        list(apigw.iter_apis(ctx, kind="graphql"))


def test_a_lookup_falls_through_from_v1_to_v2(ctx):
    # An id does not say which service owns it, so v1 is asked first and a miss
    # is a redirect rather than an answer.
    with Stubber(ctx.client("apigateway")) as one, Stubber(ctx.client("apigatewayv2")) as two:
        one.add_client_error("get_rest_api", "NotFoundException", "no such thing", 404)
        two.add_response("get_api", v2_item("def456"), {"ApiId": "def456"})
        found = apigw.get_api(ctx, "def456")
    assert found.kind == apigw.HTTP and found.api_id == "def456"


def test_an_api_in_neither_half_is_a_lookup_error(ctx):
    with Stubber(ctx.client("apigateway")) as one, Stubber(ctx.client("apigatewayv2")) as two:
        one.add_client_error("get_rest_api", "NotFoundException", "nope", 404)
        two.add_client_error("get_api", "NotFoundException", "nope", 404)
        with pytest.raises(LookupError, match="no API 'gone'"):
            apigw.get_api(ctx, "gone")


def test_the_routes_of_an_api_go_to_the_half_that_owns_it(ctx):
    rest = v1.api_from(rest_item(), "eu-central-1")
    with Stubber(ctx.client("apigateway")) as one, Stubber(ctx.client("apigatewayv2")) as two:
        wanted = {"restApiId": ANY, "limit": ANY, "embed": ANY}
        one.add_response("get_resources", {"items": []}, wanted)

        assert apigw.list_routes(ctx, rest) == []
        two.assert_no_pending_responses()  # the v2 client was never asked


# --- the invoke -----------------------------------------------------------


class _Answer:
    """The bare minimum of what `urlopen` hands back."""

    def __init__(self, status: int = 200, body: bytes = b"{}", headers: dict | None = None):
        self.status = status
        self._body = body
        self.headers = headers or {"Content-Type": "application/json"}

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_) -> None:
        return None


def test_an_invoke_sends_the_built_request_and_reports_the_answer(ctx, monkeypatch):
    seen: dict = {}

    def fake(request, timeout=None):
        seen["url"], seen["method"] = request.full_url, request.get_method()
        seen["headers"] = dict(request.headers)
        return _Answer(200, b'{"ok":true}')

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    api = am.Api("abc123", region="eu-central-1")
    answer = inv.invoke(ctx, api, "prod", "GET", "/pets", query={"limit": "2"})
    assert answer.ok and answer.status == 200
    assert seen["url"].endswith("/prod/pets?limit=2") and seen["method"] == "GET"
    assert answer.pretty() == '{\n  "ok": true\n}'


def test_a_non_2xx_is_an_answer_not_an_exception(ctx, monkeypatch):
    def fake(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden",
            {},
            None,  # type: ignore[arg-type]
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    answer = inv.invoke(ctx, am.Api("abc", region="eu-1"), "prod")
    # A 403 from someone's authorizer answers the user's question; it does not raise.
    assert not answer.ok and answer.status == 403


def test_a_broken_connection_does_raise(ctx, monkeypatch):
    def fake(request, timeout=None):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(ClitkaError, match="cannot reach"):
        inv.invoke(ctx, am.Api("abc", region="eu-1"), "prod")


def test_read_only_mode_allows_a_get_and_refuses_a_post(ctx, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=None: _Answer())
    api = am.Api("abc", region="eu-1")
    guarded = Context(region="eu-1", read_only=True)
    # Reading is fine; anything that could change something is not.
    assert inv.invoke(guarded, api, "prod", "GET").ok
    with pytest.raises(ReadOnlyError):
        inv.invoke(guarded, api, "prod", "POST", body="{}")


def test_the_dry_run_builds_the_request_without_a_socket(monkeypatch):
    # Nothing is patched: a real request here would fail the test outright.
    api = am.Api("abc123", region="eu-central-1")
    url, headers, payload = inv.request_for(api, "prod", "POST", "/pets", body='{"a":1}')
    assert url.endswith("/prod/pets")
    assert headers["Content-Type"] == "application/json" and payload == b'{"a":1}'


def test_a_body_on_a_get_is_refused_before_the_call():
    with pytest.raises(ClitkaError, match="carries no body"):
        inv.request_for(am.Api("a", region="eu-1"), "prod", "GET", "/", body="{}")


def test_a_signed_request_carries_an_authorization_header(ctx, monkeypatch):
    seen: dict = {}

    def fake(request, timeout=None):
        seen.update(dict(request.headers))
        return _Answer()

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    answer = inv.invoke(ctx, am.Api("abc", region="eu-1"), "prod", sign=True)
    signed = {name.lower(): value for name, value in seen.items()}
    assert "authorization" in signed and "AWS4-HMAC-SHA256" in signed["authorization"]
    # And the credential scope names execute-api, not apigateway.
    assert "/execute-api/" in signed["authorization"]
    assert answer.signed


# --- the answer -----------------------------------------------------------


def test_the_misleading_403_is_explained():
    said = ar.Response(403, ar.MISLEADING).hint()
    assert "unmatched route" in said
    # It is a different problem once the request was signed.
    assert "execute-api:Invoke" in ar.Response(403, ar.MISLEADING, signed=True).hint()


def test_a_handler_error_has_nothing_added_to_it():
    # A 500 from someone's own code is its own explanation.
    assert ar.Response(500, "boom").hint() == ""
