"""What may be *done* with an SSM document, and what came back. Boto3-free.

Split from `ssmrunbook.py` for the 8 kB rule, and it landed on the read/write
seam the way `ecr.py` / `ecrops.py` did.

This module exists for one rule, the `ecsrun.shell_for()` rule:
**everything knowable before the call is checked before the call.** `SendCommand`
runs a shell script on someone's production machine; once AWS has accepted it
there is nothing useful left to say. So `refuses_run()` answers in a sentence for
each of the four knowable failures, in this order:

1. the document is not a `Command` document - and no amount of extra parameters
   will fix that, which is why it is checked first,
2. no target was named - `InstanceIds` may not be empty,
3. the document does not support the platform asked for - a Linux-only document
   sent to a Windows instance fails *on the instance*, minutes later,
4. a required parameter was not supplied - an `InvalidParameters` error that does
   not name which one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from clitka.core.ssmrunbook import COMMAND, Document

__all__ = [
    "DONE_STATES",
    "Invocation",
    "missing_parameters",
    "refuses_run",
]

# A command invocation is finished in one of these states; anything else
# (`Pending`, `InProgress`, `Delayed`) means "ask again in a moment".
DONE_STATES: tuple[str, ...] = ("Success", "Cancelled", "TimedOut", "Failed")


def missing_parameters(doc: Document, given: dict[str, Any] | None) -> tuple[str, ...]:
    """The document's required parameters that `given` does not supply.

    Separate from `refuses_run` so the TUI and `--dry-run` can *ask* for them
    rather than only complain - the `apigwroute.fill_path()` shape.
    """
    supplied = set(given or {})
    return tuple(name for name in doc.required if name not in supplied)


def refuses_run(
    doc: Document,
    instance_ids: list[str] | None,
    parameters: dict[str, Any] | None = None,
    platform: str = "",
) -> str:
    """Why this document cannot be sent right now, or "" when it can.

    A sentence rather than a bool: "it is an Automation runbook" and "it still
    wants commands" are different problems with different fixes.
    """
    if not doc.runnable:
        # "is Automation document" reads like a typo, so the article is added -
        # and a type AWS did not report needs different wording again.
        kind = f"an {doc.document_type}" if doc.document_type else "an unknown type of"
        return (
            f"{doc.name} is {kind} document, not a {COMMAND} one - "
            f"only a {COMMAND} document can be sent to an instance"
        )

    if not instance_ids:
        return f"{doc.name} needs at least one instance to run on"
    if not doc.supports(platform):
        return f"{doc.name} runs on {doc.platforms or 'nothing listed'}, not on {platform}"
    absent = missing_parameters(doc, parameters)
    if absent:
        return f"{doc.name} still wants {', '.join(absent)}"
    return ""


@dataclass(frozen=True)
class Invocation:
    """What one instance did with one command."""

    command_id: str
    instance_id: str
    status: str = ""
    status_details: str = ""
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    document: str = ""

    @property
    def done(self) -> bool:
        return self.status in DONE_STATES

    @property
    def ok(self) -> bool:
        """Delivered *and* the script was happy - two separate claims.

        A script that returned 3 is reported as `Failed`, but a multi-step
        document can be `Success` with no exit code at all, so a missing code is
        not a failure. Same family as Lambda's `FunctionError`: the transport
        succeeding says nothing about the work.
        """
        return self.status == "Success" and self.exit_code in (0, None)

    def summary(self) -> str:
        code = "" if self.exit_code is None else f", exit {self.exit_code}"
        return f"{self.instance_id}: {self.status or 'unknown'}{code}"

    def row(self) -> dict[str, Any]:
        return {
            "identifier": self.instance_id,
            "status": self.status,
            "exit_code": "" if self.exit_code is None else str(self.exit_code),
            "command_id": self.command_id,
        }


def _self_check() -> None:
    from clitka.core.ssmrunbook import DocumentParameter

    shell = Document(
        "AWS-RunShellScript",
        document_type=COMMAND,
        platform_types=("Linux",),
        parameters=(
            DocumentParameter("commands", "StringList"),
            DocumentParameter("workingDirectory", "String", default=""),
        ),
    )
    # The happy path is silent.
    assert refuses_run(shell, ["i-1"], {"commands": ["uptime"]}) == ""
    # An optional parameter left out is not a refusal.
    assert refuses_run(shell, ["i-1"], {"commands": ["x"], "workingDirectory": "/"}) == ""

    # ...and each of the four refusals says which one it is.
    auto = Document("my-runbook", document_type="Automation")
    assert "not a Command one" in refuses_run(auto, ["i-1"])
    # The article, which the live round on sw-sandbox caught missing: "is
    # Automation document" reads like a typo.
    assert "is an Automation document" in refuses_run(auto, ["i-1"])

    assert "at least one instance" in refuses_run(shell, [], {"commands": ["x"]})
    assert "at least one instance" in refuses_run(shell, None, {"commands": ["x"]})
    assert "not on Windows" in refuses_run(shell, ["i-1"], {"commands": ["x"]}, "Windows")
    assert "still wants commands" in refuses_run(shell, ["i-1"], {})
    assert "still wants commands" in refuses_run(shell, ["i-1"], None)
    # The order matters: a non-Command document cannot be fixed by supplying
    # more parameters or a target, so that complaint has to win.
    assert "not a Command one" in refuses_run(auto, [], None)
    # A document whose type AWS did not report must still refuse readably.
    assert "unknown type" in refuses_run(Document("x"), ["i-1"])

    assert missing_parameters(shell, None) == ("commands",)
    # A supplied-but-empty value is supplied: only the *user* may decide that.
    assert missing_parameters(shell, {"commands": []}) == ()

    good = Invocation("c1", "i-1", status="Success", exit_code=0)
    assert good.done and good.ok and "exit 0" in good.summary()
    # A multi-step document can succeed without reporting an exit code at all.
    assert Invocation("c1", "i-1", status="Success").ok
    bad = Invocation("c1", "i-1", status="Failed", exit_code=3)
    assert bad.done and not bad.ok and "exit 3" in bad.summary()
    waiting = Invocation("c1", "i-1", status="InProgress")
    assert not waiting.done and not waiting.ok
    # A timed-out command is finished, but it is not a success.
    timed = Invocation("c1", "i-1", status="TimedOut")
    assert timed.done and not timed.ok
    assert Invocation("c1", "i-1").row()["identifier"] == "i-1"
    assert Invocation("c1", "i-1").row()["exit_code"] == ""
    print("[OK] ssm command self-check passed")


if __name__ == "__main__":
    _self_check()
