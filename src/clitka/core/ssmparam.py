"""Parameter Store: the listing and the reads.

Generators plus `wrap_aws_errors`, as in `core/ec2.py`; the boto3-free rows are in
`core/ssmmodel.py` and the write half in `core/ssmput.py`, **re-exported here** so
callers import one module (the `ecr.py` / `ecrops.py` arrangement).

Two things about this API shaped the module:

- **`DescribeParameters` never returns a value**, whatever the type - so a listing
  cannot leak a secret even by accident.
- **`WithDecryption` is False unless a caller passes `decrypt=True`**, which only
  the CLI's `--decrypt` does. The tree and the preview pane never do, so a
  `SecureString` reaches the screen as `ssmmodel.MASK`.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Any

from clitka.core.context import Context
from clitka.core.errors import wrap_aws_errors
from clitka.core.ssmmodel import SECURE, Parameter, param_name_of

# The write half lives next door but belongs to this module's public surface, so
# a caller never has to know which of the two files a name came from.
from clitka.core.ssmput import delete_parameter, put_parameter

__all__ = [
    "PAGE",
    "Parameter",
    "by_path",
    "delete_parameter",
    "get_parameter",
    "history",
    "iter_parameters",
    "list_parameters",
    "param_name_of",
    "put_parameter",
]

PAGE = 50  # DescribeParameters caps MaxResults at 50


def _client(ctx: Context) -> Any:
    return ctx.client("ssm")


def _moment(when: Any) -> dt.datetime | None:
    """SSM timestamps arrive as datetimes already; anything else is discarded."""
    return when if isinstance(when, dt.datetime) else None


def _from_metadata(raw: dict[str, Any]) -> Parameter:
    """A `DescribeParameters` entry. No value - that call never returns one."""
    return Parameter(
        name=str(raw.get("Name", "")),
        type=str(raw.get("Type", "")),
        version=int(raw.get("Version", 0) or 0),
        last_modified=_moment(raw.get("LastModifiedDate")),
        last_modified_user=str(raw.get("LastModifiedUser", "")),
        tier=str(raw.get("Tier", "")),
        key_id=str(raw.get("KeyId", "")),
        data_type=str(raw.get("DataType", "")),
        description=str(raw.get("Description", "")),
        allowed_pattern=str(raw.get("AllowedPattern", "")),
    )


def _from_value(raw: dict[str, Any], decrypted: bool) -> Parameter:
    """A `GetParameter` entry, which does carry the value.

    `decrypted` is what the *caller asked for*, not what the response says - the
    response looks identical either way, and only the request knows.
    """
    return Parameter(
        name=str(raw.get("Name", "")),
        type=str(raw.get("Type", "")),
        value=str(raw.get("Value", "")),
        version=int(raw.get("Version", 0) or 0),
        last_modified=_moment(raw.get("LastModifiedDate")),
        data_type=str(raw.get("DataType", "")),
        decrypted=decrypted,
    )


# --- listing ---------------------------------------------------------------


def iter_parameters(ctx: Context, contains: str = "", page_size: int = PAGE) -> Iterator[Parameter]:
    """Yield every parameter's *metadata*, page by page. Never a value.

    `contains` matches locally: `ParameterFilters` only does `BeginsWith` or an
    exact `Equals`, and "the bit I remember" is what a user types.
    """

    client = _client(ctx)
    kwargs: dict[str, Any] = {"MaxResults": page_size}
    wanted = contains.casefold()
    token: str | None = None
    while True:
        if token:
            kwargs["NextToken"] = token
        page = _describe_page(ctx, client, kwargs)
        for raw in page.get("Parameters", []):
            one = _from_metadata(raw)
            if not wanted or wanted in one.name.casefold():
                yield one
        token = page.get("NextToken")
        if not token:
            return


@wrap_aws_errors
def _describe_page(ctx: Context, client: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    return client.describe_parameters(**kwargs)


def list_parameters(ctx: Context, contains: str = "", limit: int | None = None) -> list[Parameter]:
    """Eager variant for the CLI, sorted by name - which is also the path order."""
    found = sorted(iter_parameters(ctx, contains=contains), key=lambda one: one.name.casefold())
    return found if limit is None else found[:limit]


def by_path(
    ctx: Context, path: str, recursive: bool = True, decrypt: bool = False
) -> list[Parameter]:
    """Every parameter under `path`, values included.

    The leading `/` is mandatory in this API and a path without one matches
    nothing *without saying so*, so it is added here.
    """
    wanted = path if path.startswith("/") else "/" + path
    client = _client(ctx)
    out: list[Parameter] = []
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "Path": wanted,
            "Recursive": recursive,
            "WithDecryption": decrypt,
            "MaxResults": 10,  # this call's own cap, and it is 10, not 50
        }
        if token:
            kwargs["NextToken"] = token
        page = _by_path_page(ctx, client, kwargs)
        out.extend(_from_value(raw, decrypt) for raw in page.get("Parameters", []))
        token = page.get("NextToken")
        if not token:
            return sorted(out, key=lambda one: one.name.casefold())


@wrap_aws_errors
def _by_path_page(ctx: Context, client: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    return client.get_parameters_by_path(**kwargs)


def get_parameter(ctx: Context, identifier: str, decrypt: bool = False) -> Parameter:
    """One parameter, by name or ARN. `decrypt` must be asked for explicitly."""
    name = param_name_of(identifier)
    raw = _get_call(ctx, name, decrypt)
    one = raw.get("Parameter") or {}
    if not one:
        raise LookupError(f"no SSM parameter {name!r} in this region")
    return _from_value(one, decrypt)


@wrap_aws_errors
def _get_call(ctx: Context, name: str, decrypt: bool) -> dict[str, Any]:
    return _client(ctx).get_parameter(Name=name, WithDecryption=decrypt)


def history(ctx: Context, identifier: str, limit: int = 10) -> list[Parameter]:
    """Recent versions, newest first. Never decrypted.

    ponytail: the first page only. Ceiling: 50 versions. Upgrade path: follow
    NextToken as `iter_parameters` does.
    """

    name = param_name_of(identifier)
    raw = _history_call(ctx, name)
    found = [_from_value(one, decrypted=False) for one in raw.get("Parameters", [])]
    found.sort(key=lambda one: one.version, reverse=True)
    return found[:limit]


@wrap_aws_errors
def _history_call(ctx: Context, name: str) -> dict[str, Any]:
    # WithDecryption is deliberately absent: a history listing is a *browse*, and
    # the whole point of the SecureString rule is that browsing cannot decrypt.
    return _client(ctx).get_parameter_history(Name=name, MaxResults=50)


def _self_check() -> None:
    """The AWS shapes that would otherwise be found at runtime."""
    meta = _from_metadata({"Name": "/db/pw", "Type": SECURE, "Version": 3})
    # DescribeParameters returns no value at all - a listing cannot leak one.
    assert meta.name == "/db/pw" and meta.version == 3
    assert not meta.has_value and not meta.decrypted

    # A value only counts as decrypted when the call asked for it.
    told = _from_value({"Name": "/a", "Type": SECURE, "Value": "hunter2"}, decrypted=True)
    assert told.decrypted and told.display_value() == "hunter2"
    hidden = _from_value({"Name": "/a", "Type": SECURE, "Value": "AQICAHgc"}, decrypted=False)
    assert not hidden.decrypted and "AQICAHgc" not in hidden.display_value()

    # A parameter AWS reported with nothing in it must not crash.
    assert _from_metadata({}).name == "" and _from_value({}, False).version == 0
    assert _moment("2026-08-01") is None and _moment(None) is None

    assert PAGE <= 50, "DescribeParameters caps MaxResults at 50"
    # The write half must be reachable from here.
    assert {"put_parameter", "delete_parameter"} <= set(__all__)
    print("[OK] ssm parameters self-check passed")


if __name__ == "__main__":
    _self_check()
