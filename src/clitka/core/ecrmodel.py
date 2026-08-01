"""What ECR hands back, as things CLITKA can render.

No boto3 call in here - the same seam as `logsmodel.py` and `lambdamodel.py`, so
the URI arithmetic, the tag handling and the "which reference deletes this image"
question are testable without a network. `core/ecr.py` is the API side.

`human_size` and `stamp` are reused from `logsmodel` rather than written again -
they are boto3-free already and bytes are bytes.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from clitka.core.logsmodel import human_size, stamp

__all__ = [
    "Image",
    "Repository",
    "human_size",
    "moment",
    "repo_name_of",
    "short_digest",
    "stamp",
]


@dataclass(frozen=True)
class Repository:
    """One ECR repository, as the browser, the CLI and the preview show it."""

    name: str
    arn: str = ""
    uri: str = ""
    registry_id: str = ""
    created: dt.datetime | None = None
    tag_mutability: str = ""
    scan_on_push: bool = False
    encryption: str = ""

    @property
    def region(self) -> str:
        """The region out of the ARN, or "" when there is no usable ARN."""
        parts = self.arn.split(":")
        return parts[3] if len(parts) > 3 else ""

    @property
    def registry(self) -> str:
        """The registry host - what `docker login` is aimed at.

        Derived from the repository URI (`<acct>.dkr.ecr.<region>.amazonaws.com/x`)
        because that is the one field ECR always fills in.
        """
        return self.uri.split("/", 1)[0] if self.uri else ""

    def row(self) -> dict[str, Any]:
        """The explorer table row. `identifier` is the column every screen keys on."""
        return {
            "identifier": self.name,
            "tags": self.tag_mutability.lower() or "",
            "scan_on_push": "yes" if self.scan_on_push else "no",
            "created": stamp(self.created),
        }


@dataclass(frozen=True)
class Image:
    """One image in a repository - a digest, whatever tags point at it, and size.

    An image with no tags is normal (it was overwritten, or pushed as part of a
    multi-arch manifest list) and is exactly what a cleanup wants to find.
    """

    digest: str
    repository: str = ""
    tags: tuple[str, ...] = ()
    size: int = 0
    pushed: dt.datetime | None = None
    manifest_type: str = ""
    scan_status: str = ""
    findings: dict[str, int] | None = None

    @property
    def untagged(self) -> bool:
        return not self.tags

    @property
    def label(self) -> str:
        """What a human calls this image: its tags, or "(untagged)" plus the digest."""
        if self.tags:
            return ", ".join(self.tags)
        return f"(untagged) {short_digest(self.digest)}"

    @property
    def reference(self) -> str:
        """The one string that identifies this image for a delete.

        The **digest**, always. Deleting by tag looks friendlier but removes the
        image every other tag also points at, which is how people lose `latest`
        and `v3` in one keystroke.
        """
        return self.digest

    @property
    def worst(self) -> str:
        """The most serious finding severity, or "" when nothing was reported."""
        for level in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL"):
            if (self.findings or {}).get(level):
                return level
        return ""

    def row(self) -> dict[str, Any]:
        return {
            "identifier": self.label,
            "size": human_size(self.size),
            "pushed": stamp(self.pushed),
            "scan": self.worst or self.scan_status.lower(),
            "digest": short_digest(self.digest),
        }


def short_digest(digest: str, keep: int = 12) -> str:
    """`sha256:abcdef...` as `abcdef123456` - long enough to be unambiguous."""
    body = digest.split(":", 1)[1] if ":" in digest else digest
    return body[:keep]


def repo_name_of(identifier: str) -> str:
    """A name, an ARN or a repository URI reduced to the plain repository name.

    Cloud Control identifies a repository by its name, but a URI or an ARN can
    arrive from a CLI argument or a hand-typed palette entry.
    """
    if identifier.startswith("arn:"):
        marker = ":repository/"
        if marker in identifier:
            return identifier.split(marker, 1)[1]
        return identifier
    if ".amazonaws.com/" in identifier:
        return identifier.split(".amazonaws.com/", 1)[1]
    return identifier


def moment(when: Any) -> dt.datetime | None:
    """ECR timestamps arrive as datetimes already; anything else is discarded."""
    return when if isinstance(when, dt.datetime) else None


def _self_check() -> None:
    repo = Repository(
        name="my-app",
        arn="arn:aws:ecr:eu-central-1:111122223333:repository/my-app",
        uri="111122223333.dkr.ecr.eu-central-1.amazonaws.com/my-app",
        tag_mutability="MUTABLE",
        scan_on_push=True,
    )
    assert repo.region == "eu-central-1", repo.region
    assert repo.registry == "111122223333.dkr.ecr.eu-central-1.amazonaws.com"
    assert repo.row()["tags"] == "mutable" and repo.row()["scan_on_push"] == "yes"
    # A repository built from a listing that omitted the ARN/URI must not explode.
    assert Repository("x").region == "" and Repository("x").registry == ""

    digest = "sha256:0123456789abcdef0123456789abcdef"
    tagged = Image(digest, tags=("latest", "v3"), size=2048)
    assert tagged.label == "latest, v3" and not tagged.untagged
    assert tagged.row()["size"] == "2.0K"
    # A delete is always by digest - deleting by tag takes every other tag with it.
    assert tagged.reference == digest

    bare = Image(digest)
    assert bare.untagged and bare.label.startswith("(untagged) 0123456789ab")
    assert short_digest(digest) == "0123456789ab"
    assert short_digest("nodigestprefix", keep=4) == "nodi"

    scanned = Image(digest, findings={"LOW": 2, "CRITICAL": 1})
    assert scanned.worst == "CRITICAL", scanned.worst
    assert Image(digest, scan_status="COMPLETE").row()["scan"] == "complete"
    # A count of zero is not a finding.
    assert Image(digest, findings={"HIGH": 0}).worst == ""

    assert repo_name_of("my-app") == "my-app"
    assert repo_name_of("arn:aws:ecr:eu-central-1:1:repository/team/my-app") == "team/my-app"
    assert repo_name_of("1.dkr.ecr.eu-central-1.amazonaws.com/team/my-app") == "team/my-app"
    assert moment("2026-08-01") is None and moment(None) is None
    print("[OK] ecr model self-check passed")


if __name__ == "__main__":
    _self_check()
