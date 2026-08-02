"""`ChildNode`: the payload of a sub-branch under a resource leaf.

No Textual import, so the labelling and the "is this loaded yet" logic are testable
without a screen - the same seam as `treemodel.py`, whose `TypeNode` this mirrors
almost line for line. It is a separate type rather than a reused `TypeNode` because
a sub-branch is not a type: it belongs to *one parent resource* and knows which
`ChildLister` fills it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from clitka.core import cloudcontrol as cc
from clitka.core.actions import ResourceRef
from clitka.core.lister import ChildLister


@dataclass
class ChildNode:
    """A "Tasks" / "Services" sub-branch hanging under one resource."""

    lister: ChildLister
    parent_ref: ResourceRef
    """The resource this is a child listing of - what `lister.list` is called with."""
    loaded: bool = False
    loading: bool = False
    count: int = 0
    error: str = ""
    resources: list[cc.Resource] = field(default_factory=list)

    @property
    def type_name(self) -> str:
        """What comes out of here - so `ActionHost`'s headings read correctly."""
        return self.lister.child_type

    def label(self) -> str:
        """`Tasks (3)`, or what went wrong instead - `TypeNode.label`'s shape."""
        head = f"[b]{self.lister.label}[/b]"
        if self.error:
            return f"{head}   [red][ERROR] {self.error}[/red]"
        if self.loading:
            return f"{head}   [dim]loading...[/dim]"
        if not self.loaded:
            return head
        if self.count == 0:
            return f"{head}   [dim](none)[/dim]"
        return f"{head}   [dim]({self.count})[/dim]"

    def reset(self) -> None:
        """Forget everything - F5 on the branch, or after an action changed it."""
        self.loaded = False
        self.loading = False
        self.count = 0
        self.error = ""
        self.resources = []


def _self_check() -> None:
    lister = ChildLister("ecs.tasks", "Tasks", "AWS::ECS::Task", lambda _c, _r: [])
    node = ChildNode(lister, ResourceRef.from_row("AWS::ECS::Cluster", {"identifier": "prod"}))

    assert node.type_name == "AWS::ECS::Task"
    assert node.label() == "[b]Tasks[/b]"
    node.loading = True
    assert "loading" in node.label()
    node.loading, node.loaded, node.count = False, True, 3
    assert "(3)" in node.label()
    node.count = 0
    assert "(none)" in node.label()
    node.error = "AccessDenied"
    assert "[ERROR] AccessDenied" in node.label()
    node.reset()
    assert node.label() == "[b]Tasks[/b]" and not node.loaded
    # The parent is kept, because that is what the lister is called with.
    assert node.parent_ref.identifier == "prod"
    print("[OK] child model self-check passed")


if __name__ == "__main__":
    _self_check()
