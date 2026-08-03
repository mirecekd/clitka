"""What F3 shows on an S3 object: **the file**.

The owner's report (2026-08-02): *"nejak to nevypada, ze bych se mohl podivat na
data v souboru na s3 - nejlepe pres F3, ktera je k tomu urcena"*. Before this, F3
called Cloud Control `GetResource` on `AWS::S3::Object` - a type CLITKA invented -
so it failed and fell back to printing the listing row. `core/viewer.Viewer` is
the seam that fixes it, and this module is its first user.

Three decisions, all of them from measurements in `/tmp/clitka-s3-poc/`:

- **Text or binary is decided by decoding, not by `ContentType`.** A GraphQL schema
  in the owner's account is served as `binary/octet-stream` and is perfectly good
  UTF-8; a PNG's first 2 kB contains no NUL byte at all, so the usual heuristic
  calls it text. See `core/s3read`.
- **A binary object gets a hex dump, not mojibake.** The magic bytes are usually
  enough to recognise what it is, which is more than a screen of question marks.
- **A prefix is not a file.** F3 on a folder lists it instead of failing, because
  that is the only useful reading of "view this".
"""

from __future__ import annotations

from clitka.core import s3read
from clitka.core.actions import ResourceRef
from clitka.core.context import Context
from clitka.core.viewer import Viewer
from clitka.services.s3.actions import is_object, is_prefix, listing_block, location_of

HEADER = "[dim]{summary}[/dim]\n[dim]{rule}[/dim]\n"


def _framed(body: s3read.Body, text: str) -> str:
    """The object's own summary above whatever we could make of its bytes."""
    summary = f"{body.location.uri}   {body.summary()}"
    return HEADER.format(summary=summary, rule="-" * min(len(summary), 78)) + text


def view_object(ctx: Context, ref: ResourceRef) -> str:
    """F3 on an object: its bytes, as text where they are text.

    The body is markup-escaped, the `logs` plugin's lesson: a JSON payload is all
    brackets and Rich would eat half of it as markup and then complain about the
    rest.
    """
    from rich.markup import escape

    body = s3read.read_object(ctx, location_of(ref).identifier)
    text = body.text
    if text is None:
        return _framed(
            body,
            "[dim](not text - showing the first bytes)[/dim]\n\n"
            + escape(s3read.hexdump(body.raw)),
        )
    if not text:
        return _framed(body, "[dim](empty)[/dim]")
    shown = escape(text)
    if body.truncated:
        shown += f"\n\n[dim]... cut off at {s3read.MAX_PREVIEW // 1024} kB[/dim]"
    return _framed(body, shown)


def view_prefix(ctx: Context, ref: ResourceRef) -> str:
    """F3 on a folder: what is in it. Failing would be the only worse answer.

    Reuses the F9 `Contents` action's own text, so the two never drift apart.
    """
    where = location_of(ref)
    text, count = listing_block(ctx, where.identifier)
    return f"[dim]{where.uri}   {count} entries[/dim]\n\n{text}"


VIEWERS: tuple[Viewer, ...] = (
    Viewer(id="s3.object", view=view_object, applies_to=is_object, label="Object"),
    Viewer(id="s3.prefix", view=view_prefix, applies_to=is_prefix, label="Prefix"),
)


def _self_check() -> None:
    """Offline: the framing and the branch each kind of body takes."""
    from clitka.core.s3model import BUCKET, OBJECT, PREFIX, Location

    where = Location("b", "logs/a.txt")

    plain = s3read.Body(where, b"hello\nworld\n", total=12, content_type="text/plain")
    framed = _framed(plain, "hello")
    assert "s3://b/logs/a.txt" in framed and "text/plain" in framed

    # A viewer claims an object and a prefix - and NOT a bucket, which Cloud
    # Control can genuinely `GetResource`. That fallthrough is the whole contract.
    obj = ResourceRef.from_row(OBJECT, {"identifier": "b/logs/a.txt"})
    folder = ResourceRef.from_row(PREFIX, {"identifier": "b/logs/"})
    bucket = ResourceRef.from_row(BUCKET, {"identifier": "b"})
    claimed = {one.id: one for one in VIEWERS}
    assert claimed["s3.object"].applies_to(obj)
    assert not claimed["s3.object"].applies_to(folder)
    assert claimed["s3.prefix"].applies_to(folder)
    assert not any(one.applies_to(bucket) for one in VIEWERS), (
        "a bucket is a real Cloud Control type - do not intercept it"
    )

    # Markup in the file must not be read AS markup - the logs plugin's lesson.
    # Measured rather than assumed: `escape` only touches what looks like a tag,
    # so `[1, 2]` in JSON is left alone and `[dim]` in the file is neutered. My
    # first assert here claimed the opposite and this self-check caught it.
    from rich.markup import escape

    assert escape("[dim]not a tag[/dim]") == "\\[dim]not a tag\\[/dim]"
    assert escape('{"n": 1}') == '{"n": 1}'

    # And `view_object` itself, with the one API call stubbed out - the three
    # branches a real bucket produces (text, binary, empty).
    real_read = s3read.read_object
    served: dict[str, bytes] = {"raw": b""}

    def fake_read(_ctx: object, _identifier: str, limit: int = 0) -> s3read.Body:
        raw = served["raw"]
        return s3read.Body(where, raw, total=len(raw))

    try:
        for raw, expected in (
            (b'{"level": "INFO"}', '{"level": "INFO"}'),
            (b"\x89PNG\r\n\x1a\n", "89 50 4e 47"),
            (b"", "(empty)"),
        ):
            served["raw"] = raw
            s3read.read_object = fake_read  # type: ignore[assignment]
            shown = view_object(None, obj)  # type: ignore[arg-type]
            assert expected in shown, (expected, shown[:120])
            assert "s3://b/logs/a.txt" in shown, "every view names what it is showing"
    finally:
        s3read.read_object = real_read  # type: ignore[assignment]

    print("[OK] s3 viewers self-check passed")


if __name__ == "__main__":
    _self_check()
