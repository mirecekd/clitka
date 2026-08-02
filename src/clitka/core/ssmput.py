"""Writing a parameter, and deleting one. The write half of Parameter Store.

Split from `core/ssmparam.py` for the 8 kB rule, and it landed on the read/write
seam exactly as `ecr.py` / `ecrops.py` did. `ssmparam` re-exports both names, so
callers only ever import `core.ssm`.

Two things about `PutParameter` are checked here rather than left to AWS:

- **it cannot change the type of an existing parameter.** Turning a `String` into
  a `SecureString` is a delete and a create, and the API's own complaint does not
  say so.
- **without `Overwrite=True` it answers `ParameterAlreadyExists`**, which reads
  like a bug rather than like "you meant to update, say so".

Both need the parameter's current state, so both cost a `GetParameter` first -
the `ec2.power()` rule: read, decide, then act.
"""

from __future__ import annotations

from typing import Any

from clitka.core.context import Context
from clitka.core.errors import wrap_aws_errors
from clitka.core.ssmmodel import SECURE, TYPES, Parameter, param_name_of

__all__ = ["delete_parameter", "put_parameter"]


def put_parameter(
    ctx: Context,
    name: str,
    value: str,
    type_name: str = "String",
    overwrite: bool = False,
    description: str = "",
    key_id: str = "",
) -> str:
    """Create or update a parameter. Returns a sentence about what happened."""
    if type_name not in TYPES:
        raise ValueError(f"unknown parameter type {type_name!r} - one of {', '.join(TYPES)}")
    if key_id and type_name != SECURE:
        raise ValueError(f"a KMS key only means something for a {SECURE}")
    existing = existing_parameter(ctx, name)
    if existing is not None:
        if not overwrite:
            raise ValueError(f"{name} already exists (version {existing.version}) - pass overwrite")
        if existing.type != type_name:
            raise ValueError(
                f"{name} is a {existing.type} and AWS cannot change that - "
                "delete it first if you really mean to"
            )
    ctx.require_write(f"write {name}")
    kwargs: dict[str, Any] = {
        "Name": name,
        "Value": value,
        "Type": type_name,
        "Overwrite": overwrite,
    }
    if description:
        kwargs["Description"] = description
    if key_id:
        kwargs["KeyId"] = key_id
    answer = _put_call(ctx, kwargs)
    verb = "updated" if existing is not None else "created"
    return f"{name}: {verb}, now version {answer.get('Version', '?')}"


def existing_parameter(ctx: Context, name: str) -> Parameter | None:
    """The parameter as it is now, or None when there is none.

    **Never decrypts** - it is only read for its type and version, and the
    SecureString rule has no exceptions, not even internal ones.
    """
    from clitka.core.ssmparam import get_parameter

    try:
        return get_parameter(ctx, name, decrypt=False)
    except LookupError:
        return None
    except Exception as exc:  # ParameterNotFound arrives as an AwsError
        if "ParameterNotFound" in str(exc) or "ParameterVersionNotFound" in str(exc):
            return None
        raise


@wrap_aws_errors
def _put_call(ctx: Context, kwargs: dict[str, Any]) -> dict[str, Any]:
    return _client(ctx).put_parameter(**kwargs)


def delete_parameter(ctx: Context, identifier: str) -> str:
    """Delete a parameter. **Every version goes and there is no undo.**"""
    name = param_name_of(identifier)
    ctx.require_write(f"delete {name}")
    _delete_call(ctx, name)
    return f"{name}: deleted"


@wrap_aws_errors
def _delete_call(ctx: Context, name: str) -> dict[str, Any]:
    return _client(ctx).delete_parameter(Name=name)


def _client(ctx: Context) -> Any:
    return ctx.client("ssm")


def _self_check() -> None:
    # The validation that happens before any client is built, so it is testable
    # with a Context that could never connect to anything.
    ctx = Context(region="eu-central-1")
    for bad in ("Secret", "string", ""):
        try:
            put_parameter(ctx, "/a", "v", type_name=bad)
        except ValueError as exc:
            assert "unknown parameter type" in str(exc), exc
        else:  # pragma: no cover
            raise AssertionError(f"type {bad!r} was accepted")

    try:
        put_parameter(ctx, "/a", "v", type_name="String", key_id="alias/aws/ssm")
    except ValueError as exc:
        assert SECURE in str(exc), exc
    else:  # pragma: no cover
        raise AssertionError("a KMS key on a plain String was accepted")

    assert SECURE in TYPES
    print("[OK] ssm put self-check passed")


if __name__ == "__main__":
    _self_check()
