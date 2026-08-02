"""Sending a document to an instance, and finding out what it did.

The write half of `core/ssmdoc.py`, split off for the 8 kB rule - the same
read/write seam as `ecr.py` / `ecrops.py` and `ssmparam.py` / `ssmput.py`.
`ssmdoc` re-exports these names, so callers import one module.

Three things about `SendCommand` shaped this:

- **it returns a `CommandId` and nothing else.** The script has not run yet, so
  the result needs one `GetCommandInvocation` per instance - hence `wait_for`, and
  hence `run()` not pretending to be synchronous.
- **the invocation does not exist yet the moment the command is accepted.**
  Asking straight away answers `InvocationDoesNotExist`, which is *not* an error -
  it is "ask again in a moment". Found on the first live run against sw-sandbox,
  where a perfectly good command reported a failure. `wait_for` therefore treats
  it as a pending state; only `invocation()` on its own still raises, because
  there a missing invocation really is the answer.
- **the document is described before anything is sent**, so every knowable
  complaint is a sentence beforehand. Same rule as `ec2.power()` and
  `ecsrun.shell_for()`: after the call there is nothing useful left to say.
"""

from __future__ import annotations

import time
from typing import Any

from clitka.core.context import Context
from clitka.core.errors import ClitkaError, wrap_aws_errors
from clitka.core.ssmcommand import Invocation, refuses_run

__all__ = ["invocation", "run", "wait_for"]

# What AWS says when the command has been accepted but the per-instance record has
# not appeared yet. Seconds, in practice - but the first poll is inside that gap.
NOT_YET = "InvocationDoesNotExist"


def _client(ctx: Context) -> Any:
    return ctx.client("ssm")


def run(
    ctx: Context,
    name: str,
    instance_ids: list[str],
    parameters: dict[str, list[str]] | None = None,
    comment: str = "",
) -> str:
    """Send a Command document to instances. Returns the `CommandId`."""
    from clitka.core.ssmdoc import get_document

    doc = get_document(ctx, name)
    refusal = refuses_run(doc, instance_ids, parameters)
    if refusal:
        raise ValueError(refusal)
    ctx.require_write(f"run {name} on {', '.join(instance_ids)}")
    kwargs: dict[str, Any] = {"DocumentName": doc.name, "InstanceIds": instance_ids}
    if parameters:
        kwargs["Parameters"] = parameters
    if comment:
        kwargs["Comment"] = comment[:100]  # the API's own limit, and it rejects more
    answer = _send_call(ctx, kwargs)
    return str((answer.get("Command") or {}).get("CommandId", ""))


@wrap_aws_errors
def _send_call(ctx: Context, kwargs: dict[str, Any]) -> dict[str, Any]:
    return _client(ctx).send_command(**kwargs)


def invocation(ctx: Context, command_id: str, instance_id: str) -> Invocation:
    """What one instance has done with one command, right now."""
    raw = _invocation_call(ctx, command_id, instance_id)
    return Invocation(
        command_id=command_id,
        instance_id=instance_id,
        status=str(raw.get("Status", "")),
        status_details=str(raw.get("StatusDetails", "")),
        exit_code=exit_code_of(raw.get("ResponseCode")),
        stdout=str(raw.get("StandardOutputContent", "")),
        stderr=str(raw.get("StandardErrorContent", "")),
        document=str(raw.get("DocumentName", "")),
    )


def exit_code_of(code: Any) -> int | None:
    """`ResponseCode` as an exit code, or None when there is not one.

    **SSM reports -1 when the script never ran at all** - the command was never
    delivered, the instance was unreachable. That is not an exit status of -1 and
    must not be shown as one.
    """
    return int(code) if isinstance(code, int) and code >= 0 else None


@wrap_aws_errors
def _invocation_call(ctx: Context, command_id: str, instance_id: str) -> dict[str, Any]:
    return _client(ctx).get_command_invocation(CommandId=command_id, InstanceId=instance_id)


def wait_for(
    ctx: Context,
    command_id: str,
    instance_id: str,
    timeout: float = 60.0,
    sleep: Any = time.sleep,
) -> Invocation:
    """Poll until the invocation is finished, or until `timeout` runs out.

    `sleep` is an argument so a test never actually waits - the trick
    `core/sso.py` already uses for the device flow. A timeout returns the last
    state seen rather than raising: "still running" is an answer.

    `InvocationDoesNotExist` is treated as "not yet", not as a failure: the very
    first poll usually lands before AWS has created the per-instance record.
    """
    deadline = time.monotonic() + timeout
    last = _pending_aware(ctx, command_id, instance_id)
    while not last.done:
        if time.monotonic() >= deadline:
            return last
        sleep(1.0)
        last = _pending_aware(ctx, command_id, instance_id)
    return last


def _pending_aware(ctx: Context, command_id: str, instance_id: str) -> Invocation:
    """One poll, with "the record is not there yet" reported as `Pending`."""
    try:
        return invocation(ctx, command_id, instance_id)
    except ClitkaError as exc:
        if NOT_YET not in str(exc):
            raise
        return Invocation(command_id, instance_id, status="Pending", status_details=NOT_YET)


def _self_check() -> None:
    # -1 is "the script never ran", not an exit status.
    assert exit_code_of(0) == 0
    assert exit_code_of(3) == 3
    assert exit_code_of(-1) is None
    assert exit_code_of(None) is None
    assert exit_code_of("0") is None, "a string is not an exit code"
    print("[OK] ssm run self-check passed")


if __name__ == "__main__":
    _self_check()
