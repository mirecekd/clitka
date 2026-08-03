"""What hangs UNDERNEATH a bucket, and underneath a prefix, and so on down.

The ECS listers were the first user of `core/lister.ChildLister`; this is the
second, and it is the one the hook turns out to have been shaped for. **One lister
applies to both a bucket and its own prefix output**, which is the whole recursion:

    applies_to=lambda ref: is_bucket(ref) or is_prefix(ref)

A PoC measured it to depth three before any of this was written (2026-08-02,
`/tmp/clitka-s3-poc/`) precisely because "does the existing hook do arbitrary
depth?" is not a question to answer by reading. It does, with **no change to the
tree or to `core/lister.py`** - `has_child_listers` caches per *type name*, and
every prefix shares one, so every prefix gets a fold arrow and every object gets
none.

Two things the rows must carry, the ECS lesson:

- **`Bucket`**, because every S3 call is scoped by it and a row is all a later F9
  action receives.
- **`Name`**, because `resname.name_of` is what the leaf leads with - and a
  `CommonPrefix` arrives as the *whole* path, so a leaf that showed the identifier
  would print `logs/2026/08/` at every single depth.
"""

from __future__ import annotations

from typing import Any

from clitka.core import s3
from clitka.core.actions import ResourceRef
from clitka.core.cloudcontrol import Resource
from clitka.core.context import Context
from clitka.core.lister import ChildLister
from clitka.services.s3.actions import BUCKET, OBJECT, PREFIX, is_bucket, is_prefix


def prefix_resource(where: s3.Location) -> Resource:
    """One folder as a tree row.

    The identifier is `<bucket>/<key>/`, which is exactly what `s3.browse` takes,
    so opening this node needs no lookup - the node *is* the argument.
    """
    row: dict[str, Any] = {
        "Name": where.label,
        "Bucket": where.bucket,
        "Prefix": where.key,
        "uri": where.uri,
    }
    return Resource(type_name=PREFIX, identifier=where.identifier, properties=row)


def object_resource(obj: s3.S3Object) -> Resource:
    """One object as a tree row - a leaf, and nothing hangs under it."""
    row: dict[str, Any] = dict(obj.row())
    # The Resource owns both of these: `identifier` is a field and `name` is
    # derived from `Name`. Left in, the leaf would show them twice.
    row.pop("identifier", None)
    row.pop("name", None)
    row["uri"] = obj.location.uri
    return Resource(type_name=OBJECT, identifier=obj.location.identifier, properties=row)


def list_children(ctx: Context, ref: ResourceRef) -> list[Resource]:
    """One level: the folders first, then the files.

    The order here is only a courtesy - `ChildLoader._children_done` re-sorts with
    `treemodel.sort_key`, which is where folders-first actually lives (a trailing
    slash sorts first). Returning them in this order anyway keeps the CLI and the
    tree telling the same story.

    A capped listing is **not** silently truncated: `browse` says so, and the
    placeholder row below says so on screen. A browser that quietly shows 2000 of
    50 000 keys is lying about what is in the bucket.
    """
    found = s3.browse(ctx, ref.identifier)
    rows = [prefix_resource(one) for one in found.folders]
    rows += [object_resource(one) for one in found.files]
    if found.capped:
        rows.append(
            Resource(
                type_name=OBJECT,
                identifier=f"{found.location.identifier}#capped",
                properties={
                    "Name": f"... more than {s3.MAX_CHILDREN} entries - narrow it down",
                    "Bucket": found.location.bucket,
                },
            )
        )
    return rows


LISTERS: tuple[ChildLister, ...] = (
    ChildLister(
        id="s3.objects",
        label="Objects",
        child_type=PREFIX,
        list=list_children,
        # A bucket AND a prefix - one lister, arbitrary depth. This single line is
        # what the PoC was written to check.
        applies_to=lambda ref: is_bucket(ref) or is_prefix(ref),
    ),
)


def _self_check() -> None:
    folder = s3.Location("my-bucket", "logs/2026/")
    made = prefix_resource(folder)
    assert made.type_name == PREFIX
    # The identifier is what `browse` takes, so the node is its own argument.
    assert made.identifier == "my-bucket/logs/2026/"
    # The leaf leads with the LAST segment - the whole path would repeat at depth.
    assert made.properties["Name"] == "2026/"
    assert made.name() == "2026/"
    assert made.properties["Bucket"] == "my-bucket"

    obj = s3.S3Object(s3.Location("my-bucket", "logs/a.txt"), size=2048)
    leaf = object_resource(obj)
    assert leaf.type_name == OBJECT
    assert leaf.identifier == "my-bucket/logs/a.txt"
    assert leaf.properties["Name"] == "a.txt" and leaf.properties["size"] == "2.0K"
    assert "identifier" not in leaf.properties

    # The recursion, as a predicate: a bucket opens, a prefix opens, an object does
    # not. That last one is what stops a fold arrow appearing on a file.
    bucket_ref = ResourceRef.from_row(BUCKET, {"identifier": "my-bucket"})
    prefix_ref = ResourceRef.from_row(PREFIX, made.row())
    object_ref = ResourceRef.from_row(OBJECT, leaf.row())
    lister = LISTERS[0]
    assert lister.applies_to(bucket_ref)
    assert lister.applies_to(prefix_ref), "a prefix must open, or there is no depth"
    assert not lister.applies_to(object_ref), "an object has nothing under it"
    print("[OK] s3 listers self-check passed")


if __name__ == "__main__":
    _self_check()
