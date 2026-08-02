"""SSM documents: the listing and the content.

The API side of `core/ssmrunbook.py` (what a document is). Sending one lives in
`core/ssmrun.py` and is **re-exported here**, so callers import one module.

Two things about this API shaped the module:

- **`ListDocuments` returns every AWS-owned document too** - hundreds of them, and
  they bury the handful anyone in the account actually wrote. `Owner=Self` is the
  filter that matters, so `mine=True` is the *default* here even though the API's
  own default is everything.
- **The parameters a document declares only come from `DescribeDocument`**, not
  from the listing - which is why `ssmrun.run()` has to describe first. Not a
  wasted call: it is the same one that answers "is this even a Command document?".
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

from clitka.core.context import Context
from clitka.core.errors import wrap_aws_errors

# Sending a document belongs to this module's public surface even though the
# 8 kB rule put it next door.
from clitka.core.ssmrun import invocation, run, wait_for
from clitka.core.ssmrunbook import Document, DocumentParameter

__all__ = [
    "PAGE",
    "Document",
    "get_document",
    "invocation",
    "iter_documents",
    "list_documents",
    "run",
    "wait_for",
]


PAGE = 50


def _client(ctx: Context) -> Any:
    return ctx.client("ssm")


def _moment(when: Any) -> dt.datetime | None:
    return when if isinstance(when, dt.datetime) else None


def _from_listing(raw: dict[str, Any]) -> Document:
    """One `ListDocuments` entry. No parameters and no content - those need a describe."""
    return Document(
        name=str(raw.get("Name", "")),
        document_type=str(raw.get("DocumentType", "")),
        document_format=str(raw.get("DocumentFormat", "")),
        owner=str(raw.get("Owner", "")),
        version=str(raw.get("DocumentVersion", "")),
        platform_types=tuple(str(one) for one in raw.get("PlatformTypes", [])),
        target_type=str(raw.get("TargetType", "")),
        created=_moment(raw.get("CreatedDate")),
    )


def _parameters_of(raw: dict[str, Any]) -> tuple[DocumentParameter, ...]:
    """`DescribeDocument`'s `Parameters`. A missing `DefaultValue` means required."""
    out: list[DocumentParameter] = []
    for one in raw.get("Parameters", []) or []:
        out.append(
            DocumentParameter(
                name=str(one.get("Name", "")),
                type=str(one.get("Type", "String")),
                description=str(one.get("Description", "")),
                # None, not "": an absent DefaultValue is what makes it required,
                # and an empty one is a real default. Not interchangeable.
                default=one.get("DefaultValue"),
            )
        )
    return tuple(out)


# --- listing ---------------------------------------------------------------


def iter_documents(ctx: Context, mine: bool = True, kind: str = "") -> Iterator[Document]:
    """Yield documents, page by page. `mine` keeps AWS's own hundreds out."""
    client = _client(ctx)
    filters: list[dict[str, Any]] = []
    if mine:
        filters.append({"Key": "Owner", "Values": ["Self"]})
    if kind:
        filters.append({"Key": "DocumentType", "Values": [kind]})
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"MaxResults": PAGE}
        if filters:
            kwargs["Filters"] = filters
        if token:
            kwargs["NextToken"] = token
        page = _list_page(ctx, client, kwargs)
        for raw in page.get("DocumentIdentifiers", []):
            yield _from_listing(raw)
        token = page.get("NextToken")
        if not token:
            return


@wrap_aws_errors
def _list_page(ctx: Context, client: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    return client.list_documents(**kwargs)


def list_documents(
    ctx: Context, mine: bool = True, kind: str = "", limit: int | None = None
) -> list[Document]:
    """Eager variant for the CLI, sorted by name."""
    found = sorted(iter_documents(ctx, mine=mine, kind=kind), key=lambda one: one.name.casefold())
    return found if limit is None else found[:limit]


def get_document(ctx: Context, name: str, with_content: bool = False) -> Document:
    """One document in full - **this is the only call that knows its parameters**."""
    raw = _describe_call(ctx, name)
    one = _from_listing(
        {
            "Name": raw.get("Name", name),
            "DocumentType": raw.get("DocumentType", ""),
            "DocumentFormat": raw.get("DocumentFormat", ""),
            "Owner": raw.get("Owner", ""),
            "DocumentVersion": raw.get("DocumentVersion", ""),
            "PlatformTypes": raw.get("PlatformTypes", []),
            "TargetType": raw.get("TargetType", ""),
            "CreatedDate": raw.get("CreatedDate"),
        }
    )
    content = _content_call(ctx, name).get("Content", "") if with_content else ""
    return replace(
        one,
        status=str(raw.get("Status", "")),
        description=str(raw.get("Description", "")),
        parameters=_parameters_of(raw),
        content=str(content),
    )


@wrap_aws_errors
def _describe_call(ctx: Context, name: str) -> dict[str, Any]:
    answer = _client(ctx).describe_document(Name=name)
    return answer.get("Document") or {}


@wrap_aws_errors
def _content_call(ctx: Context, name: str) -> dict[str, Any]:
    # GetDocument is a separate call from DescribeDocument: the first returns the
    # body, the second the metadata, and neither returns both.
    return _client(ctx).get_document(Name=name)


def _self_check() -> None:

    listed = _from_listing(
        {
            "Name": "AWS-RunShellScript",
            "DocumentType": "Command",
            "Owner": "Amazon",
            "PlatformTypes": ["Linux", "MacOS"],
        }
    )
    assert listed.runnable and listed.aws_owned
    # A listing entry knows no parameters - that is what forces the describe.
    assert listed.parameters == () and listed.required == ()
    assert _from_listing({}).name == ""

    # A missing DefaultValue means required; an empty one does not.
    params = _parameters_of(
        {
            "Parameters": [
                {"Name": "commands", "Type": "StringList"},
                {"Name": "workingDirectory", "Type": "String", "DefaultValue": ""},
            ]
        }
    )
    assert [one.name for one in params] == ["commands", "workingDirectory"]
    assert params[0].required and not params[1].required
    assert _parameters_of({}) == () and _parameters_of({"Parameters": None}) == ()

    assert PAGE <= 50
    # Running a document must be reachable from here, or a caller would have to
    # know which of the two files each name lives in.
    assert {"run", "invocation", "wait_for"} <= set(__all__)
    assert callable(run) and callable(wait_for)

    print("[OK] ssm documents self-check passed")


if __name__ == "__main__":
    _self_check()
