"""Viewers: how F3 shows a type that Cloud Control cannot fetch.

The owner's report (2026-08-02): *"nejak to nevypada, ze bych se mohl podivat na
data v souboru na s3 - nejlepe pres F3, ktera je k tomu urcena"*. Quite right. F3
went straight to `cloudcontrol.get_resource`, which is the correct answer for a
real resource type and **no answer at all** for `AWS::S3::Object` - a type CLITKA
invented, so `GetResource` fails and F3 fell back to printing the listing row. The
size and the ETag are not "the data in the file".

This is the fifth plugin hook and the last one the four-hook set was missing: the
others let a plugin add a *command*, an *action*, a *tab* or a *sub-branch*, but
none of them could answer "and how is this thing read?". Without it `tui/viewedit.py`
would have to import `services/s3`, which is precisely what the plugin seam exists
to prevent.

Deliberately the same shape as `core/preview.PreviewTab` and `core/lister.ChildLister`:
a filter predicate plus a plain synchronous callable the TUI runs on a thread.
`tui/viewedit.py` asks here first and only falls through to Cloud Control when
nothing claims the type - so every existing type behaves exactly as it did.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from clitka.core.actions import ResourceRef

__all__ = ["Viewer", "available", "first_for", "registered"]


@dataclass(frozen=True)
class Viewer:
    """How to render one resource in full, for F3.

    `view` returns text ready to display and may call AWS - `ViewEditHost` always
    runs it on a worker, exactly as it runs `GetResource` now.
    """

    id: str
    view: Callable[[Any, ResourceRef], str]
    applies_to: Callable[[ResourceRef], bool] = lambda _ref: True
    label: str = ""
    """What the result screen is titled. Falls back to the identifier."""


def available(viewers: Sequence[Viewer], ref: ResourceRef | None) -> list[Viewer]:
    """The viewers that apply to `ref`, in registration order.

    A broken `applies_to` is skipped rather than allowed to break F3 - the same
    rule as `actions.available`, `preview.available` and `lister.available`.
    """
    if ref is None:
        return []
    out: list[Viewer] = []
    for one in viewers:
        try:
            if one.applies_to(ref):
                out.append(one)
        except Exception:
            continue
    return out


def first_for(ref: ResourceRef | None) -> Viewer | None:
    """The viewer F3 should use, or None to mean "go through Cloud Control".

    First match wins, so a plugin registered earlier takes precedence - the
    `ActionMenu.on_key` rule, and worth knowing if two ever claim one type.
    """
    found = available(registered(), ref)
    return found[0] if found else None


def registered() -> list[Viewer]:
    """Every viewer published by every plugin."""
    from clitka.core import plugins

    return [one for one in plugins.viewers() if isinstance(one, Viewer)]


def _self_check() -> None:
    obj = ResourceRef.from_row("AWS::S3::Object", {"identifier": "b/a.txt"})
    bucket = ResourceRef.from_row("AWS::S3::Bucket", {"identifier": "b"})

    body = Viewer(
        id="s3.object",
        view=lambda _ctx, ref: f"the bytes of {ref.identifier}",
        applies_to=lambda ref: ref.type_name == "AWS::S3::Object",
    )

    def explode(_ref: ResourceRef) -> bool:
        raise RuntimeError("applies_to is broken")

    broken = Viewer("bad", lambda _c, _r: "", applies_to=explode)

    assert [one.id for one in available([body, broken], obj)] == ["s3.object"]
    # A type nothing claims must yield nothing, so F3 keeps its Cloud Control path.
    assert available([body], bucket) == []
    assert available([body], None) == []
    assert body.view(None, obj) == "the bytes of b/a.txt"
    print("[OK] viewer hook self-check passed")


if __name__ == "__main__":
    _self_check()
