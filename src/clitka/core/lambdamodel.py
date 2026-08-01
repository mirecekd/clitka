"""What Lambda hands back, as things CLITKA can render.

No boto3 call in here - the same seam as `logsmodel.py`, so the formatting, the
ARN arithmetic and the invocation verdict are testable without a network or a
stub. `core/lambdafn.py` is the API side, `core/lambdapayload.py` the payload
checks (split off for the 8 kB rule and re-exported here).
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Any

from clitka.core.lambdapayload import (
    MAX_ASYNC_PAYLOAD,
    MAX_SYNC_PAYLOAD,
    bad_json,
    complaint,
    decode_log_tail,
    payload_bytes,
    too_big,
)

__all__ = [
    "LOG_GROUP_PREFIX",
    "MAX_ASYNC_PAYLOAD",
    "MAX_SYNC_PAYLOAD",
    "Function",
    "Invocation",
    "bad_json",
    "complaint",
    "decode_log_tail",
    "function_name_of",
    "moment",
    "payload_bytes",
    "stamp",
    "too_big",
]

LOG_GROUP_PREFIX = "/aws/lambda/"


@dataclass(frozen=True)
class Function:
    """One Lambda function, as the browser, the CLI and the preview show it."""

    name: str
    arn: str = ""
    runtime: str = ""
    handler: str = ""
    memory: int = 0
    timeout: int = 0
    code_size: int = 0
    description: str = ""
    role: str = ""
    version: str = ""
    package_type: str = "Zip"
    architectures: tuple[str, ...] = ()
    modified: dt.datetime | None = None
    env: dict[str, str] = field(default_factory=dict)
    layers: tuple[str, ...] = ()
    state: str = ""
    state_reason: str = ""

    @property
    def log_group(self) -> str:
        """Where this function writes - the `logs` plugin can take it from here."""
        return f"{LOG_GROUP_PREFIX}{self.name}"

    @property
    def region(self) -> str:
        """The region out of the ARN, or "" when there is no usable ARN."""
        parts = self.arn.split(":")
        return parts[3] if len(parts) > 3 else ""

    @property
    def healthy(self) -> bool:
        """A function AWS is not currently complaining about.

        An empty state means `ListFunctions`, which does not report one - absence
        is not a problem.
        """
        return self.state in ("", "Active")

    def row(self) -> dict[str, Any]:
        """The explorer table row. `identifier` is the column every screen keys on."""
        return {
            "identifier": self.name,
            "runtime": self.runtime or self.package_type,
            "memory": f"{self.memory} MB" if self.memory else "",
            "timeout": f"{self.timeout}s" if self.timeout else "",
            "modified": stamp(self.modified),
        }


@dataclass(frozen=True)
class Invocation:
    """The result of invoking a function - the payload plus the verdict.

    `function_error` is Lambda's own field: the HTTP call succeeded (status 200)
    yet the handler raised. Getting that wrong is the classic Lambda mistake, so
    `ok` insists on both.
    """

    status: int = 0
    function_error: str = ""
    payload: str = ""
    log_tail: str = ""
    executed_version: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300 and not self.function_error

    @property
    def async_accepted(self) -> bool:
        """202 is what `InvocationType=Event` returns; there is no payload."""
        return self.status == 202

    def data(self) -> Any:
        """The payload parsed as JSON, or the raw string when it is not JSON."""
        if not self.payload:
            return None
        try:
            return json.loads(self.payload)
        except ValueError:
            return self.payload

    def log_lines(self) -> list[str]:
        return [line for line in self.log_tail.splitlines() if line.strip()]

    def summary(self) -> str:
        if self.async_accepted:
            return "accepted (202) - the result goes to the log group"
        if self.function_error:
            return f"[ERROR] {self.function_error} (status {self.status})"
        return f"[OK] status {self.status}"


def function_name_of(identifier: str) -> str:
    """A name, an ARN or an ARN with a qualifier, reduced to the plain name."""
    if not identifier.startswith("arn:"):
        return identifier.split(":", 1)[0]
    parts = identifier.split(":")
    # arn:aws:lambda:region:acct:function:name[:qualifier]
    return parts[6] if len(parts) > 6 else identifier


def stamp(when: dt.datetime | None) -> str:
    return "" if when is None else when.strftime("%Y-%m-%d %H:%M:%S")


def moment(iso: Any) -> dt.datetime | None:
    """Lambda speaks ISO 8601 with a `+0000` offset, unlike CloudWatch's millis."""
    if isinstance(iso, dt.datetime):
        return iso
    if not isinstance(iso, str) or not iso:
        return None
    try:
        return dt.datetime.fromisoformat(iso)
    except ValueError:
        pass
    # `2026-08-01T06:00:00.000+0000` - fromisoformat wants +00:00 before 3.11.
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return dt.datetime.strptime(iso, fmt)
        except ValueError:
            continue
    return None


def _self_check() -> None:
    fn = Function(
        name="my-fn",
        arn="arn:aws:lambda:eu-central-1:1:function:my-fn",
        runtime="python3.13",
        memory=512,
        timeout=30,
        modified=moment("2026-08-01T06:00:00.000+0000"),
    )
    assert fn.log_group == "/aws/lambda/my-fn"
    assert fn.region == "eu-central-1", fn.region
    assert fn.healthy and Function("x", state="Failed").healthy is False
    assert fn.row()["memory"] == "512 MB" and fn.row()["timeout"] == "30s"
    assert fn.row()["modified"].startswith("2026-08-01")
    # A function with no ARN must not explode on .region.
    assert Function("x").region == ""
    # A container image function has no runtime; the row shows the package type.
    assert Function("x", package_type="Image").row()["runtime"] == "Image"

    # The classic Lambda trap: HTTP 200 with a handler that raised.
    raised = Invocation(status=200, function_error="Unhandled", payload='{"errorType":"X"}')
    assert not raised.ok and "Unhandled" in raised.summary()
    assert raised.data()["errorType"] == "X"
    good = Invocation(status=200, payload='{"ok":true}')
    assert good.ok and good.data() == {"ok": True}
    assert Invocation(status=202).async_accepted
    assert "202" in Invocation(status=202).summary()
    # A non-JSON payload comes back as the string it is, not as an exception.
    assert Invocation(status=200, payload="not json").data() == "not json"
    assert Invocation().data() is None

    tail = Invocation(status=200, log_tail="a\n\nb\n")
    assert tail.log_lines() == ["a", "b"]

    # The payload half is checked in lambdapayload; here we only prove it is
    # reachable through this module, which is what every caller imports.
    assert payload_bytes(None) == b"{}"
    assert too_big("{}") == "" and bad_json('{"a":1}') == ""
    assert "not valid JSON" in complaint("{oops")
    assert decode_log_tail("") == ""

    assert function_name_of("my-fn") == "my-fn"
    assert function_name_of("my-fn:1") == "my-fn"
    assert function_name_of("arn:aws:lambda:eu-central-1:1:function:my-fn") == "my-fn"
    assert function_name_of("arn:aws:lambda:eu-central-1:1:function:my-fn:PROD") == "my-fn"
    assert moment("nonsense") is None and moment(None) is None
    print("[OK] lambda model self-check passed")


if __name__ == "__main__":
    _self_check()
