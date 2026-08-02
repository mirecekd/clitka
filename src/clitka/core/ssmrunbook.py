"""SSM documents (runbooks) as things CLITKA can render. Boto3-free.

The same seam as `ssmmodel.py` beside it, and split from it because a document
and a parameter share nothing but the service name. What may be *done* with a
document - the four refusals and the command result - is in
`core/ssmcommand.py`, which is the 8 kB rule finding the read/write seam again
(as it did for `ecr.py` / `ecrops.py`).

The one thing about a document that is derived rather than reported:
**a parameter is required exactly when AWS gave it no `DefaultValue`.** The API
never says "required" - it says nothing at all, and leaving such a parameter out
is an `InvalidParameters` error that does not name it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from clitka.core.logsmodel import stamp

__all__ = [
    "COMMAND",
    "Document",
    "DocumentParameter",
    "stamp",
]

COMMAND = "Command"


@dataclass(frozen=True)
class DocumentParameter:
    """One input a document declares."""

    name: str
    type: str = "String"
    description: str = ""
    default: str | None = None

    @property
    def required(self) -> bool:
        """True when AWS gave no default - the only way it says "required"."""
        return self.default is None

    def line(self) -> str:
        mark = "required" if self.required else f"default {self.default!r}"
        return f"{self.name} ({self.type}, {mark}) {self.description}".rstrip()


@dataclass(frozen=True)
class Document:
    """One SSM document, as the listing, the viewer and `run` see it."""

    name: str
    document_type: str = ""
    document_format: str = ""
    owner: str = ""
    version: str = ""
    status: str = ""
    platform_types: tuple[str, ...] = ()
    target_type: str = ""
    created: dt.datetime | None = None
    description: str = ""
    content: str = ""
    parameters: tuple[DocumentParameter, ...] = ()

    @property
    def aws_owned(self) -> bool:
        """True for the hundreds of documents AWS ships in every account.

        Worth asking, because they swamp the handful anyone actually wrote. The
        owner field is an account id for your own and `Amazon` for these, but the
        name prefix is the check that also works on a bare listing row.
        """
        return self.name.startswith(("AWS-", "AWSSupport-", "AWSPremiumSupport-"))

    @property
    def runnable(self) -> bool:
        """Only a Command document can be sent to an instance by `run`.

        An `Automation` runbook is `start-automation-execution` - a different API
        with a different result shape, deliberately out of scope here.
        """
        return self.document_type == COMMAND

    @property
    def platforms(self) -> str:
        return ", ".join(self.platform_types)

    @property
    def required(self) -> tuple[str, ...]:
        return tuple(one.name for one in self.parameters if one.required)

    def supports(self, platform: str) -> bool:
        """True when this document can run on `platform` (or says nothing about it)."""
        if not platform or not self.platform_types:
            return True
        wanted = platform.casefold()
        return any(one.casefold() == wanted for one in self.platform_types)

    def row(self) -> dict[str, Any]:
        return {
            "identifier": self.name,
            "type": self.document_type,
            "owner": self.owner,
            "version": self.version,
            "platforms": self.platforms,
            "format": self.document_format,
        }


def _self_check() -> None:
    shell = Document(
        "AWS-RunShellScript",
        document_type=COMMAND,
        platform_types=("Linux", "MacOS"),
        parameters=(
            DocumentParameter("commands", "StringList", "the commands to run"),
            DocumentParameter("workingDirectory", "String", "cwd", default=""),
        ),
    )
    assert shell.aws_owned and shell.runnable
    assert shell.required == ("commands",), shell.required
    assert shell.platforms == "Linux, MacOS"
    # A parameter is optional exactly when AWS gave it a default - including "".
    assert not DocumentParameter("d", default="").required
    assert DocumentParameter("d").required
    assert "required" in DocumentParameter("commands").line()
    assert "default ''" in DocumentParameter("d", default="").line()

    # Only a Command document may be sent to an instance.
    auto = Document("my-runbook", document_type="Automation")
    assert not auto.runnable and not auto.aws_owned
    assert not Document("x").runnable, "an unknown type is not runnable"

    assert shell.supports("Linux") and shell.supports("linux")
    assert not shell.supports("Windows")
    # A document that lists no platform is assumed to run anywhere.
    assert Document("any", document_type=COMMAND).supports("Windows")
    # And an empty question is not a refusal.
    assert shell.supports("")

    assert shell.row()["identifier"] == "AWS-RunShellScript"
    assert auto.row()["type"] == "Automation"
    print("[OK] ssm runbook self-check passed")


if __name__ == "__main__":
    _self_check()
