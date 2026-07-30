"""Actions: what you can *do* to a selected resource.

An action is a first-class object, not a menu item. The same object powers the
TUI F9 menu and (later) a CLI verb, so behaviour can never drift between the two
front ends. Services publish actions through the `clitka_actions` hook, which
keeps `core` free of per-service knowledge.

ponytail: `run` is a plain synchronous callable returning a finished
`ActionResult`, not the `AsyncIterator[Progress]` the design sketch allowed for.
Ceiling: no progress reporting for long operations - the TUI runs the call on a
thread worker and shows the result when it lands. Upgrade path: add an optional
`stream` attribute for actions that really need to report progress.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

Row = dict[str, Any]


@dataclass(frozen=True)
class ResourceRef:
    """What the caller has selected: a type, an identifier and the raw row."""

    type_name: str
    identifier: str
    row: Row = field(default_factory=dict)

    @classmethod
    def from_row(cls, type_name: str, row: Row) -> ResourceRef:
        return cls(type_name=type_name, identifier=str(row.get("identifier", "")), row=row)


@dataclass(frozen=True)
class ActionResult:
    """The outcome of an action, as something a screen can display."""

    title: str
    body: str = ""
    reload: bool = False
    """True when the caller should refetch the list (the resource changed)."""


@dataclass(frozen=True)
class Action:
    """One offered operation.

    `applies_to` decides whether the action shows up for a given resource, so
    the F9 menu is generated rather than hand-maintained per screen.
    """

    id: str
    label: str
    run: Callable[[Any, ResourceRef], ActionResult]
    key: str | None = None
    applies_to: Callable[[ResourceRef], bool] = lambda _ref: True
    destructive: bool = False

    def menu_label(self) -> str:
        """Label as shown in the F9 menu: key hint first, `!` when destructive."""
        prefix = f"{self.key}  " if self.key else ""
        suffix = "  (destructive)" if self.destructive else ""
        return f"{prefix}{self.label}{suffix}"


def available(actions: Sequence[Action], ref: ResourceRef | None) -> list[Action]:
    """The actions that apply to `ref`, in registration order.

    A broken `applies_to` must never take the menu down - such an action is
    simply left out.
    """
    if ref is None:
        return []
    out: list[Action] = []
    for action in actions:
        try:
            if action.applies_to(ref):
                out.append(action)
        except Exception:
            continue
    return out


def registered() -> list[Action]:
    """Every action published by every plugin."""
    from clitka.core import plugins

    return [a for a in plugins.actions() if isinstance(a, Action)]


def _self_check() -> None:
    ref = ResourceRef.from_row("AWS::S3::Bucket", {"identifier": "b1", "Arn": "arn:1"})
    assert ref.identifier == "b1"
    always = Action("a", "Always", lambda _c, _r: ActionResult("ok"))
    never = Action("b", "Never", lambda _c, _r: ActionResult("no"), applies_to=lambda _r: False)

    def explode(_ref: ResourceRef) -> bool:
        raise RuntimeError("applies_to is broken")

    broken = Action("c", "Broken", lambda _c, _r: ActionResult("no"), applies_to=explode)
    assert [a.id for a in available([always, never, broken], ref)] == ["a"]
    assert available([always], None) == []
    assert always.run(None, ref).title == "ok"
    assert "destructive" in Action("d", "Del", always.run, destructive=True).menu_label()
    print("[OK] actions self-check passed")


if __name__ == "__main__":
    _self_check()
