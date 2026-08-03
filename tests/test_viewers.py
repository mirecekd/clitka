"""F3 and the `clitka_viewers` hook: reading a type Cloud Control cannot fetch.

The owner's report (2026-08-02): *"nejak to nevypada, ze bych se mohl podivat na
data v souboru na s3 - nejlepe pres F3, ktera je k tomu urcena"*. F3 went straight
to `GetResource`, which has no answer for `AWS::S3::Object` because CLITKA invented
that type - so it fell back to printing the listing row.

The two claims worth testing are opposite sides of one coin: an object now shows
its **bytes**, and every other type still goes through Cloud Control exactly as it
did before. The second is the one that would break eight plugins if it regressed.
"""

from __future__ import annotations

import pytest

from clitka.core import s3read
from clitka.core import viewer as vw
from clitka.core.actions import ResourceRef
from clitka.core.context import Context
from clitka.core.s3model import BUCKET, OBJECT, PREFIX
from clitka.services.s3 import viewers as s3viewers
from clitka.tui.viewedit import view_yaml


@pytest.fixture
def ctx():
    return Context(profile="demo", region="eu-central-1")


def ref(type_name: str = OBJECT, identifier: str = "b/logs/a.txt") -> ResourceRef:
    return ResourceRef.from_row(type_name, {"identifier": identifier})


# --- the hook -------------------------------------------------------------


def test_the_s3_plugin_publishes_its_viewers_through_the_hook():
    """The TUI never imports `services/s3` - that is the whole point of the seam."""
    assert {one.id for one in vw.registered()} >= {"s3.object", "s3.prefix"}


def test_an_object_and_a_prefix_are_claimed_and_nothing_else_is():
    assert vw.first_for(ref(OBJECT)).id == "s3.object"
    assert vw.first_for(ref(PREFIX, "b/logs/")).id == "s3.prefix"
    # A bucket IS a real Cloud Control type, and so is everything the other eight
    # plugins own. None of them may be intercepted.
    for type_name in (
        BUCKET,
        "AWS::EC2::Instance",
        "AWS::Logs::LogGroup",
        "AWS::Lambda::Function",
        "AWS::SSM::Parameter",
    ):
        assert vw.first_for(ref(type_name, "x")) is None, type_name
    assert vw.first_for(None) is None


def test_a_broken_applies_to_never_breaks_f3():
    def explode(_ref):
        raise RuntimeError("broken")

    good = vw.Viewer("good", lambda _c, _r: "ok")
    bad = vw.Viewer("bad", lambda _c, _r: "", applies_to=explode)
    assert [one.id for one in vw.available([bad, good], ref())] == ["good"]


# --- F3 on an object ------------------------------------------------------


@pytest.fixture
def served(monkeypatch):
    """Whatever bytes the next `read_object` should hand back."""
    holder: dict[str, bytes] = {"raw": b""}

    def fake(_ctx, identifier, limit=0):
        raw = holder["raw"]
        return s3read.Body(
            s3read.Location.parse(identifier),
            raw,
            total=holder.get("total", len(raw)),  # type: ignore[arg-type]
            content_type=str(holder.get("kind", "")),
        )

    monkeypatch.setattr(s3read, "read_object", fake)
    return holder


def test_f3_on_an_object_shows_the_file_not_the_row(ctx, served):
    """The owner's request, end to end through the same function F3 calls."""
    served["raw"] = b"isolation probe 1780899552\n"
    shown = view_yaml(ctx, ref())
    assert "isolation probe" in shown, shown
    # And it must NOT be the Cloud Control fallback any more.
    assert "GetResource failed" not in shown


def test_a_json_body_survives_intact(ctx, served):
    """JSON is brackets all the way down and every one has to reach the screen."""
    served["raw"] = b'{"level": "INFO", "tags": ["a", "b"]}'
    shown = view_yaml(ctx, ref())
    assert '{"level": "INFO", "tags": ["a", "b"]}' in shown, shown


def test_a_body_that_looks_like_markup_is_neutered(ctx, served):
    """The logs plugin's lesson - and `escape` only touches an actual tag shape.

    `["a", "b"]` is not one and is left alone; `[dim]` is one and would otherwise
    be swallowed, taking the rest of the line's styling with it.
    """
    served["raw"] = b"[dim]not a tag[/dim] and [bold]nor this[/bold]"
    shown = view_yaml(ctx, ref())
    assert "\\[dim]not a tag" in shown, shown
    assert "\\[bold]nor this" in shown


def test_a_binary_object_gets_a_hex_dump_rather_than_mojibake(ctx, served):
    served["raw"] = b"\x89PNG\r\n\x1a\n" + bytes(range(32))
    served["kind"] = "image/png"
    shown = view_yaml(ctx, ref())
    assert "89 50 4e 47" in shown, shown
    assert "not text" in shown


def test_an_empty_object_says_so(ctx, served):
    served["raw"] = b""
    assert "(empty)" in view_yaml(ctx, ref())


def test_a_truncated_body_admits_it(ctx, served):
    served["raw"] = b"x" * 100
    served["total"] = 10_000_000
    shown = view_yaml(ctx, ref())
    assert "cut off" in shown, shown
    assert "of" in shown, "the header names what was read out of what"


def test_every_view_names_the_uri_it_is_showing(ctx, served):
    served["raw"] = b"hello"
    assert "s3://b/logs/a.txt" in view_yaml(ctx, ref())


# --- F3 on a prefix -------------------------------------------------------


def test_f3_on_a_prefix_lists_it_instead_of_failing(ctx, monkeypatch):
    from clitka.core import s3

    listing = s3.Listing(s3.Location("b", "logs/"))
    listing.folders = [s3.Location("b", "logs/2026/")]
    listing.files = [s3.S3Object(s3.Location("b", "logs/a.txt"), size=27)]
    monkeypatch.setattr(s3, "browse", lambda *_a, **_kw: listing)

    shown = view_yaml(ctx, ref(PREFIX, "b/logs/"))
    assert "2026/" in shown and "a.txt" in shown
    assert "2 entries" in shown


# --- the untouched path ---------------------------------------------------


def test_an_unclaimed_type_still_falls_back_to_the_listing_row():
    """Exactly the behaviour before the hook existed, for every other type."""
    row = {"identifier": "b1", "name": "b1", "Arn": "arn:x"}
    text = view_yaml(Context(profile="no-such-profile-exists"), ResourceRef.from_row(BUCKET, row))
    assert text.startswith("# GetResource failed"), text
    assert "Arn: arn:x" in text


def test_the_self_checks_pass():
    vw._self_check()
    s3viewers._self_check()
