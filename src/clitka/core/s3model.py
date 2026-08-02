"""What S3 hands back, as things CLITKA can render.

No boto3 call in here - the same seam as `ecrmodel.py` and `logsmodel.py`, so the
key arithmetic (which is the whole difficulty of an S3 browser) is testable without
a network. `core/s3.py` is the API side.

**A bucket is not a flat listing, and that is the only interesting thing about S3.**
`ListObjectsV2` with `Delimiter="/"` answers in two halves: `CommonPrefixes`, which
are the folders, and `Contents`, which are the files. Neither is a real AWS resource
type - `AWS::S3::Prefix` and `AWS::S3::Object` are **CLITKA's own type strings**, the
`ecs.TASK` trick, and a test asserts they are in neither `COMMON_TYPES` nor
`TREE_TYPES` because nothing there could list them.

Measured on `sw-sandbox` (see the notes in `core/s3.py`): a `CommonPrefix` comes
back as the **whole key path**, not the last segment, which is why `leaf_of` exists
- a tree that showed `logs/2026/08/` at every depth would repeat its own ancestry
on every line.

`human_size` and `stamp` are reused from `logsmodel` rather than written again -
the ECR precedent, and bytes are bytes.
"""

from __future__ import annotations

import datetime as dt
import posixpath
from dataclasses import dataclass
from typing import Any

from clitka.core.logsmodel import human_size, stamp

__all__ = [
    "BUCKET",
    "OBJECT",
    "PREFIX",
    "Bucket",
    "Location",
    "S3Object",
    "human_size",
    "leaf_of",
    "parent_of",
    "split_uri",
    "stamp",
]

BUCKET = "AWS::S3::Bucket"
"""A real Cloud Control type - a bucket is an ordinary tree branch."""

PREFIX = "AWS::S3::Prefix"
"""CLITKA's own type string, like `ecs.TASK`. Cloud Control has no such thing."""

OBJECT = "AWS::S3::Object"
"""CLITKA's own type string. An object is a leaf in every sense."""


def leaf_of(key: str) -> str:
    """The last segment of an S3 key, with its trailing slash kept if it had one.

    `logs/2026/08/` -> `08/`, `logs/a.txt` -> `a.txt`. The slash is kept because it
    is what says "this is a folder" on screen and in `sort_key`, which puts a
    trailing slash first.
    """
    if not key:
        return ""
    trailing = "/" if key.endswith("/") else ""
    return posixpath.basename(key.rstrip("/")) + trailing


def parent_of(key: str) -> str:
    """The prefix one level up - `logs/2026/08/` -> `logs/2026/`, and `a.txt` -> ``.

    An empty string means the bucket root, which is exactly what `ListObjectsV2`
    wants as `Prefix` for a top-level listing.
    """
    body = key.rstrip("/")
    if "/" not in body:
        return ""
    return body.rsplit("/", 1)[0] + "/"


def split_uri(identifier: str) -> tuple[str, str]:
    """`bucket/some/key` -> `("bucket", "some/key")`. Accepts an `s3://` URI too.

    CLITKA identifies a prefix or an object by `<bucket>/<key>` because a
    `ResourceRef` carries exactly one identifier string and the lister needs both
    halves. A bare bucket name yields an empty key - the root.
    """
    text = identifier[5:] if identifier.startswith("s3://") else identifier
    text = text.lstrip("/")
    bucket, _, key = text.partition("/")
    return bucket, key


@dataclass(frozen=True)
class Location:
    """Where a prefix or an object lives: a bucket plus a key.

    Deliberately a value object rather than two loose strings - `split_uri` and
    `identifier` were being re-derived at every call site.
    """

    bucket: str
    key: str = ""

    @property
    def identifier(self) -> str:
        """What the tree, F9 and the CLI pass around. `bucket/key`, no scheme."""
        return f"{self.bucket}/{self.key}" if self.key else self.bucket

    @property
    def uri(self) -> str:
        return f"s3://{self.bucket}/{self.key}" if self.key else f"s3://{self.bucket}"

    @property
    def is_folder(self) -> bool:
        """A prefix - the root of a bucket counts, because it holds things."""
        return not self.key or self.key.endswith("/")

    @property
    def label(self) -> str:
        """The last segment, or the bucket name at the root."""
        return leaf_of(self.key) or self.bucket

    def parent(self) -> Location:
        """One level up; the bucket root is its own parent."""
        return Location(self.bucket, parent_of(self.key))

    @classmethod
    def parse(cls, identifier: str) -> Location:
        return cls(*split_uri(identifier))


@dataclass(frozen=True)
class Bucket:
    """One bucket, as the tree, the CLI and the preview show it.

    `list_buckets` is global and cheap (98 buckets in 0.13 s on `sw-sandbox`) but
    returns **only** name, ARN and creation date - no region, no size, no object
    count. Everything else is one more call per bucket, so a listing must not
    enrich rows: 98 buckets would be 98 round trips for a screen nobody scrolled.
    The region and the rest belong in the preview pane, where one bucket is asked
    about.
    """

    name: str
    arn: str = ""
    created: dt.datetime | None = None
    region: str = ""
    """Only filled in by a `get`, never by a listing - see the class docstring."""

    @property
    def location(self) -> Location:
        return Location(self.name)

    def row(self) -> dict[str, Any]:
        """The explorer table row. `identifier` is the column every screen keys on."""
        return {
            "identifier": self.name,
            "created": stamp(self.created),
            "region": self.region,
        }


@dataclass(frozen=True)
class S3Object:
    """One object - a key, its size and when it was last written.

    A "folder" is not one of these: it is a `CommonPrefix`, which has no size, no
    timestamp and no existence of its own. Hence `is_folder` on `Location` and two
    separate type strings.
    """

    location: Location
    size: int = 0
    modified: dt.datetime | None = None
    storage_class: str = ""
    etag: str = ""

    @property
    def key(self) -> str:
        return self.location.key

    @property
    def is_placeholder(self) -> bool:
        """A zero-byte key ending in `/` - what the console creates for a "folder".

        Worth naming: it shows up as an object *and* as a prefix, so a listing that
        did not know about it would show the same folder twice.
        """
        return self.key.endswith("/") and self.size == 0

    def row(self) -> dict[str, Any]:
        return {
            "identifier": self.location.identifier,
            "Name": self.location.label,
            "Bucket": self.location.bucket,
            "size": human_size(self.size),
            "modified": stamp(self.modified),
            "storage": self.storage_class.lower().replace("_", " "),
        }


def _self_check() -> None:
    # The type strings are ours, not AWS's - nothing generic can list them.
    from clitka.tui.restypes import COMMON_TYPES, TREE_TYPES

    assert BUCKET in COMMON_TYPES and BUCKET in TREE_TYPES
    for made_up in (PREFIX, OBJECT):
        assert made_up not in COMMON_TYPES, made_up
        assert made_up not in TREE_TYPES, made_up

    # A CommonPrefix arrives whole, so the label has to be the last segment only.
    assert leaf_of("logs/2026/08/") == "08/"
    assert leaf_of("logs/a.txt") == "a.txt"
    assert leaf_of("top/") == "top/"
    assert leaf_of("bare") == "bare"
    assert leaf_of("") == ""

    assert parent_of("logs/2026/08/") == "logs/2026/"
    assert parent_of("logs/a.txt") == "logs/"
    assert parent_of("top/") == "", parent_of("top/")
    assert parent_of("a.txt") == ""

    assert split_uri("my-bucket") == ("my-bucket", "")
    assert split_uri("my-bucket/logs/a.txt") == ("my-bucket", "logs/a.txt")
    assert split_uri("s3://my-bucket/logs/") == ("my-bucket", "logs/")
    assert split_uri("s3://my-bucket") == ("my-bucket", "")

    root = Location("my-bucket")
    assert root.identifier == "my-bucket" and root.uri == "s3://my-bucket"
    assert root.is_folder and root.label == "my-bucket"
    folder = Location("my-bucket", "logs/2026/")
    assert folder.identifier == "my-bucket/logs/2026/"
    assert folder.uri == "s3://my-bucket/logs/2026/"
    assert folder.is_folder and folder.label == "2026/"
    assert folder.parent().key == "logs/"
    assert folder.parent().parent().key == ""
    # The root is its own parent - walking up must terminate.
    assert root.parent().key == ""
    assert Location.parse("b/logs/a.txt").key == "logs/a.txt"

    file = Location("my-bucket", "logs/a.txt")
    assert not file.is_folder and file.label == "a.txt"

    bucket = Bucket("my-bucket", arn="arn:aws:s3:::my-bucket")
    assert bucket.row()["identifier"] == "my-bucket"
    # A listing knows no region, and the row must say so rather than guess.
    assert bucket.row()["region"] == ""
    assert bucket.location.identifier == "my-bucket"

    obj = S3Object(file, size=2048, storage_class="STANDARD_IA")
    row = obj.row()
    assert row["size"] == "2.0K" and row["Name"] == "a.txt"
    # The row carries `Bucket` because that is what the tree hands to F9 later,
    # the ECS `Cluster` lesson.
    assert row["Bucket"] == "my-bucket"
    assert row["storage"] == "standard ia"
    assert not obj.is_placeholder

    # A console-made "folder" is a zero-byte key ending in a slash - it would
    # otherwise appear twice, once as a prefix and once as an object.
    assert S3Object(Location("b", "logs/"), size=0).is_placeholder
    assert not S3Object(Location("b", "logs/"), size=12).is_placeholder
    print("[OK] s3 model self-check passed")


if __name__ == "__main__":
    _self_check()
