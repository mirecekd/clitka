"""F4 on an S3 object: `$EDITOR`, then put it back - the owner's request (2026-08-03).

*"ale pomoci F4 objekt na S3 nejsem schopen editovat, je v tom nejaky hacek nebo
musim vypnout readonly?"* - neither: F4 was wired up for **no type at all**.

The test that matters is `test_the_whole_round_trip`, which uses a real editor
(`sed -i`) rather than a mock. Everything else here is a refusal, and each refusal
exists because the alternative silently loses data:

- read-only, refused before anything is even downloaded,
- a binary object, which would be replaced by its own hex dump,
- an unchanged file, which would still rewrite the ETag and LastModified,
- a **deleted** temp file, which must never be read as "empty the object".
"""

from __future__ import annotations

import os
import subprocess

import pytest

from clitka.core import s3read, s3write
from clitka.core.actions import ResourceRef
from clitka.core.context import Context
from clitka.core.errors import ReadOnlyError
from clitka.core.s3model import OBJECT, PREFIX, Location
from clitka.services.s3 import viewers as s3viewers

KEY = "b/logs/app.json"
TEXT = b'{"level": "INFO"}\n'


def ref(identifier: str = KEY, type_name: str = OBJECT) -> ResourceRef:
    return ResourceRef.from_row(type_name, {"identifier": identifier})


@pytest.fixture
def ctx():
    return Context(profile="demo", region="eu-central-1")


@pytest.fixture
def served(monkeypatch):
    """What `read_object` hands back, and what `put_object` was given."""
    state: dict = {"raw": TEXT, "kind": "application/json", "total": None, "put": []}

    def fake_read(_ctx, identifier, limit=0):
        raw = state["raw"]
        return s3read.Body(
            Location.parse(identifier),
            raw,
            total=state["total"] if state["total"] is not None else len(raw),
            content_type=state["kind"],
        )

    def fake_put(_ctx, identifier, raw, content_type=""):
        state["put"].append((identifier, raw, content_type))
        return len(raw)

    monkeypatch.setattr(s3read, "read_object", fake_read)
    monkeypatch.setattr(s3write, "read_object", fake_read)
    monkeypatch.setattr(s3write, "put_object", fake_put)
    monkeypatch.setenv("EDITOR", "true")  # a no-op "editor" unless a test says otherwise
    return state


# --- the refusals, all of them before the terminal is handed over ---------


def test_read_only_refuses_and_says_why(served):
    """The owner's actual question. Read-only was not the cause, but it does refuse."""
    with pytest.raises(ReadOnlyError) as caught:
        s3viewers.edit_object(Context(read_only=True), ref())
    assert "read-only" in str(caught.value)
    assert served["put"] == [], "nothing may be written"


def test_a_binary_object_is_not_editable(ctx, served):
    """Editing a hex dump and putting the text back would destroy the object."""
    served["raw"] = b"\x89PNG\r\n\x1a\n\xff\xfe"
    served["kind"] = "image/png"
    with pytest.raises(ValueError) as caught:
        s3viewers.edit_object(ctx, ref())
    assert "not text" in str(caught.value)
    assert "hex dump" in str(caught.value)


def test_an_object_too_big_to_read_whole_is_not_editable(ctx, served):
    """Saving a truncated body back would silently chop the object."""
    served["raw"] = b"x" * 100
    served["total"] = 10_000_000
    with pytest.raises(ValueError) as caught:
        s3viewers.edit_object(ctx, ref())
    assert "truncate" in str(caught.value)


def test_no_editor_at_all_is_a_sentence_not_a_crash(ctx, served, monkeypatch):
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setattr(s3write.shutil, "which", lambda _name: None)
    with pytest.raises(ValueError) as caught:
        s3viewers.edit_object(ctx, ref())
    assert "$EDITOR" in str(caught.value)


def test_a_prefix_has_no_f4_at_all():
    from clitka.core import viewer as vw

    prefix = vw.first_for(ref("b/logs/", PREFIX))
    assert prefix is not None and not prefix.editable
    obj = vw.first_for(ref())
    assert obj is not None and obj.editable


# --- the session ----------------------------------------------------------


def test_the_editor_is_handed_the_temp_file_and_needs_to_be_on_path(ctx, served):
    session = s3viewers.edit_object(ctx, ref())
    handoff = session.handoff
    # The last argv entry is the file; the first is what has to exist on PATH.
    assert handoff.argv[0] == "true"
    assert handoff.needs == ("true",)
    assert handoff.unavailable() == "", "`true` is on PATH, so the handoff can proceed"
    assert "app.json" in handoff.argv[-1], "the temp file keeps the key's name"
    assert os.path.exists(handoff.argv[-1])
    session.finish()


def test_an_unchanged_file_is_not_uploaded(ctx, served):
    """A no-op PutObject still rewrites the ETag and LastModified."""
    session = s3viewers.edit_object(ctx, ref())
    session.handoff.run()  # `true` - opens nothing, changes nothing
    message = session.finish()
    assert "unchanged" in message
    assert served["put"] == [], "an untouched object must not be written"


def test_a_deleted_temp_file_never_truncates_the_object(ctx, served):
    """Quitting an editor in a way that removes the file must not empty S3."""
    session = s3viewers.edit_object(ctx, ref())
    os.unlink(session.handoff.argv[-1])
    message = session.finish()
    assert "unchanged" in message
    assert served["put"] == []


def test_the_temp_file_is_cleaned_up(ctx, served):
    session = s3viewers.edit_object(ctx, ref())
    path = session.handoff.argv[-1]
    session.finish()
    assert not os.path.exists(path), "the temp file must not be left behind"


# --- the whole thing, with a real editor ---------------------------------


def test_the_whole_round_trip(ctx, served, monkeypatch):
    """A REAL editor (`sed -i`) edits the file, and the new bytes reach `put_object`.

    Deliberately not a mock: the claim under test is "what I typed ends up in S3",
    and a mocked editor would prove only that the code calls itself.
    """
    if subprocess.run(["which", "sed"], capture_output=True, check=False).returncode:
        pytest.skip("sed is not installed")
    monkeypatch.setenv("EDITOR", "sed -i s/INFO/DEBUG/")

    session = s3viewers.edit_object(ctx, ref())
    outcome = session.handoff.run()
    assert outcome.returncode == 0, outcome

    message = session.finish()
    assert "Saved" in message, message
    assert len(served["put"]) == 1
    identifier, raw, content_type = served["put"][0]
    assert identifier == KEY
    assert raw == b'{"level": "DEBUG"}\n', raw
    # The content type is preserved: PutObject replaces the object wholesale, so
    # anything not sent again is lost.
    assert content_type == "application/json"


def test_the_self_checks_pass():
    s3write._self_check()
    s3viewers._self_check()
