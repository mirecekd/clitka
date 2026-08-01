"""The invocation payload: bytes, limits and the two ways it can be wrong.

Split out of `lambdamodel.py` for the 8 kB rule, and it turned out to be the
right seam anyway: this half is what both the CLI (`lambda invoke --payload`)
and the TUI (the F9 invoke action) check *before* calling AWS, so the user gets a
sentence instead of a RequestEntityTooLargeException or a raw JSON parser error.
"""

from __future__ import annotations

import base64
import json

# Lambda's own ceilings, so we can say "too big" before AWS does.
MAX_SYNC_PAYLOAD = 6 * 1024 * 1024
MAX_ASYNC_PAYLOAD = 256 * 1024


def payload_bytes(payload: str | bytes | None) -> bytes:
    """Whatever the caller has, as the bytes boto3 wants. `None` means `{}`."""
    if payload is None or payload == "":
        return b"{}"
    if isinstance(payload, bytes):
        return payload
    return payload.encode("utf-8")


def too_big(payload: str | bytes | None, asynchronous: bool = False) -> str:
    """ "" when the payload fits, otherwise why it does not."""
    size = len(payload_bytes(payload))
    ceiling = MAX_ASYNC_PAYLOAD if asynchronous else MAX_SYNC_PAYLOAD
    if size <= ceiling:
        return ""
    kind = "asynchronous" if asynchronous else "synchronous"
    return f"payload is {size} bytes; the {kind} limit is {ceiling}"


def bad_json(payload: str | bytes | None) -> str:
    """ "" when the payload is JSON Lambda will accept, otherwise the complaint."""
    raw = payload_bytes(payload)
    try:
        json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        return f"payload is not valid UTF-8: {exc}"
    except ValueError as exc:
        return f"payload is not valid JSON: {exc}"
    return ""


def complaint(payload: str | bytes | None, asynchronous: bool = False) -> str:
    """Both checks in the order a user cares about - malformed beats too big."""
    return bad_json(payload) or too_big(payload, asynchronous)


def decode_log_tail(encoded: str) -> str:
    """`LogResult` is base64. Undecodable input loses the tail, never the call."""
    if not encoded:
        return ""
    try:
        return base64.b64decode(encoded, validate=True).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""


def _self_check() -> None:
    assert payload_bytes(None) == b"{}" and payload_bytes("") == b"{}"
    assert payload_bytes(b"x") == b"x" and payload_bytes("x") == b"x"

    assert too_big("{}") == ""
    big = "x" * (MAX_ASYNC_PAYLOAD + 1)
    assert "limit is" in too_big(big, asynchronous=True)
    assert too_big(big) == ""  # the same payload is fine synchronously

    assert bad_json('{"a":1}') == "" and bad_json(None) == ""
    assert "not valid JSON" in bad_json("{oops")
    assert "not valid UTF-8" in bad_json(b"\xff\xfe")

    # Malformed is the more useful complaint, so it wins.
    assert "not valid JSON" in complaint("{" + "x" * MAX_ASYNC_PAYLOAD, asynchronous=True)
    assert complaint('{"a":1}') == ""

    assert decode_log_tail(base64.b64encode(b"one\ntwo").decode()) == "one\ntwo"
    assert decode_log_tail("not base64 at all!!") == ""
    assert decode_log_tail("") == ""
    print("[OK] lambda payload self-check passed")


if __name__ == "__main__":
    _self_check()
