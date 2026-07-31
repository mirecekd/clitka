"""Preview tabs: what a service wants shown *about* a selected resource.

The tree is 1/3 of the screen; the other 2/3 is a tabbed preview. Two tabs come
from core for every type - "Overview" (grouped properties) and "Raw" (the API
shape) - and anything else is published by a service through the `clitka_previews`
hook. That is how a log group gets its last events, an S3 object its content and
an EC2 instance the list of things it is made of, without the tree importing a
single service module.

Deliberately the same shape as `core/actions.Action`: a filter predicate plus a
plain synchronous callable the TUI runs on a thread worker.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from clitka.core.actions import ResourceRef

OVERVIEW = "overview"
RAW = "raw"


@dataclass(frozen=True)
class PreviewTab:
    """One tab of the preview pane.

    `build` returns markup ready to display, and may call AWS - the pane always
    runs it on a worker. `applies_to` decides whether the tab appears at all, so
    the tab strip is generated rather than hand-maintained per type.
    """

    id: str
    label: str
    build: Callable[[Any, ResourceRef], str]
    applies_to: Callable[[ResourceRef], bool] = lambda _ref: True
    lazy: bool = True
    """True when `build` calls AWS, so the pane only runs it once the tab is shown."""

    def matches_type(self, type_name: str) -> bool:
        """Convenience for the common case of "only for this one type"."""
        return self.applies_to(ResourceRef(type_name, ""))


def for_type(type_name: str) -> Callable[[ResourceRef], bool]:
    """An `applies_to` that accepts exactly one resource type."""

    def predicate(ref: ResourceRef) -> bool:
        return ref.type_name == type_name

    return predicate


def available(tabs: Sequence[PreviewTab], ref: ResourceRef | None) -> list[PreviewTab]:
    """The tabs that apply to `ref`, in registration order.

    A broken `applies_to` must never empty the pane - such a tab is left out,
    exactly as `actions.available` does.
    """
    if ref is None:
        return []
    out: list[PreviewTab] = []
    for tab in tabs:
        try:
            if tab.applies_to(ref):
                out.append(tab)
        except Exception:
            continue
    return out


def registered() -> list[PreviewTab]:
    """Every preview tab published by every plugin."""
    from clitka.core import plugins

    return [t for t in plugins.previews() if isinstance(t, PreviewTab)]


def _self_check() -> None:
    ref = ResourceRef.from_row("AWS::Logs::LogGroup", {"identifier": "/aws/lambda/x"})
    events = PreviewTab(
        "events", "Events", lambda _c, _r: "built", applies_to=for_type("AWS::Logs::LogGroup")
    )
    other = PreviewTab("x", "X", lambda _c, _r: "", applies_to=for_type("AWS::S3::Bucket"))

    def explode(_ref: ResourceRef) -> bool:
        raise RuntimeError("applies_to is broken")

    broken = PreviewTab("b", "B", lambda _c, _r: "", applies_to=explode)
    assert [t.id for t in available([events, other, broken], ref)] == ["events"]
    assert available([events], None) == []
    assert events.matches_type("AWS::Logs::LogGroup")
    assert not events.matches_type("AWS::S3::Bucket")
    assert events.build(None, ref) == "built"

    assert events.lazy is True
    print("[OK] preview hook self-check passed")


if __name__ == "__main__":
    _self_check()
