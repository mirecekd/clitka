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
from clitka.tui.treemodel import ResourceNode, TypeNode

NOT_LOADED = "[dim](not loaded)[/dim]"
LOADING = "[dim]loading...[/dim]"
NONE_FOUND = "[dim](none)[/dim]"


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
        if not found:
            return
        for resource in found:
            leaf = ResourceNode(node.type_name, resource)
            branch.add_leaf(leaf.label(), data=leaf)
        self.drop_placeholders(branch)
        node.resources.extend(found)
        node.count = len(node.resources)
        branch.set_label(node.label())

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
        self._title(f"{node.type_name}\n[ERROR] {exc}")  # type: ignore[attr-defined]
        if isinstance(exc, ExpiredLoginError):
            # A dead login is the one error the user can actually fix from here.
            offer = getattr(self.app, "offer_login", None)  # type: ignore[attr-defined]
            if offer is not None:
                offer(f"{node.type_name}: the login has expired")


def _self_check() -> None:
    for text in (NOT_LOADED, LOADING, NONE_FOUND):
        assert text.startswith("[dim]") and text.endswith("[/dim]")
    # The contract: these are the four things a host screen must provide.
    for name in ("placeholder", "drop_placeholders", "reset_branch", "load"):
        assert callable(getattr(BranchLoader, name))
    assert 0 < PAGE_ROWS < MAX_ROWS
    print("[OK] branch loader self-check passed")


if __name__ == "__main__":
    _self_check()
