"""What API Gateway hands back, as things CLITKA can render - and the URL maths.

No boto3 call in here, the same seam as `logsmodel.py` / `ec2model.py` /
`ecsmodel.py`. `core/apigw.py` is the API side and `core/apigwinvoke.py` sends the
request. `Route`, `Stage` and `fill_path()` live in `core/apigwroute.py` (the 8 kB
rule) and are re-exported here, so callers only import `apigwmodel`.

Two things about API Gateway shaped this module:

- **There are two unrelated services behind one console page.** A REST API lives
  in `apigateway` and an HTTP or WebSocket API in `apigatewayv2`, with different
  calls, different pagination and different field names for the same idea. `Api`
  is the one row type both collapse into, and `kind` says which half it came from.
- **A REST API does not tell you its URL.** `apigatewayv2` answers with
  `ApiEndpoint`; v1 answers with nothing at all, so the invoke URL has to be built
  from the id, the region and the stage. That arithmetic is `invoke_url()`, and
  the three ways it cannot work are `refuses_invoke()`.

`stamp` is reused from `logsmodel` rather than written again.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from clitka.core.apigwroute import Route, Stage, fill_path, path_params
from clitka.core.logsmodel import stamp

__all__ = [
    "HTTP",
    "REST",
    "WEBSOCKET",
    "Api",
    "Route",
    "Stage",
    "api_id_of",
    "fill_path",
    "path_params",
    "stamp",
]

REST = "REST"
HTTP = "HTTP"
WEBSOCKET = "WEBSOCKET"


@dataclass(frozen=True)
class Api:
    """One API, whether it came from `apigateway` or `apigatewayv2`."""

    api_id: str
    name: str = ""
    kind: str = REST
    description: str = ""
    created: dt.datetime | None = None
    endpoint_type: str = ""  # v1 only: EDGE / REGIONAL / PRIVATE
    api_endpoint: str = ""  # v2 only: AWS states it outright
    execute_api_disabled: bool = False
    version: str = ""
    region: str = ""

    @property
    def label(self) -> str:
        """What a human calls this API: its name, or the id when it has none."""
        return self.name or self.api_id

    @property
    def is_rest(self) -> bool:
        return self.kind == REST

    def invoke_url(self, stage: str = "", path: str = "/") -> str:
        """The URL a request goes to, built the way each half of the service works.

        v2 states its own endpoint, so that is used verbatim. v1 says nothing, so
        the default `execute-api` host is assembled - which is also why a REST API
        behind a custom domain or a private endpoint cannot be reached this way.
        """
        base = self.api_endpoint or f"https://{self.api_id}.execute-api.{self.region}.amazonaws.com"
        base = base.rstrip("/")
        # An HTTP API's $default stage serves from the root; every other stage,
        # and every REST stage, is a path segment.
        if stage and stage != "$default":
            base = f"{base}/{stage.lstrip('/')}"
        tail = path if path.startswith("/") else f"/{path}"
        return base if tail == "/" else f"{base}{tail}"

    def refuses_invoke(self) -> str:
        """Why the built URL cannot work, or "" when it can.

        A sentence beforehand rather than a connection error afterwards - the same
        rule as `ec2.power()` reading the state first.
        """
        if not self.api_id:
            return "no API id, so there is no URL to call"
        if self.execute_api_disabled:
            return (
                f"{self.label} has the default execute-api endpoint disabled, "
                "so it is only reachable through its custom domain"
            )
        if self.endpoint_type == "PRIVATE":
            return f"{self.label} is a PRIVATE endpoint - reachable only from inside its VPC"
        if not (self.api_endpoint or self.region):
            return "no region known, so the execute-api host cannot be built"
        return ""

    def row(self) -> dict[str, Any]:
        """The explorer table row. `identifier` is the column every screen keys on."""
        return {
            "identifier": self.api_id,
            "name": self.name,
            "kind": self.kind,
            "endpoint": self.endpoint_type or ("regional" if self.kind != REST else ""),
            "created": stamp(self.created),
            "description": self.description,
        }


def api_id_of(identifier: str) -> str:
    """An id, an ARN or an `execute-api` URL reduced to the plain API id.

    Cloud Control identifies a `AWS::ApiGateway::RestApi` by its id, but an ARN
    (`arn:aws:apigateway:eu-central-1::/restapis/abc123`) can arrive from a CLI
    argument and so can a URL someone pasted.
    """
    if identifier.startswith("arn:"):
        return identifier.rstrip("/").rsplit("/", 1)[-1]
    if identifier.startswith(("https://", "http://")):
        host = identifier.split("://", 1)[1].split("/", 1)[0]
        return host.split(".", 1)[0]
    return identifier


def _self_check() -> None:
    rest = Api("abc123", name="pets", kind=REST, region="eu-central-1", endpoint_type="REGIONAL")
    assert rest.label == "pets" and rest.is_rest
    assert rest.invoke_url("prod", "/pets") == (
        "https://abc123.execute-api.eu-central-1.amazonaws.com/prod/pets"
    )
    # The root of a stage keeps the stage but grows no trailing slash.
    assert rest.invoke_url("prod") == "https://abc123.execute-api.eu-central-1.amazonaws.com/prod"
    # A path without its leading slash is still a path.
    assert rest.invoke_url("prod", "pets").endswith("/prod/pets")
    assert rest.refuses_invoke() == ""

    # v2 states its own endpoint, and $default serves from the root.
    http = Api(
        "def456",
        name="orders",
        kind=HTTP,
        api_endpoint="https://def456.execute-api.eu-central-1.amazonaws.com",
    )
    assert not http.is_rest
    assert http.invoke_url("$default", "/orders").endswith(".amazonaws.com/orders")
    assert http.invoke_url("dev", "/orders").endswith("/dev/orders")

    # The four ways an invoke cannot work, each with its own sentence.
    assert "custom domain" in Api("a", execute_api_disabled=True, region="eu-1").refuses_invoke()
    assert "VPC" in Api("a", endpoint_type="PRIVATE", region="eu-1").refuses_invoke()
    assert "no region" in Api("a").refuses_invoke()
    assert "no API id" in Api("").refuses_invoke()

    assert rest.row()["identifier"] == "abc123" and rest.row()["name"] == "pets"
    assert Api("a", kind=HTTP).row()["endpoint"] == "regional"

    assert api_id_of("abc123") == "abc123"
    assert api_id_of("arn:aws:apigateway:eu-central-1::/restapis/abc123") == "abc123"
    assert api_id_of("https://abc123.execute-api.eu-central-1.amazonaws.com/prod") == "abc123"

    # The re-exports have to keep working - callers only import this module.
    assert fill_path("/pets/{id}", {"id": "3"}) == ("/pets/3", [])
    assert Route("GET", "/").open and Stage("prod").name == "prod"
    print("[OK] apigw model self-check passed")


if __name__ == "__main__":
    _self_check()
