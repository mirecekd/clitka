"""S3: buckets, and one level of a bucket at a time.

Same shape as `core/ecr.py` and `core/logs.py` - generators plus
`wrap_aws_errors`, with the boto3-free key arithmetic in `core/s3model.py`.

**The whole design rests on one measurement** (`sw-sandbox`, 2026-08-02, boto3 -
the notes are in `/tmp/clitka-s3-poc/findings-api.log`):

- **Cross-region needs no special handling at all.** An `eu-central-1` client
  listed a bucket living in `eu-north-1` and got the right answer - botocore
  follows the redirect itself. So there is **no `get_bucket_location` before a
  listing** and no per-region client cache, which is the single largest thing this
  module does *not* have to do. (A presign is expected to differ, because a
  presigned URL is signed for one endpoint. Measure that before writing it.)
- **`list_buckets` returns only `Name`, `BucketArn` and `CreationDate`** - no
  region, no size, no object count - and it is global and cheap (98 buckets in
  0.13 s). Anything more is one call *per bucket*, so `iter_buckets` enriches
  nothing: 98 buckets would be 98 round trips for a screen nobody has scrolled.
  `get_bucket` is where the region comes from, because there one bucket is asked
  about.
- **`Contents` and `CommonPrefixes` are ABSENT, not empty**, when a level holds
  none of that kind. `raw["Contents"]` raises `KeyError` on a folders-only
  listing, which is an ordinary shape - hence `.get(..., [])` throughout.
- **A `CommonPrefix` is the full key path**, so it is already what the next call
  wants as `Prefix`. `s3model.leaf_of` is what keeps the screen readable.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from clitka.core.context import Context

# `ecrmodel.moment`, NOT `logsmodel.moment`: the latter parses CloudWatch's epoch
# milliseconds and silently answers None for a datetime, so every S3 timestamp
# came back blank. S3, like ECR, hands back real datetimes. Found live, not by
# reading - the listing printed `created=` with nothing after it.
from clitka.core.ecrmodel import moment
from clitka.core.errors import wrap_aws_errors
from clitka.core.s3model import (
    BUCKET,
    OBJECT,
    PREFIX,
    Bucket,
    Location,
    S3Object,
    leaf_of,
    parent_of,
    split_uri,
)

__all__ = [
    "BUCKET",
    "MAX_CHILDREN",
    "OBJECT",
    "PAGE",
    "PREFIX",
    "Bucket",
    "Listing",
    "Location",
    "S3Object",
    "browse",
    "get_bucket",
    "iter_buckets",
    "leaf_of",
    "list_buckets",
    "parent_of",
    "split_uri",
]

PAGE = 1000
"""`ListObjectsV2` hard-caps `MaxKeys` at 1000, whatever is asked for."""

MAX_CHILDREN = 2000
"""What one tree sub-branch will show, and it is a deliberate cap.

`ChildLoader._children_done` is not paged (a documented ceiling), so a prefix
holding 50 000 keys would build 50 000 Textual nodes in one call - the branch
picker froze at 1842 for exactly that reason. This is the same number as the
explorer's `MAX_ROWS`; `Listing.capped` is how the screen says it cut the list.
"""


def _client(ctx: Context) -> Any:
    return ctx.client("s3")


class Listing:
    """One level of a bucket: the folders, the files, and whether it was cut short.

    Not a dataclass because `folders` and `files` are built up while paging, and
    because "was there more?" is a question the caller has to be able to ask -
    silently truncating a listing is how a browser lies about what is in a bucket.
    """

    def __init__(self, location: Location) -> None:
        self.location = location
        self.folders: list[Location] = []
        self.files: list[S3Object] = []
        self.capped = False

    @property
    def total(self) -> int:
        return len(self.folders) + len(self.files)

    def __repr__(self) -> str:  # pragma: no cover - debugging convenience
        cut = ", capped" if self.capped else ""
        where = self.location.identifier
        return f"<Listing {where}: {len(self.folders)} folders, {len(self.files)} files{cut}>"


@wrap_aws_errors
def iter_buckets(ctx: Context) -> Iterator[Bucket]:
    """Every bucket in the account, name/ARN/created only - see the module notes.

    `ListBuckets` is not paged in practice for a normal account, but it does take
    a continuation token now, so this follows it rather than assuming one page.
    """
    client = _client(ctx)
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {}
        if token:
            kwargs["ContinuationToken"] = token
        raw = client.list_buckets(**kwargs)
        for one in raw.get("Buckets", []) or []:
            yield Bucket(
                name=str(one.get("Name", "")),
                arn=str(one.get("BucketArn", "")),
                created=moment(one.get("CreationDate")),
            )
        token = raw.get("ContinuationToken") or None
        if not token:
            return


def list_buckets(ctx: Context) -> list[Bucket]:
    """Every bucket, sorted by name - what the CLI and the tree branch show."""
    return sorted(iter_buckets(ctx), key=lambda one: one.name.casefold())


@wrap_aws_errors
def get_bucket(ctx: Context, name: str) -> Bucket:
    """One bucket, **with** its region - the only place that costs a second call.

    `get_bucket_location` answers `None` for `us-east-1`, which is a genuine value
    and not an error: that region predates the constraint and is encoded as its
    absence. Saying "us-east-1" out loud is more use than an empty column.
    """
    client = _client(ctx)
    bucket, _ = split_uri(name)
    where = client.get_bucket_location(Bucket=bucket)
    region = str(where.get("LocationConstraint") or "us-east-1")
    return Bucket(name=bucket, region=region)


@wrap_aws_errors
def browse(ctx: Context, identifier: str, limit: int = MAX_CHILDREN) -> Listing:
    """One level of a bucket: `Delimiter="/"` turns a flat keyspace into folders.

    `identifier` is `<bucket>` or `<bucket>/<prefix>/` - the same string the tree
    carries as a `ResourceRef` identifier, so a lister can pass its own node
    straight in.

    Two shapes are deliberately dropped:

    - the **placeholder key** a console "create folder" leaves behind: a zero-byte
      key equal to the prefix itself. It would otherwise appear as a file *inside*
      the folder it represents, next to the folder.
    - nothing else. An empty level is an empty level and says so by being empty.
    """
    client = _client(ctx)
    location = Location.parse(identifier)
    found = Listing(location)
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "Bucket": location.bucket,
            "Delimiter": "/",
            "MaxKeys": PAGE,
        }
        if location.key:
            kwargs["Prefix"] = location.key
        if token:
            kwargs["ContinuationToken"] = token
        raw = client.list_objects_v2(**kwargs)

        # Absent, not empty, when a level holds only one kind - see module notes.
        for one in raw.get("CommonPrefixes", []) or []:
            found.folders.append(Location(location.bucket, str(one.get("Prefix", ""))))
        for one in raw.get("Contents", []) or []:
            key = str(one.get("Key", ""))
            if key == location.key:
                continue  # the console's own folder placeholder
            found.files.append(
                S3Object(
                    location=Location(location.bucket, key),
                    size=int(one.get("Size", 0) or 0),
                    modified=moment(one.get("LastModified")),
                    storage_class=str(one.get("StorageClass", "")),
                    etag=str(one.get("ETag", "")).strip('"'),
                )
            )

        if found.total >= limit:
            found.capped = True
            del found.folders[limit:]
            del found.files[max(0, limit - len(found.folders)) :]
            return found
        token = raw.get("NextContinuationToken") or None
        if not token or not raw.get("IsTruncated"):
            return found


def _self_check() -> None:
    """Offline: the parts that are arithmetic, not API. The rest is in the tests."""
    listing = Listing(Location("b", "logs/"))
    assert listing.total == 0 and not listing.capped
    listing.folders.append(Location("b", "logs/2026/"))
    listing.files.append(S3Object(Location("b", "logs/a.txt"), size=1))
    assert listing.total == 2
    assert "logs/" in repr(listing)

    # The cap is the branch-picker lesson: what does this do with 50 000 rows?
    assert MAX_CHILDREN > 0 and PAGE == 1000
    from clitka.tui.restypes import MAX_ROWS

    assert MAX_CHILDREN == MAX_ROWS, "keep the tree's two caps in step"

    # THE RIGHT `moment`. `logsmodel.moment` wants CloudWatch's epoch millis and
    # answers None for a datetime, which silently blanked every S3 timestamp until
    # a live listing showed `created=` with nothing after it.
    import datetime as dt

    real = dt.datetime(2026, 4, 29, 22, 20, 46, tzinfo=dt.UTC)
    assert moment(real) is real, "S3 hands back datetimes - do not parse them as millis"
    assert moment(1754130000000) is None, "and an epoch int is not an S3 timestamp"
    print("[OK] s3 self-check passed")


if __name__ == "__main__":
    _self_check()
