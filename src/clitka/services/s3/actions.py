"""What F9 offers on a bucket, a prefix or an object, and what the preview shows.

Both are published through pluggy hooks, so the tree and the F9 menu never import
this module - they only ever see `Action` and `PreviewTab` objects.

**The F9 keys had to be checked against what already applies.** On any type with an
identifier the baseline `resources.*` actions claim `y`, `j`, `i` and `d`, and
`ActionMenu.on_key` runs the *first* match - which is how `ec2.details` on `d` once
became "delete the instance". These use `o`, `u` and `z`.

Nothing here mutates. Downloading, uploading, deleting an object and presigning are
all still to come, and the deliberate decision so far is the `lambda.invoke` rule:
where one keystroke is too cheap for the consequence, F9 prints the command instead
of running it.
"""

from __future__ import annotations

from clitka.core import preview as pv
from clitka.core import s3
from clitka.core.actions import Action, ActionResult, ResourceRef
from clitka.core.context import Context
from clitka.core.s3model import BUCKET, OBJECT, PREFIX, human_size, stamp

__all__ = [
    "ACTIONS",
    "BUCKET",
    "OBJECT",
    "PREFIX",
    "PREVIEWS",
    "is_bucket",
    "is_object",
    "is_prefix",
    "listing_block",
    "location_of",
]


def is_bucket(ref: ResourceRef) -> bool:
    return ref.type_name == BUCKET


def is_prefix(ref: ResourceRef) -> bool:
    return ref.type_name == PREFIX


def is_object(ref: ResourceRef) -> bool:
    return ref.type_name == OBJECT


def is_ours(ref: ResourceRef) -> bool:
    """Any of the three - a bucket, a folder in one, or an object."""
    return is_bucket(ref) or is_prefix(ref) or is_object(ref)


def location_of(ref: ResourceRef) -> s3.Location:
    """Where the thing under the cursor lives.

    Every identifier this plugin produces is already `<bucket>/<key>`, so this is
    usually just a parse - but a bucket reached through the *generic* explorer
    carries a plain name, and a hand-typed palette entry may carry an `s3://` URI.
    """
    raw = ref.identifier or str(ref.row.get("Name", ""))
    return s3.Location.parse(raw)


def _lines(pairs: list[tuple[str, str]]) -> str:
    """Label/value pairs as an aligned block - the shape every tab here uses."""
    if not pairs:
        return "[dim](nothing to show)[/dim]"
    width = max(len(label) for label, _ in pairs)
    return "\n".join(f"[dim]{label:<{width}}[/dim]  {value}" for label, value in pairs)


def listing_block(ctx: Context, identifier: str) -> tuple[str, int]:
    """One level as text, and how many entries it held. Shared by F9 and the tab."""
    found = s3.browse(ctx, identifier)
    if not found.total:
        return "[dim](empty)[/dim]", 0
    rows: list[tuple[str, str]] = []
    for folder in found.folders:
        rows.append((folder.label, "[dim]folder[/dim]"))
    for obj in found.files:
        detail = human_size(obj.size)
        if obj.modified:
            detail += f"   {stamp(obj.modified)}"
        rows.append((obj.location.label, detail))
    text = _lines(rows)
    if found.capped:
        text += f"\n[dim]... cut off at {s3.MAX_CHILDREN} - narrow the prefix[/dim]"
    return text, found.total


def show_contents(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9 `o`: what is at this level, folders first.

    On a bucket this is the root; on a prefix it is that prefix. The same question,
    so it is the same action rather than two.
    """
    where = location_of(ref)
    text, count = listing_block(ctx, where.identifier)
    return ActionResult(f"{where.uri} - {count} entries", text)


def show_details(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9 `z`: what the row knows, plus the region for a bucket.

    The region costs one extra call (`get_bucket_location`) and is deliberately not
    in the listing - see `core/s3.py`. An object needs no call at all: the listing
    already carried its size and timestamp.
    """
    where = location_of(ref)
    pairs: list[tuple[str, str]] = [("uri", where.uri), ("bucket", where.bucket)]
    if is_bucket(ref):
        bucket = s3.get_bucket(ctx, where.bucket)
        pairs.append(("region", bucket.region))
        created = str(ref.row.get("created", ""))
        if created:
            pairs.append(("created", created))
    else:
        pairs.append(("key", where.key))
        for label in ("size", "modified", "storage"):
            value = str(ref.row.get(label, ""))
            if value:
                pairs.append((label, value))
    return ActionResult(f"{where.label} - details", _lines(pairs))


def show_uri(_ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9 `u`: the `s3://` URI and the CLI commands that act on it.

    Handing over commands rather than running them, the `ecr.login` /
    `lambda.invoke` rule: a download writes to the user's disk and a delete cannot
    be un-run, so neither belongs behind one keystroke while there is no confirm
    screen that names the file.
    """
    where = location_of(ref)
    if where.is_folder:
        body = "\n".join(
            (
                f"aws s3 ls {where.uri}",
                f"aws s3 sync {where.uri} ./{where.label.rstrip('/') or where.bucket}",
            )
        )
    else:
        body = "\n".join(
            (
                f"aws s3 cp {where.uri} ./{where.label}",
                f"aws s3 presign {where.uri} --expires-in 3600",
            )
        )
    return ActionResult(where.uri, f"{where.uri}\n\n[dim]useful commands:[/dim]\n{body}")


def contents_tab(ctx: Context, ref: ResourceRef) -> str:
    """The `Contents` preview tab - lazy, so opening a bucket costs nothing."""
    text, _ = listing_block(ctx, location_of(ref).identifier)
    return text


ACTIONS: tuple[Action, ...] = (
    Action(
        id="s3.contents",
        label="Contents",
        run=show_contents,
        key="o",
        applies_to=lambda ref: is_bucket(ref) or is_prefix(ref),
    ),
    Action(
        id="s3.details",
        label="Details",
        run=show_details,
        # `z`, not `d`: `resources.delete` owns `d` on every type with an
        # identifier and `ActionMenu.on_key` runs the first match - the
        # `ec2.details` lesson, which cost a real bug once.
        key="z",
        applies_to=is_ours,
    ),
    Action(
        id="s3.uri",
        label="URI and commands",
        run=show_uri,
        key="u",
        applies_to=is_ours,
    ),
)

PREVIEWS: tuple[pv.PreviewTab, ...] = (
    pv.PreviewTab(
        id="s3.contents",
        label="Contents",
        build=contents_tab,
        applies_to=lambda ref: is_bucket(ref) or is_prefix(ref),
        lazy=True,
    ),
)


def _self_check() -> None:
    bucket = ResourceRef.from_row(BUCKET, {"identifier": "my-bucket"})
    folder = ResourceRef.from_row(PREFIX, {"identifier": "my-bucket/logs/2026/"})
    obj = ResourceRef.from_row(OBJECT, {"identifier": "my-bucket/logs/a.txt", "size": "2.0K"})

    assert location_of(bucket).identifier == "my-bucket"
    assert location_of(folder).key == "logs/2026/"
    assert location_of(obj).label == "a.txt"
    # A palette entry may be a URI, and a bucket may arrive with no key at all.
    assert location_of(ResourceRef(OBJECT, "s3://b/x.txt", {})).bucket == "b"

    # THE KEY COLLISION CHECK. `resources.*` claims y/j/i/d on anything with an
    # identifier, and the first match wins - so none of ours may reuse those.
    taken = {"y", "j", "i", "d"}
    ours = {one.key for one in ACTIONS}
    assert not (ours & taken), f"collides with resources.*: {ours & taken}"
    assert len(ours) == len(ACTIONS), "two of our own actions share a key"

    # Contents applies to things that hold things, not to a file.
    holds = [one.id for one in ACTIONS if one.applies_to(folder)]
    assert "s3.contents" in holds
    assert "s3.contents" not in [one.id for one in ACTIONS if one.applies_to(obj)]
    # But details and the URI apply to all three.
    for ref in (bucket, folder, obj):
        applies = [one.id for one in ACTIONS if one.applies_to(ref)]
        assert "s3.details" in applies and "s3.uri" in applies

    # Nothing here is destructive, and nothing here writes.
    assert not any(one.destructive for one in ACTIONS)

    # The URI action offers a download for a file and a sync for a folder.
    assert "aws s3 cp" in show_uri(None, obj).body  # type: ignore[arg-type]
    assert "aws s3 sync" in show_uri(None, folder).body  # type: ignore[arg-type]

    # The preview tab must be lazy or opening a bucket leaf would list it at once.
    assert all(tab.lazy for tab in PREVIEWS)
    print("[OK] s3 actions self-check passed")


if __name__ == "__main__":
    _self_check()
