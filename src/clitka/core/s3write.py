"""Putting an object back after `$EDITOR` - the write half of `core/s3read.py`.

The owner asked why F4 could not edit an object (2026-08-03). It could not because
F4 was never wired up **for any type** - `ViewEditHost.action_edit` printed what it
*would* do. Read-only had nothing to do with it.

The mechanism is the one the terminal handoff already proved: write the bytes to a
temporary file, hand the whole terminal to `$EDITOR` inside `App.suspend()`, and put
the file back if it changed. No pty plumbing - `core/handoff.Handoff` does that part.

**Three refusals, and each of them exists because the alternative loses data:**

1. **A binary object is not editable.** The screen shows a hex dump; letting someone
   "edit" that and putting the text back would replace a PNG with its own hex dump.
2. **Read-only is refused BEFORE the editor opens.** Discovering it after ten
   minutes of typing would be the worst possible moment - the `ec2.power()` rule:
   every knowable complaint arrives as a sentence first.
3. **An unchanged file is not uploaded.** A no-op `PutObject` still rewrites the
   ETag and `LastModified`, which lies to everything watching the bucket - and it
   silently drops any storage class or metadata the object had.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clitka.core.context import Context
from clitka.core.errors import wrap_aws_errors
from clitka.core.handoff import Handoff
from clitka.core.s3model import Location, human_size
from clitka.core.s3read import Body, read_object

__all__ = ["EDITORS", "Edit", "editor_argv", "prepare_edit", "put_object"]

EDITORS = ("nano", "vim", "vi")
"""What to try when `$EDITOR` and `$VISUAL` are both unset, in this order.

`vi` is last and is the POSIX guarantee; `nano` is first because someone who never
set `$EDITOR` is the person least likely to know how to leave `vim`.
"""


def editor_argv(path: Path) -> list[str]:
    """The editor command, from `$VISUAL` / `$EDITOR` or the first one installed.

    `$EDITOR` may carry flags (`code -w`, `emacs -nw`), so it is split - but on
    whitespace only, never through a shell, because a bucket key is attacker-shaped
    input and `Handoff` builds argv rather than a command string.
    """
    chosen = os.environ.get("VISUAL") or os.environ.get("EDITOR") or ""
    parts = chosen.split() if chosen.strip() else []
    if not parts:
        found = next((name for name in EDITORS if shutil.which(name)), "")
        parts = [found] if found else []
    return [*parts, str(path)] if parts else []


@dataclass
class Edit:
    """One edit in flight: the temp file, what to run, and what it started as.

    Not frozen - `changed()` is asked after the editor has written to `path`.
    """

    body: Body
    path: Path
    argv: list[str]
    original: bytes

    @property
    def location(self) -> Location:
        return self.body.location

    def handoff(self) -> Handoff:
        """The editor as a `Handoff`, so `ShellHost` runs it the way it runs a shell.

        `needs` names the editor itself rather than the AWS CLI - this handoff does
        not shell out to `aws` at all, and `Handoff.unavailable()` is checked before
        the app suspends.
        """
        return Handoff(
            label=f"edit {self.location.uri}",
            argv=self.argv,
            needs=(self.argv[0],) if self.argv else (),
        )

    def current(self) -> bytes:
        """Whatever is in the temp file now. Empty when the editor removed it."""
        try:
            return self.path.read_bytes()
        except OSError:
            return b""

    def changed(self) -> bool:
        """True when the file on disk differs from what was downloaded.

        A deleted temp file counts as **no change**, not as "empty the object":
        quitting an editor in a way that removes the file must never truncate
        something in S3.
        """
        if not self.path.exists():
            return False
        return self.current() != self.original

    def cleanup(self) -> None:
        """Remove the temp file and its directory. Never raises."""
        try:
            self.path.unlink(missing_ok=True)
            self.path.parent.rmdir()
        except OSError:
            pass


def prepare_edit(ctx: Context, identifier: str) -> Edit:
    """Everything that can fail with a message, before the terminal is handed over.

    Raises rather than returning a half-ready edit: read-only, a binary body, a
    truncated body or no editor at all are all better said as a sentence than
    discovered after the editor has closed.
    """
    ctx.require_write(f"edit {identifier}")

    body = read_object(ctx, identifier)
    if not body.is_text:
        raise ValueError(
            f"{body.location.uri} is not text ({body.content_type or 'unknown type'}) - "
            "editing it here would replace it with its own hex dump"
        )
    if body.truncated:
        raise ValueError(
            f"{body.location.uri} is {human_size(body.total)} and only the first "
            f"{human_size(len(body.raw))} was read - editing it would truncate the object"
        )

    import tempfile

    # A directory of our own, so the file can keep the key's own name: `$EDITOR`
    # gets its syntax highlighting from the extension, and `tmpXXXX` has none.
    folder = Path(tempfile.mkdtemp(prefix="clitka-s3-"))
    path = folder / _filename(body)
    path.write_bytes(body.raw)

    argv = editor_argv(path)
    if not argv:
        path.unlink(missing_ok=True)
        folder.rmdir()
        raise ValueError(
            "no editor found - set $EDITOR (or install one of: " + ", ".join(EDITORS) + ")"
        )
    return Edit(body=body, path=path, argv=argv, original=body.raw)


def _filename(body: Body) -> str:
    """A temp file name that keeps the key's extension - and nothing of its path."""
    stem = body.location.label.rsplit("/", 1)[-1] or "object"
    stem = stem.replace("\x00", "")
    return stem if stem else "object"


@wrap_aws_errors
def put_object(ctx: Context, identifier: str, raw: bytes, content_type: str = "") -> int:
    """Write `raw` to the object. Returns how many bytes went up.

    The content type is preserved deliberately: `PutObject` **replaces** the object
    wholesale, so anything not sent again is lost. A body that arrived as
    `application/json` must not come back as `binary/octet-stream`.
    """
    ctx.require_write(f"write {identifier}")
    where = Location.parse(identifier)
    if not where.key:
        raise ValueError(f"{identifier} names a bucket, not an object")
    kwargs: dict[str, Any] = {"Bucket": where.bucket, "Key": where.key, "Body": raw}
    if content_type:
        kwargs["ContentType"] = content_type
    ctx.client("s3").put_object(**kwargs)
    return len(raw)


def _self_check() -> None:
    from clitka.core.errors import ReadOnlyError

    where = Location("b", "logs/app.json")

    # --- the editor command
    saved = {name: os.environ.get(name) for name in ("EDITOR", "VISUAL")}
    try:
        os.environ.pop("VISUAL", None)
        os.environ["EDITOR"] = "vim"
        assert editor_argv(Path("/tmp/x.json")) == ["vim", "/tmp/x.json"]
        # $EDITOR may carry flags, and they must survive as separate argv entries.
        os.environ["EDITOR"] = "code -w"
        assert editor_argv(Path("/tmp/x")) == ["code", "-w", "/tmp/x"]
        # $VISUAL wins over $EDITOR, which is what every other tool does.
        os.environ["VISUAL"] = "nano"
        assert editor_argv(Path("/tmp/x"))[0] == "nano"
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    # --- changed() is what stops a pointless PutObject
    import tempfile

    folder = Path(tempfile.mkdtemp(prefix="clitka-selfcheck-"))
    path = folder / "app.json"
    original = b'{"a": 1}\n'
    path.write_bytes(original)
    body = Body(where, original, total=len(original), content_type="application/json")
    edit = Edit(body=body, path=path, argv=["true", str(path)], original=original)

    assert not edit.changed(), "an untouched file must not be uploaded"
    path.write_bytes(b'{"a": 2}\n')
    assert edit.changed() and edit.current() == b'{"a": 2}\n'
    # A removed temp file is "no change", NOT "empty the object".
    path.unlink()
    assert not edit.changed(), "a deleted temp file must never truncate S3"
    edit.cleanup()

    # The handoff needs the EDITOR on PATH, not the AWS CLI - nothing here shells
    # out to `aws`, and the check happens before the app suspends.
    made = Edit(body=body, path=Path("/tmp/x"), argv=["vim", "/tmp/x"], original=b"")
    assert made.handoff().needs == ("vim",)
    assert made.handoff().argv == ["vim", "/tmp/x"]

    # --- read-only is refused, and it is refused FIRST
    locked = Context(read_only=True)
    for call in (
        lambda: prepare_edit(locked, "b/logs/app.json"),
        lambda: put_object(locked, "b/logs/app.json", b"x"),
    ):
        try:
            call()
        except ReadOnlyError:
            pass
        else:  # pragma: no cover - the guard is the point
            raise AssertionError("read-only must refuse before anything else happens")
    print("[OK] s3 write self-check passed")


if __name__ == "__main__":
    _self_check()
