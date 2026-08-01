"""Calling an API Gateway endpoint - the one thing `aws apigateway` cannot do.

`aws apigateway test-invoke-method` exists but it **bypasses the whole edge**: no
authorizer, no stage variable, no WAF, and a REST API only. So an invoke here is a
real HTTP request to the real URL, which is why this module exists at all. The
answer, and what each refusal means, is `core/apigwresponse.py`.

Two deliberate choices, both ponytail:

- **`urllib.request` from the stdlib, not `requests`.** One POST with headers is
  four lines of stdlib. Ceiling: no connection reuse, no HTTP/2, no retries.
  Upgrade path: `httpx` behind the same `invoke()` signature.
- **SigV4 signing comes from botocore**, which is already a dependency.

The payload checks are lifted from `core/lambdapayload.py` rather than restated: a
body is validated *before* the request, so a typo costs nothing.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request

from clitka.core.apigwmodel import Api, fill_path
from clitka.core.apigwresponse import Response
from clitka.core.apigwsign import signed_headers
from clitka.core.context import Context
from clitka.core.errors import ClitkaError
from clitka.core.lambdapayload import bad_json, payload_bytes

__all__ = ["MAX_BODY", "TIMEOUT", "Response", "invoke", "request_for"]

# API Gateway's own ceiling on a request body. Saying so beforehand beats a 413.
MAX_BODY = 10 * 1024 * 1024
TIMEOUT = 30.0

# The methods that carry no body. A GET with one is legal HTTP and rejected by
# enough proxies that sending it silently would be a trap.
_BODYLESS = ("GET", "HEAD", "DELETE", "OPTIONS")


def request_for(
    api: Api,
    stage: str,
    method: str = "GET",
    path: str = "/",
    params: dict[str, str] | None = None,
    query: dict[str, str] | None = None,
    body: str | bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[str, dict[str, str], bytes]:
    """The URL, headers and body a call would use - without making it.

    Split out so the CLI can `--dry-run` and the TUI can *show* the request, and
    so every complaint (an unfilled `{petId}`, a malformed body) is raised before
    a socket is opened.
    """
    refusal = api.refuses_invoke()
    if refusal:
        raise ClitkaError(refusal)
    filled, missing = fill_path(path, params)
    if missing:
        raise ClitkaError(
            f"path {path!r} still wants {', '.join(missing)} - pass --param name=value"
        )
    verb = method.upper()
    payload = payload_bytes(body) if body not in (None, "") else b""
    if payload and verb in _BODYLESS:
        raise ClitkaError(f"a {verb} request carries no body - use POST, PUT or PATCH")
    if len(payload) > MAX_BODY:
        raise ClitkaError(f"body is {len(payload)} bytes; API Gateway accepts {MAX_BODY}")
    sent = {"Accept": "application/json"} | dict(headers or {})
    if payload and not any(key.lower() == "content-type" for key in sent):
        # Only claim JSON when it really is JSON - a form body must not be mislabelled.
        sent["Content-Type"] = "application/json" if not bad_json(payload) else "text/plain"
    url = api.invoke_url(stage, filled)
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    return url, sent, payload


def invoke(
    ctx: Context,
    api: Api,
    stage: str,
    method: str = "GET",
    path: str = "/",
    params: dict[str, str] | None = None,
    query: dict[str, str] | None = None,
    body: str | bytes | None = None,
    headers: dict[str, str] | None = None,
    sign: bool = False,
    timeout: float = TIMEOUT,
) -> Response:
    """Call the API for real and return what it said.

    A non-2xx is a `Response`, **not an exception**: a 403 or a 500 from someone's
    handler is an answer to the user's question, the same rule the EC2 power
    refusals follow. Only a broken *connection* raises.

    A method that is not read-only goes through `require_write` first - an invoke
    can create an order or send an email, and read-only mode has to mean it.
    """
    verb = method.upper()
    url, sent, payload = request_for(api, stage, verb, path, params, query, body, headers)
    if verb not in _BODYLESS:
        ctx.require_write(f"{verb} {url}")
    if sign:
        sent = signed_headers(ctx, url, verb, sent, payload)

    request = urllib.request.Request(url, data=payload or None, headers=sent, method=verb)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            status, raw, got = answer.status, answer.read(), dict(answer.headers)
    except urllib.error.HTTPError as exc:
        # A 4xx/5xx is an answer. urllib insists on raising it, so it is unwrapped.
        status, raw, got = exc.code, exc.read(), dict(exc.headers or {})
    except TimeoutError as exc:
        raise ClitkaError(f"{url} did not answer within {timeout:g} s") from exc
    except urllib.error.URLError as exc:
        raise ClitkaError(f"cannot reach {url}: {exc.reason}") from exc
    return Response(
        status=status,
        body=raw.decode("utf-8", errors="replace"),
        headers=got,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        url=url,
        signed=sign,
    )


def _self_check() -> None:
    api = Api("abc123", name="pets", region="eu-central-1", endpoint_type="REGIONAL")

    url, headers, payload = request_for(api, "prod", "GET", "/pets")
    assert url.endswith("/prod/pets") and payload == b""
    assert headers["Accept"] == "application/json"
    # A GET must not silently grow a Content-Type it has no body for.
    assert "Content-Type" not in headers

    url, headers, payload = request_for(api, "prod", "post", "/pets", body='{"a":1}')
    assert payload == b'{"a":1}' and headers["Content-Type"] == "application/json"
    # A non-JSON body is not labelled as JSON.
    assert request_for(api, "prod", "POST", "/", body="a=1")[1]["Content-Type"] == "text/plain"
    # An explicit Content-Type from the caller wins.
    given = {"Content-Type": "application/xml"}
    assert request_for(api, "prod", "POST", "/", body="<a/>", headers=given)[1] == {
        "Accept": "application/json",
        "Content-Type": "application/xml",
    }

    # A path parameter is filled in; a missing one is a sentence beforehand.

    assert request_for(api, "prod", "GET", "/pets/{id}", params={"id": "3"})[0].endswith("/pets/3")
    for method, path, sent_body, says in (
        ("GET", "/pets/{id}", None, "still wants id"),
        ("GET", "/", "{}", "carries no body"),
        ("POST", "/", "x" * (MAX_BODY + 1), "API Gateway accepts"),
    ):
        try:
            request_for(api, "prod", method, path, body=sent_body)
        except ClitkaError as exc:
            assert says in str(exc), (says, exc)
        else:
            raise AssertionError(f"expected a refusal for {method} {path}")

    # An API that cannot be reached is refused before anything is built.
    try:
        request_for(Api("a", endpoint_type="PRIVATE", region="eu-1"), "prod")
    except ClitkaError as exc:
        assert "PRIVATE" in str(exc)
    else:
        raise AssertionError("a private endpoint must be refused")

    assert request_for(api, "prod", "GET", "/pets", query={"limit": "2"})[0].endswith("?limit=2")
    print("[OK] apigw invoke self-check passed")


if __name__ == "__main__":
    _self_check()
