"""Lambda: list, describe, invoke.

Same shape as `core/logs.py` - generators plus `wrap_aws_errors`. The row and
verdict types live in `core/lambdamodel.py` (no boto3 there).

`lambdafn`, not `lambda`: the latter is a keyword, so `core/lambda.py` could
never be imported.

Two things about `Invoke`:

- **HTTP 200 does not mean success.** A handler that raised still answers 200
  with `FunctionError` set and the traceback as the payload, so `Invocation.ok`
  insists on both.
- The response `Payload` is a streaming body: `read()` it once and keep it.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from clitka.core.context import Context
from clitka.core.errors import wrap_aws_errors
from clitka.core.lambdamodel import (
    Function,
    Invocation,
    complaint,
    decode_log_tail,
    function_name_of,
    moment,
    payload_bytes,
)

PAGE = 50  # ListFunctions allows up to 10 000, but 50 paints sooner


def _client(ctx: Context) -> Any:
    return ctx.client("lambda")


def _function_from(raw: dict[str, Any], env: dict[str, str] | None = None) -> Function:
    """One `FunctionConfiguration` as a `Function`. Shared by list and get."""
    return Function(
        name=str(raw.get("FunctionName", "")),
        arn=str(raw.get("FunctionArn", "")),
        runtime=str(raw.get("Runtime", "")),
        handler=str(raw.get("Handler", "")),
        memory=int(raw.get("MemorySize", 0) or 0),
        timeout=int(raw.get("Timeout", 0) or 0),
        code_size=int(raw.get("CodeSize", 0) or 0),
        description=str(raw.get("Description", "")),
        role=str(raw.get("Role", "")),
        version=str(raw.get("Version", "")),
        package_type=str(raw.get("PackageType", "Zip")),
        architectures=tuple(str(one) for one in raw.get("Architectures", []) or ()),
        modified=moment(raw.get("LastModified")),
        env=env if env is not None else _env_of(raw),
        layers=tuple(str(one.get("Arn", "")) for one in raw.get("Layers", []) or ()),
        state=str(raw.get("State", "")),
        state_reason=str(raw.get("StateReason", "")),
    )


def _env_of(raw: dict[str, Any]) -> dict[str, str]:
    """The environment variables, if AWS returned any.

    `Environment` may carry an `Error` instead of `Variables` when the KMS key is
    unreachable - which is not our problem to solve, only to not crash on.
    """
    section = raw.get("Environment") or {}
    variables = section.get("Variables") or {}
    return {str(key): str(value) for key, value in variables.items()}


# --- listing ---------------------------------------------------------------


def iter_functions(ctx: Context, page_size: int = PAGE) -> Iterator[Function]:
    """Yield every function in the region, page by page."""
    client = _client(ctx)
    kwargs: dict[str, Any] = {"MaxItems": page_size}
    marker: str | None = None
    while True:
        if marker:
            kwargs["Marker"] = marker
        page = _functions_page(ctx, client, kwargs)
        for raw in page.get("Functions", []):
            yield _function_from(raw)
        marker = page.get("NextMarker")
        if not marker:
            return


@wrap_aws_errors
def _functions_page(ctx: Context, client: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    return client.list_functions(**kwargs)


def list_functions(ctx: Context, limit: int | None = None) -> list[Function]:
    """Eager variant for the CLI."""
    out: list[Function] = []
    for fn in iter_functions(ctx):
        out.append(fn)
        if limit is not None and len(out) >= limit:
            break
    return out


@wrap_aws_errors
def get_function(ctx: Context, name: str, qualifier: str = "") -> Function:
    """One function in full - `GetFunction`, which also carries the env vars."""
    kwargs: dict[str, Any] = {"FunctionName": name}
    if qualifier:
        kwargs["Qualifier"] = qualifier
    raw = _client(ctx).get_function(**kwargs)
    return _function_from(raw.get("Configuration", {}))


@wrap_aws_errors
def list_aliases(ctx: Context, name: str) -> list[dict[str, str]]:
    """The function's aliases as {name, version, description} - no pagination.

    ponytail: one page. Ceiling: a function with more than 50 aliases shows the
    first 50. Upgrade path: the same Marker loop as `iter_functions`.
    """
    page = _client(ctx).list_aliases(FunctionName=name, MaxItems=50)
    return [
        {
            "name": str(one.get("Name", "")),
            "version": str(one.get("FunctionVersion", "")),
            "description": str(one.get("Description", "")),
        }
        for one in page.get("Aliases", [])
    ]


# --- invoking --------------------------------------------------------------


def invoke(
    ctx: Context,
    name: str,
    payload: str | bytes | None = None,
    asynchronous: bool = False,
    qualifier: str = "",
    with_logs: bool = True,
) -> Invocation:
    """Invoke a function and report what came back.

    The payload is validated here, before the call, so a typo in the JSON costs
    nothing. Invoking is a write, so `--read-only` refuses it.
    """
    ctx.require_write(f"invoke {function_name_of(name)}")
    wrong = complaint(payload, asynchronous)
    if wrong:
        raise ValueError(wrong)
    kwargs: dict[str, Any] = {
        "FunctionName": name,
        "InvocationType": "Event" if asynchronous else "RequestResponse",
        "Payload": payload_bytes(payload),
    }
    if with_logs and not asynchronous:
        # Tail only exists for a synchronous call - there is no output otherwise.
        kwargs["LogType"] = "Tail"
    if qualifier:
        kwargs["Qualifier"] = qualifier
    return _read(_invoke_call(ctx, kwargs))


@wrap_aws_errors
def _invoke_call(ctx: Context, kwargs: dict[str, Any]) -> dict[str, Any]:
    return _client(ctx).invoke(**kwargs)


def _read(raw: dict[str, Any]) -> Invocation:
    """The `Invoke` response as an `Invocation`. The body is read exactly once."""
    body = raw.get("Payload")
    text = ""
    if body is not None:
        try:
            data = body.read()
        except Exception:
            data = b""
        text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
    return Invocation(
        status=int(raw.get("StatusCode", 0) or 0),
        function_error=str(raw.get("FunctionError", "")),
        payload=text,
        log_tail=decode_log_tail(str(raw.get("LogResult", ""))),
        executed_version=str(raw.get("ExecutedVersion", "")),
    )


def _self_check() -> None:
    """The three AWS shapes that would otherwise be found at runtime."""
    import io

    # A string Timeout must still become an int, and Layers is a list of dicts.
    listed = _function_from(
        {
            "FunctionName": "my-fn",
            "Timeout": "30",
            "Layers": [{"Arn": "arn:aws:lambda:eu-central-1:1:layer:l:1"}],
        }
    )
    assert listed.timeout == 30 and listed.layers[0].endswith(":1")

    # An Environment carrying an Error instead of Variables must not crash.
    assert _env_of({"Environment": {"Error": {"Message": "kms is unhappy"}}}) == {}
    assert _env_of({"Environment": {"Variables": {"A": 1}}}) == {"A": "1"}

    # HTTP 200 with FunctionError is a failure, not a success.
    result = _read(
        {
            "StatusCode": 200,
            "FunctionError": "Unhandled",
            "Payload": io.BytesIO(b'{"errorType":"ValueError"}'),
        }
    )
    assert not result.ok and result.data()["errorType"] == "ValueError"

    # A payload the caller cannot use must not reach AWS at all.
    try:
        invoke(Context(region="eu-central-1"), "my-fn", "{oops")
    except ValueError as exc:
        assert "not valid JSON" in str(exc), exc
    else:
        raise AssertionError("a malformed payload should have been refused")
    print("[OK] lambdafn self-check passed")


if __name__ == "__main__":
    _self_check()
