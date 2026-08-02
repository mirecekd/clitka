"""`ChildLoader`: the sub-branches a plugin hangs under a resource leaf.

Mixed into `restree.ResourceTree` beside `BranchLoader`. The two are deliberately
separate: `BranchLoader` fetches a *type* through `cloudcontrol.iter_resources`,
this one fetches *children of one resource* through a plugin's `ChildLister`, which
is the only way a type Cloud Control cannot list (`AWS::ECS::Task`) gets into the
tree at all. That was the owner's report: the tasks were not clickable.

The Textual traps from `treeload.py` all apply here too - above all: a childless
node is a leaf and cannot be expanded, so a sub-branch keeps one dataless
placeholder child until the real children arrive.

A resource leaf grows its sub-branches **when the leaf itself is opened**, not when
the page that carried it landed: `available()` is pure and offline, but building the
nodes for 2000 leaves up front would cost 2000 nodes nobody looks at.
"""

from __future__ import annotations

from typing import Any

from textual.widgets.tree import TreeNode
from textual.worker import get_current_worker

from clitka.core import actions as act
from clitka.core import lister as ls
from clitka.tui.childmodel import ChildNode
from clitka.tui.treeload import LOADING, NONE_FOUND, NOT_LOADED
from clitka.tui.treemodel import ResourceNode, sort_key


class ChildLoader:
    """Expects `self.context`, `self.app`, `self.run_worker`, `self.placeholder`."""

    def attach_children(self, leaf: TreeNode, node: ResourceNode) -> bool:
        """Give `leaf` a sub-branch per applicable `ChildLister`. True if any.

        Idempotent: called every time the leaf is opened, and does nothing on the
        second visit. Offline - `applies_to` is a predicate, not a call.
        """
        if node.expanded_children:
            return bool(leaf.children)
        node.expanded_children = True
        ref = act.ResourceRef.from_row(node.type_name, node.resource.row())
        for one in ls.available(self.registered_listers(), ref):
            child = ChildNode(one, ref)
            branch = leaf.add(child.label(), data=child, expand=False)
            self.placeholder(branch, NOT_LOADED)  # type: ignore[attr-defined]
        return bool(leaf.children)

    def registered_listers(self) -> list[ls.ChildLister]:
        """Overridden in the tests; the real answer comes from the plugins."""
        return ls.registered()

    def has_child_listers(self, node: ResourceNode) -> bool:
        """Whether this resource is worth a fold arrow, cached per type.

        `BranchLoader._page` asks this for **every** leaf it inserts, up to
        `MAX_ROWS` of them, so it must not walk the plugin list 2000 times - and
        the answer only ever depends on the type. Asked with a bare `ResourceRef`
        (no row) on purpose: a lister keying on a *property* then gets no arrow,
        which is the right way round - an arrow onto nothing is worse than none.
        """
        cache = getattr(self, "_child_types", None)
        if cache is None:
            cache = {}
            self._child_types = cache
        known = cache.get(node.type_name)
        if known is None:
            probe = act.ResourceRef(node.type_name, "")
            known = bool(ls.available(self.registered_listers(), probe))
            cache[node.type_name] = known
        return known

    def load_children(self, branch: TreeNode, node: ChildNode) -> None:
        """Start filling a sub-branch. Safe to call again after a reset."""
        node.loading = True
        node.count = 0
        node.error = ""
        self.placeholder(branch, LOADING)  # type: ignore[attr-defined]
        branch.set_label(node.label())
        self.run_worker(  # type: ignore[attr-defined]
            lambda: self._children(branch, node),
            thread=True,
            exclusive=False,
            group=f"children-{node.lister.id}",
        )

    def _children(self, branch: TreeNode, node: ChildNode) -> None:
        """One `ChildLister` call on a worker thread.

        ponytail: not paged - a cluster has tens of tasks, not thousands. Ceiling: a
        pathological parent blocks the worker until its whole listing is in. Upgrade
        path: let `ChildLister.list` return an iterator and reuse `treeload._page`.
        """
        worker = get_current_worker()
        try:
            found = node.lister.list(self.context, node.parent_ref)  # type: ignore[attr-defined]
        except Exception as exc:  # any failure must reach the user, not the log
            self.app.call_from_thread(self._children_failed, branch, node, exc)  # type: ignore[attr-defined]
            return
        if worker.is_cancelled:
            return
        self.app.call_from_thread(self._children_done, branch, node, found)  # type: ignore[attr-defined]

    def _children_done(self, branch: TreeNode, node: ChildNode, found: list) -> None:
        for resource in sorted(found, key=sort_key):
            leaf = ResourceNode(resource.type_name, resource)
            branch.add(leaf.label(), data=leaf, expand=False)
        self.drop_placeholders(branch)  # type: ignore[attr-defined]
        node.loading = False
        node.loaded = True
        node.resources = list(found)
        node.count = len(found)
        if not found:
            self.placeholder(branch, NONE_FOUND)  # type: ignore[attr-defined]
        branch.set_label(node.label())
        self._title(f"{node.lister.label} - {node.count}")  # type: ignore[attr-defined]

    def _children_failed(self, branch: TreeNode, node: ChildNode, exc: Exception) -> None:
        node.loading = False
        node.loaded = True  # F5 is the retry, as on a type branch
        node.error = str(exc)
        self.placeholder(branch, f"[red]{exc}[/red]")  # type: ignore[attr-defined]
        branch.set_label(node.label())
        self._title(f"{node.lister.label}\n[ERROR] {exc}")  # type: ignore[attr-defined]


def _self_check() -> None:
    """The contract, plus the one thing worth pinning: attach is idempotent."""
    from clitka.core import cloudcontrol as cc

    calls: list[str] = []

    class FakeNode:
        def __init__(self) -> None:
            self.children: list[object] = []

        def add(self, label, data=None, expand=False):
            self.children.append(data)
            return FakeNode()

    class Host(ChildLoader):
        def placeholder(self, branch, text) -> None:
            calls.append(text)

        def registered_listers(self):
            return [
                ls.ChildLister(
                    "t", "Tasks", "AWS::ECS::Task", lambda _c, _r: [], applies_to=lambda _r: True
                )
            ]

    node = ResourceNode("AWS::ECS::Cluster", cc.Resource("AWS::ECS::Cluster", "prod", {}))
    leaf: Any = FakeNode()
    host = Host()
    # The fold arrow is per type and cached: a 2000-row branch asks the plugin
    # list once, not 2000 times.
    assert host.has_child_listers(node) is True
    assert host._child_types == {"AWS::ECS::Cluster": True}
    assert host.attach_children(leaf, node) is True
    assert len(leaf.children) == 1 and node.expanded_children
    # A second open must not double the sub-branches.
    assert host.attach_children(leaf, node) is True
    assert len(leaf.children) == 1, leaf.children

    # A resource nothing applies to stays a plain leaf.
    class Empty(Host):
        def registered_listers(self):
            return []

    bucket = ResourceNode("AWS::S3::Bucket", cc.Resource("AWS::S3::Bucket", "b", {}))
    empty: Any = FakeNode()
    assert Empty().attach_children(empty, bucket) is False
    assert Empty().has_child_listers(bucket) is False
    print("[OK] child loader self-check passed")


if __name__ == "__main__":
    _self_check()
