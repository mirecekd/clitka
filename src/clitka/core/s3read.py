"""Reading what is actually IN an object - the owner's request (2026-08-02).

*"nejak to nevypada, ze bych se mohl podivat na data v souboru na s3 - nejlepe
pres F3, ktera je k tomu urcena"*. Quite right, and it is the generic-explorer
problem from the other side: **F3 goes through Cloud Control `GetResource`, and
`AWS::S3::Object` is CLITKA's own pseudo-type**, so there was nothing to fetch and
F3 fell back to showing the listing row - the size and the ETag, not the file.

Three things were measured against `sw-sandbox` before this was written, and two of
them went against what the documentation suggests (`/tmp/clitka-s3-poc/`):

1. **`Range` is safe past the end.** `bytes=0-999999` on a 27-byte object returns
   27 bytes and no error, so a preview can always ask for its cap without a
   `head_object` first. `ContentRange` reports the **real total**, which is how the
   screen can say "showing 2 kB of 5.7 kB" without lying.
2. **`ContentType` is a liar.** A GraphQL schema uploaded by Amplify arrives as
   `binary/octet-stream` and is perfectly good UTF-8. Whatever put it there never
   set the type, and S3 does not guess.
3. **A NUL-byte scan is a liar too.** The first 2 kB of a real PNG contains no NUL
   at all, so the usual heuristic calls it text.

So the only test that works is the one that matters anyway: **try to decode it**.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from clitka.core.context import Context
from clitka.core.errors import wrap_aws_errors
from clitka.core.s3model import Location, human_size

__all__ = ["MAX_PREVIEW", "Body", "hexdump", "read_object"]

MAX_PREVIEW = 256 * 1024
"""How much of an object a screen will read. 256 kB is ~4000 lines of text.

ponytail: a fixed cap, not paging. Ceiling: a big file is shown head-only, which
is what `less` does on a pipe too. Upgrade path: `Range` again with an offset the
screen keeps - the API already supports it, only the UI does not.
"""


@dataclass(frozen=True)
class Body:
    """What came back, and whether it can be shown as text at all."""

    location: Location
    raw: bytes
    total: int
    """The object's real size, from `ContentRange` - not just what was read."""
    content_type: str = ""

    @property
    def truncated(self) -> bool:
        return self.total > len(self.raw)

    @property
    def text(self) -> str | None:
        """The bytes as UTF-8, or None when they are not text.

        A truncated read can cut a multi-byte character in half, which would make
        a perfectly good text file look binary - so a broken sequence **in the last
        few bytes** is dropped and the decode is tried again. Any UTF-8 file over
        the cap has a 3-in-4 chance of ending mid-character.

        The "last few bytes" part is the whole point, and a live read is what
        taught it: the first version dropped everything from `exc.start` onwards,
        so a **512-byte slice of a PNG** decoded its first 8 bytes, threw the rest
        away and reported itself as text. A UTF-8 character is at most 4 bytes, so
        a failure further from the end than that is real binary, not a cut.
        """
        try:
            return self.raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            if not self.truncated or len(self.raw) - exc.start > 3:
                return None
            try:
                return self.raw[: exc.start].decode("utf-8")
            except UnicodeDecodeError:  # pragma: no cover - defensive
                return None

    @property
    def is_text(self) -> bool:
        return self.text is not None

    def summary(self) -> str:
        """One line naming what was read out of what."""
        shown = human_size(len(self.raw))
        whole = human_size(self.total)
        kind = self.content_type or "(no content-type)"
        if self.truncated:
            return f"{shown} of {whole}, {kind}"
        return f"{whole}, {kind}"


def hexdump(raw: bytes, width: int = 16, limit: int = 32) -> str:
    """A classic hex dump of the head - what to show when it is not text.

    A wall of mojibake tells the user nothing; a hex dump at least shows the magic
    bytes, which is usually enough to recognise a PNG or a gzip.
    """
    lines: list[str] = []
    for offset in range(0, min(len(raw), width * limit), width):
        chunk = raw[offset : offset + width]
        hexed = " ".join(f"{byte:02x}" for byte in chunk)
        shown = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in chunk)
        lines.append(f"{offset:08x}  {hexed:<{width * 3 - 1}}  {shown}")
    if len(raw) > width * limit:
        lines.append("...")
    return "\n".join(lines)


@wrap_aws_errors
def read_object(ctx: Context, identifier: str, limit: int = MAX_PREVIEW) -> Body:
    """Read up to `limit` bytes of an object. Never raises for being too big.

    `Range` is always sent, even for a tiny object, because asking past the end is
    harmless and it saves a `head_object` round trip - measured, not assumed.
    """
    where = Location.parse(identifier)
    client = ctx.client("s3")
    raw_response: dict[str, Any] = client.get_object(
        Bucket=where.bucket, Key=where.key, Range=f"bytes=0-{limit - 1}"
    )
    raw = raw_response["Body"].read()
    return Body(
        location=where,
        raw=raw,
        total=_total_of(raw_response, len(raw)),
        content_type=str(raw_response.get("ContentType", "")),
    )


def _total_of(response: dict[str, Any], read: int) -> int:
    """The object's real size. `ContentRange` is `bytes 0-2047/5825`.

    `ContentLength` is only what this *response* carried, so on a ranged read it
    is the truncated length and would make every big file look complete.
    """
    header = str(response.get("ContentRange", ""))
    if "/" in header:
        tail = header.rsplit("/", 1)[1].strip()
        if tail.isdigit():
            return int(tail)
    return int(response.get("ContentLength", read) or read)


def _self_check() -> None:
    where = Location("b", "logs/a.txt")

    whole = Body(where, b"hello\nworld\n", total=12, content_type="text/plain")
    assert whole.is_text and whole.text == "hello\nworld\n"
    assert not whole.truncated
    assert "text/plain" in whole.summary() and "of" not in whole.summary()

    # A PNG header: no NUL byte in it, which is why the NUL heuristic fails.
    png = b"\x89PNG\r\n\x1a\n" + b"IHDR" * 4
    binary = Body(where, png, total=len(png), content_type="image/png")
    assert b"\x00" not in png, "the very reason `is_text` decodes instead of scanning"
    assert not binary.is_text and binary.text is None
    dump = hexdump(png)
    assert dump.startswith("00000000  89 50 4e 47"), dump
    assert ".PNG" in dump

    # A truncated read that cut a multi-byte character in half is still text.
    full = "ěščřž" * 4
    cut = full.encode("utf-8")[:-1]
    partial = Body(where, cut, total=1000)
    assert partial.truncated
    assert partial.is_text, "a half character must not make a text file binary"
    assert partial.text is not None and partial.text.startswith("ěščřž")
    assert "of" in partial.summary()

    # The same broken tail on a COMPLETE read really is binary - nothing was cut.
    assert not Body(where, cut, total=len(cut)).is_text

    # THE BUG A LIVE READ FOUND, AND THIS SELF-CHECK HAD MISSED: a 512-byte slice
    # of a real PNG. It is truncated, so the half-character fallback applied - and
    # the first version dropped everything from `exc.start`, decoded the 8 leading
    # bytes and called a PNG text. A cut character is within 3 bytes of the end;
    # anything earlier is binary.
    sliced = (b"\x89PNG\r\n\x1a\n" + b"\xff" * 500)[:512]
    body = Body(where, sliced, total=5825, content_type="image/png")
    assert body.truncated
    assert not body.is_text, "a truncated PNG is still a PNG"
    # ...while a genuinely cut character, at the very end, still reads as text.
    assert Body(where, b"ok" + "č".encode()[:1], total=99).is_text

    # `ContentRange` wins over `ContentLength`, or every big file looks complete.
    assert _total_of({"ContentRange": "bytes 0-2047/5825", "ContentLength": 2048}, 2048) == 5825
    assert _total_of({"ContentLength": 27}, 27) == 27
    assert _total_of({}, 5) == 5
    # A malformed header must not crash the screen.
    assert _total_of({"ContentRange": "bytes */*", "ContentLength": 9}, 9) == 9

    assert MAX_PREVIEW > 0
    print("[OK] s3 read self-check passed")


if __name__ == "__main__":
    _self_check()
