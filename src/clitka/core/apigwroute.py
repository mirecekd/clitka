"""A route, a stage, and the path-template arithmetic. No boto3, no socket.

Split out of `apigwmodel.py` for the 8 kB rule, and it landed on a real seam: an
`Api` is a *thing*, while a `Route` and a `Stage` are what makes it *callable* -
and `fill_path()` is the piece both the invoke half and the TUI need.

**`{petId}` in a route is not a URL.** A path template has to have its parameters
filled in before anything can be sent, and the *missing* ones are the useful
complaint: a request to a literal `/pets/{petId}` gets a 403 from API Gateway
that names neither the parameter nor the mistake.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

from clitka.core.logsmodel import stamp

__all__ = ["Route", "Stage", "fill_path", "path_params"]

# `{petId}` and the greedy `{proxy+}` API Gateway uses for a catch-all.
_PARAM = re.compile(r"\{([A-Za-z0-9._-]+)(\+?)\}")


@dataclass(frozen=True)
class Route:
    """One callable thing: a REST resource+method, or an HTTP API route."""

    method: str
    path: str
    resource_id: str = ""
    route_id: str = ""
    authorization: str = "NONE"
    integration: str = ""

    @property
    def label(self) -> str:
        return f"{self.method} {self.path}"

    @property
    def open(self) -> bool:
        """True when nothing but the network stands between a caller and this."""
        return self.authorization in ("", "NONE")

    @property
    def parameters(self) -> list[str]:
        return path_params(self.path)

    def row(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "path": self.path,
            "auth": self.authorization,
            "integration": self.integration,
            "identifier": self.resource_id or self.route_id,
        }


@dataclass(frozen=True)
class Stage:
    """A deployed stage - which is what makes an API reachable at all."""

    name: str
    description: str = ""
    deployment_id: str = ""
    updated: dt.datetime | None = None
    auto_deploy: bool = False  # v2 only
    tracing: bool = False
    variables: dict[str, str] = field(default_factory=dict)

    def row(self) -> dict[str, Any]:
        return {
            "identifier": self.name,
            "description": self.description,
            "deployment": self.deployment_id,
            "auto_deploy": "yes" if self.auto_deploy else "",
            "updated": stamp(self.updated),
        }


def path_params(template: str) -> list[str]:
    """The names a path template wants filled in, in the order they appear."""
    return [name for name, _ in _PARAM.findall(template)]


def fill_path(template: str, params: dict[str, str] | None = None) -> tuple[str, list[str]]:
    """`("/pets/3", [])` for a filled template, or the names still missing.

    Both halves are returned because the missing names are the whole complaint.
    An unfilled template is handed back untouched, so the caller can show what it
    could not fill rather than sending a literal `{petId}`.
    """
    given = params or {}
    missing: list[str] = []

    def _one(match: re.Match[str]) -> str:
        name = match.group(1)
        value = given.get(name)
        if value in (None, ""):
            missing.append(name)
            return match.group(0)
        # A greedy `{proxy+}` takes a whole sub-path, so its slashes are trimmed
        # rather than doubled against the ones already in the template.
        return str(value).strip("/") if match.group(2) else str(value)

    return _PARAM.sub(_one, template), missing


def _self_check() -> None:
    route = Route("GET", "/pets/{petId}", resource_id="r1")
    assert route.label == "GET /pets/{petId}" and route.open
    assert route.parameters == ["petId"]
    assert route.row()["identifier"] == "r1"
    # An authorizer of any kind means this is not open.
    assert not Route("GET", "/", authorization="AWS_IAM").open
    assert not Route("GET", "/", authorization="CUSTOM").open
    # An HTTP API route carries a RouteId instead of a ResourceId.
    assert Route("GET", "/", route_id="r2").row()["identifier"] == "r2"

    assert Stage("prod", auto_deploy=True).row()["auto_deploy"] == "yes"
    assert Stage("prod").row()["identifier"] == "prod" and Stage("prod").row()["auto_deploy"] == ""

    filled, missing = fill_path("/pets/{petId}/toys/{toyId}", {"petId": "3", "toyId": "9"})
    assert filled == "/pets/3/toys/9" and missing == []
    filled, missing = fill_path("/pets/{petId}", {})
    assert filled == "/pets/{petId}" and missing == ["petId"]
    # An empty value counts as missing - it would build `/pets/` otherwise.
    assert fill_path("/pets/{petId}", {"petId": ""})[1] == ["petId"]
    assert fill_path("/{proxy+}", {"proxy": "/a/b/"})[0] == "/a/b"
    assert fill_path("/pets", None) == ("/pets", [])
    assert path_params("/pets/{petId}") == ["petId"] and path_params("/pets") == []
    print("[OK] apigw route self-check passed")


if __name__ == "__main__":
    _self_check()
