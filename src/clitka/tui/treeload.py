"""`BranchLoader`: filling a tree branch with resources, on a worker, page by page.

Mixed into `restree.ResourceTree`. Split out purely so both files stay under 8 kB,
but the seam is a real one: everything here is about *getting* the resources, and
nothing about the keyboard or the layout.

The placeholder dance in here is not decoration. Textual treats a childless node
as a leaf and refuses to expand it - and folds it straight back up if it is
emptied while expanding - so a branch always keeps at least one dataless child
until the real resources take over.
"""

from __future__ import annotations

from textual.widgets.tree import TreeNode
from textual.worker import get_current_worker

from clitka.core import cloudcontrol as cc
from clitka.core.errors import ExpiredLoginError
from clitka.tui.restypes import MAX_ROWS, PAGE_ROWS
from clitka.tui.treemodel import ResourceNode, TypeNode, sort_key

NOT_LOADED = "[dim](not loaded)[/dim]"
LOADING = "[dim]loading...[/dim]"
NONE_FOUND = "[dim](none)[/dim]"
# The TUI has no login screen on purpose; this is what the user has to do instead.
RELOGIN = "run `clitka auth login` in a shell, then F5"


class BranchLoader:
    """Expects `self.context`, `self.app`, `self.run_worker` and `self._title`."""

    def placeholder(self, branch: TreeNode, text: str) -> None:
        """Keep exactly one dataless child on `branch`, carrying `text`."""
        spare = [child for child in branch.children if child.data is None]
        if spare:
            spare[0].set_label(text)
            for extra in spare[1:]:
                extra.remove()
        else:
            branch.add_leaf(text)

    def drop_placeholders(self, branch: TreeNode) -> None:
        for child in list(branch.children):
            if child.data is None:
                child.remove()

    def reset_branch(self, branch: TreeNode, node: TypeNode) -> None:
        """Fold it, empty it, forget it - the next expand fetches again."""
        node.reset()
        branch.collapse()
        branch.remove_children()
        self.placeholder(branch, NOT_LOADED)
        branch.set_label(node.label())

    def load(self, branch: TreeNode, node: TypeNode) -> None:
        """Start filling `branch`. Safe to call again after `reset_branch`."""
        node.loading = True
        node.count = 0
        node.error = ""
        # Relabel rather than remove: emptying a branch mid-expand folds it.
        self.placeholder(branch, LOADING)
        branch.set_label(node.label())
        self.run_worker(  # type: ignore[attr-defined]
            lambda: self._fetch(branch, node),
            thread=True,
            exclusive=False,
            group=f"list-{node.type_name}",
        )

    def _fetch(self, branch: TreeNode, node: TypeNode) -> None:
        """Page through the type on a worker thread."""
        worker = get_current_worker()
        page: list[cc.Resource] = []
        try:
            for resource in cc.iter_resources(self.context, node.type_name):  # type: ignore[attr-defined]
                if worker.is_cancelled:
                    return
                page.append(resource)
                if len(page) >= PAGE_ROWS:
                    self.app.call_from_thread(self._page, branch, node, page)  # type: ignore[attr-defined]
                    page = []
                    if node.count >= MAX_ROWS:
                        node.capped = True
                        break
        except Exception as exc:  # any failure must reach the user, not the log
            self.app.call_from_thread(self._failed, branch, node, exc)  # type: ignore[attr-defined]
            return
        if worker.is_cancelled:
            return
        self.app.call_from_thread(self._page, branch, node, page)  # type: ignore[attr-defined]
        self.app.call_from_thread(self._done, branch, node)  # type: ignore[attr-defined]

    def _page(self, branch: TreeNode, node: TypeNode, found: list[cc.Resource]) -> None:
        """Merge a page in, keeping the branch alphabetical (owner's report).

        Cloud Control returns resources in no useful order, and appending each
        page left the branch looking shuffled. The pages still arrive while the
        user watches, so this inserts each resource where it belongs rather than
        rebuilding: `add_leaf(before=...)` against the leaves already there.

        ponytail: a linear scan per resource, so a page costs O(page x branch).
        Ceiling: `MAX_ROWS` is 2000, which is instant. Upgrade path: `bisect` over
        a kept list of keys.
        """
        if not found:
            return
        for resource in sorted(found, key=sort_key):
            leaf = ResourceNode(node.type_name, resource)
            # `add`, not `add_leaf`: a resource a plugin can list children of has
            # to be expandable, or an ECS task stays unreachable (owner's report).
            # `allow_expand` is decided per resource so every other type keeps a
            # plain leaf with no misleading fold arrow.
            branch.add(
                leaf.label(),
                data=leaf,
                before=self._slot(branch, resource),
                expand=False,
                allow_expand=self.leaf_allows_children(leaf),
            )
        self.drop_placeholders(branch)
        node.resources.extend(found)
        node.resources.sort(key=sort_key)  # keep the payload in step with the screen
        node.count = len(node.resources)
        branch.set_label(node.label())

    def leaf_allows_children(self, leaf: ResourceNode) -> bool:
        """Whether anything could hang under this resource.

        `ChildLoader` answers properly; a host without it says no, which is what
        keeps this mixin usable on its own.
        """
        ask = getattr(self, "has_child_listers", None)
        return bool(ask(leaf)) if callable(ask) else False

    @staticmethod
    def _slot(branch: TreeNode, resource: cc.Resource) -> TreeNode | None:
        """The first leaf that sorts *after* `resource`, or None for the end.

        Placeholders (`data is None`) are skipped - they are not resources, and
        `sort_key` would have nothing to read on them.
        """
        key = sort_key(resource)
        for child in branch.children:
            data = child.data
            if isinstance(data, ResourceNode) and sort_key(data.resource) > key:
                return child
        return None

    def _done(self, branch: TreeNode, node: TypeNode) -> None:
        node.loading = False
        node.loaded = True
        if node.count == 0:
            self.placeholder(branch, NONE_FOUND)
        branch.set_label(node.label())
        self._title(f"{node.type_name} - {node.count} resources")  # type: ignore[attr-defined]

    def _failed(self, branch: TreeNode, node: TypeNode, exc: Exception) -> None:
        node.loading = False
        node.loaded = True  # do not refetch on every expand; F5 is the retry
        node.error = str(exc)
        self.placeholder(branch, f"[red]{exc}[/red]")
        branch.set_label(node.label())
        message = f"{node.type_name}\n[ERROR] {exc}"
        if isinstance(exc, ExpiredLoginError):
            # Signing in is not part of the TUI (owner's call) - say what fixes it.
            message += f"\n[dim]{RELOGIN}[/dim]"
        self._title(message)  # type: ignore[attr-defined]


def _self_check() -> None:
    for text in (NOT_LOADED, LOADING, NONE_FOUND):
        assert text.startswith("[dim]") and text.endswith("[/dim]")
    # The contract: these are the four things a host screen must provide.
    for name in ("placeholder", "drop_placeholders", "reset_branch", "load", "_slot"):
        assert callable(getattr(BranchLoader, name))
    assert 0 < PAGE_ROWS < MAX_ROWS
    print("[OK] branch loader self-check passed")


if __name__ == "__main__":
    _self_check()
