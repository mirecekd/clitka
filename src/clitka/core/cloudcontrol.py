"""Cloud Control API: one generic explorer for every CFN resource type.

This is the cheapest breadth CLITKA can buy - `cloudcontrol` speaks the same
four verbs (list / get / update / delete) for any resource type registered with
CloudFormation, so no per-service code is needed to browse things.

Two honest caveats, surfaced rather than hidden:

- Child types need a parent identifier in `ResourceModel` (a subnet id, say), and
  some types cannot be listed at all. `AdditionalInputsError` turns the API's
  `InvalidRequestException` into "which extra inputs are missing".
- Enumerating types needs `cloudformation:ListTypes`, which not every identity
  has. The caller gets a normal AwsError with the IAM hint.

Properties are masked on the way in - see `core/redact.py`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

# `Resource` and `columns_for` are the boto3-free half, split out for the 8 kB
# rule and re-exported here so every caller still imports one module.
from clitka.core.ccmodel import Resource, columns_for
from clitka.core.context import Context
from clitka.core.errors import ClitkaError, wrap_aws_errors
from clitka.core.redact import redact

__all__ = [
    "AdditionalInputsError",
    "Resource",
    "columns_for",
    "delete_resource",
    "get_resource",
    "iter_resources",
    "list_resources",
    "list_types",
]


_PAGE = 100
# ponytail: types the API cannot enumerate are discovered by failing the call,
# not by shipping a hand-maintained allow-list. Ceiling: one wasted round trip
# per unsupported type; upgrade path is caching the failures per region.
_NEEDS_PARENT = ("InvalidRequestException", "UnsupportedActionException")


class AdditionalInputsError(ClitkaError):
    """The type cannot be listed without extra identifiers (parent ids etc.)."""

    def __init__(self, type_name: str, message: str) -> None:
        self.type_name = type_name
        self.aws_message = message
        super().__init__(
            f"{type_name} cannot be listed on its own: {message}\n"
            "Hint: pass the parent identifier, e.g. "
            '--input \'{"VpcId": "vpc-1234"}\''
        )


def _parse_properties(type_name: str, raw: str | None) -> dict[str, Any]:
    """The `Properties` JSON as a dict, **with anything secret masked**.

    The listing and `get_resource` both come through here, which is the point -
    see `core/redact.py` for the leak that made it necessary.
    """

    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"Properties": raw}
    if not isinstance(parsed, dict):
        return {"Properties": parsed}
    return redact(type_name, parsed)


def _client(ctx: Context) -> Any:
    return ctx.client("cloudcontrol")


@wrap_aws_errors
def list_types(ctx: Context, visibility: str = "PUBLIC") -> list[dict[str, Any]]:
    """Every resource type Cloud Control could know about, sorted by name.

    Needs `cloudformation:ListTypes`.
    """
    cfn = ctx.client("cloudformation")
    paginator = cfn.get_paginator("list_types")
    rows: list[dict[str, Any]] = []
    for page in paginator.paginate(Visibility=visibility, Type="RESOURCE"):
        for summary in page.get("TypeSummaries", []):
            rows.append(
                {
                    "type_name": summary.get("TypeName", ""),
                    "description": (summary.get("Description") or "").split("\n")[0][:80],
                    "last_updated": summary.get("LastUpdated"),
                }
            )
    rows.sort(key=lambda row: row["type_name"])
    return rows


def iter_resources(
    ctx: Context,
    type_name: str,
    resource_model: dict[str, Any] | None = None,
    page_size: int = _PAGE,
) -> Iterator[Resource]:
    """Yield resources page by page, so the TUI can fill the table lazily."""
    client = _client(ctx)
    kwargs: dict[str, Any] = {"TypeName": type_name, "MaxResults": page_size}
    if resource_model:
        kwargs["ResourceModel"] = json.dumps(resource_model)
    token: str | None = None
    while True:
        if token:
            kwargs["NextToken"] = token
        page = _list_page(ctx, client, type_name, kwargs)
        for description in page.get("ResourceDescriptions", []):
            yield Resource(
                type_name=type_name,
                identifier=str(description.get("Identifier", "")),
                properties=_parse_properties(type_name, description.get("Properties")),
            )

        token = page.get("NextToken")
        if not token:
            return


@wrap_aws_errors
def _list_page(
    ctx: Context,  # first, so wrap_aws_errors can name the profile and region
    client: Any,
    type_name: str,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    try:
        return client.list_resources(**kwargs)
    except client.exceptions.ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in _NEEDS_PARENT:
            raise AdditionalInputsError(
                type_name, exc.response.get("Error", {}).get("Message", str(exc))
            ) from exc
        raise


def list_resources(
    ctx: Context,
    type_name: str,
    resource_model: dict[str, Any] | None = None,
    limit: int | None = None,
) -> list[Resource]:
    """Eager variant for the CLI; `limit` stops after that many resources."""
    out: list[Resource] = []
    for resource in iter_resources(ctx, type_name, resource_model):
        out.append(resource)
        if limit is not None and len(out) >= limit:
            break
    return out


@wrap_aws_errors
def get_resource(ctx: Context, type_name: str, identifier: str) -> Resource:
    """Full properties of a single resource."""
    response = _client(ctx).get_resource(TypeName=type_name, Identifier=identifier)
    description = response.get("ResourceDescription", {})
    return Resource(
        type_name=type_name,
        identifier=str(description.get("Identifier", identifier)),
        properties=_parse_properties(type_name, description.get("Properties")),
    )


@wrap_aws_errors
def delete_resource(ctx: Context, type_name: str, identifier: str) -> dict[str, Any]:
    """Delete a resource. Destructive: the caller must confirm first."""
    ctx.require_write(f"delete {type_name} {identifier}")
    response = _client(ctx).delete_resource(TypeName=type_name, Identifier=identifier)
    event = response.get("ProgressEvent", {})
    return {
        "type_name": type_name,
        "identifier": identifier,
        "operation": event.get("Operation", "DELETE"),
        "status": event.get("OperationStatus", "UNKNOWN"),
        "request_token": event.get("RequestToken", ""),
        "status_message": event.get("StatusMessage", ""),
        "error_code": event.get("ErrorCode", ""),
    }
