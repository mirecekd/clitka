"""The landing screen: a tree of resource types beside a preview of what is picked.

The owner's model: 1/3 tree on the left, 2/3 detail on the right. The tree lists
only the *interesting* types; open one and its resources unfold underneath, loaded
on demand and appearing page by page; close it and they fold away but are kept.
Anything not on the list is one `:` away. Pressing enter on a resource previews it
- moving the cursor never fetches - and a resource a plugin can list children of
opens further still (an ECS cluster into its Tasks).

The mixins do the work: `BranchLoader` fetches a type, `ChildLoader` a plugin's
sub-branch, `TreeSelection` answers "what is picked", `ActionHost` is F9,
`ViewEditHost` F3/F4 and `ShellHost` the `x` handoff. This file is layout and
keyboard only.
"""

from __future__ import annotations

from collections.abc import Sequence

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Static, Tree

from clitka.core.context import Context
from clitka.tui.actionhost import ActionHost
from clitka.tui.childload import ChildLoader
from clitka.tui.childmodel import ChildNode
from clitka.tui.dropdown import TextDrop
from clitka.tui.keybar import KeyBar
from clitka.tui.preview import PreviewPane
from clitka.tui.restypes import TREE_HELP, TREE_TYPES
from clitka.tui.shellhost import ShellHost
from clitka.tui.status import StatusBar
from clitka.tui.treebranch import BranchKeeper
from clitka.tui.treekeys import TREE_BINDINGS, TREE_CSS
from clitka.tui.treeload import BranchLoader
from clitka.tui.treemodel import ResourceNode, TypeNode
from clitka.tui.treesel import TreeSelection
from clitka.tui.viewedit import ViewEditHost

Payload = TypeNode | ResourceNode | ChildNode


# `TreeSelection` comes FIRST: it supplies `selected_ref` / `_title` /
# `_after_action`, which `ActionHost` declares abstract. Put ActionHost ahead of
# it and the MRO picks the NotImplementedError stubs instead - F9 then dies on
# every keypress.
class ResourceTree(
    TreeSelection,
    ViewEditHost,
    ShellHost,
    ActionHost,
    BranchKeeper,
    ChildLoader,
    BranchLoader,
    Screen[None],
):
    """The tree of resource types. F9 acts on the resource under the cursor."""

    # Both live in `treekeys.py` - declarations only, and this file was over 8 kB.
    DEFAULT_CSS = TREE_CSS
    BINDINGS = TREE_BINDINGS

    def __init__(self, context: Context, types: Sequence[str] = TREE_TYPES) -> None:
        super().__init__()
        self.context = context
        self.types = list(types)
        self.title_text = "Resources"

    # --- layout -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield KeyBar()
        yield Static(self.title_text, id="tree-title")
        tree: Tree[Payload] = Tree("Resources", id="resource-tree")
        tree.show_root = False
        tree.guide_depth = 3
        with Horizontal(id="split"):
            with Vertical(id="tree-side"):
                yield tree
            yield PreviewPane(self.context)
        yield StatusBar(self.context)

    def on_mount(self) -> None:
        tree = self.rtree
        for type_name in self.types:
            self._add_type(type_name)
        tree.focus()
        # The root is hidden and the cursor starts on it, so the first key press
        # would land on nothing. Move it with `cursor_line`, NOT `select_node()`:
        # selecting a node toggles it, and the screen must open fully folded.
        if tree.root.children:
            tree.cursor_line = 0
        self._overview()

    @property
    def rtree(self) -> Tree[Payload]:
        """Our `Tree` widget.

        Deliberately NOT named `tree`: `Screen.tree` is a Textual property and
        shadowing it breaks things only at runtime - the `Widget.region` trap.
        """
        return self.query_one(Tree)

    def _overview(self) -> None:
        self._title(f"{len(self.types)} resource types - enter opens one, `:` adds one")

    def _title(self, text: str) -> None:
        self.title_text = text
        self.query_one("#tree-title", Static).update(text)

    # --- branches ---------------------------------------------------------
    # `_add_type`, `add_type`, `adopt_types` and `_reveal` are `BranchKeeper`'s.

    def on_tree_node_expanded(self, event: Tree.NodeExpanded[Payload]) -> None:
        """Three kinds of node open here, and each fetches differently: a type
        through Cloud Control, a plugin sub-branch through a `ChildLister`, and a
        *resource* only grows its sub-branches - which is what makes an ECS task
        clickable at all (owner's report, 2026-08-01).
        """
        data = event.node.data
        if isinstance(data, TypeNode) and not data.loaded and not data.loading:
            self.load(event.node, data)
        elif isinstance(data, ChildNode) and not data.loaded and not data.loading:
            self.load_children(event.node, data)
        elif isinstance(data, ResourceNode):
            self.attach_children(event.node, data)

    def adopt_context(self, context: Context) -> None:
        """F2/F3 switched profile or region - everything loaded is now stale."""
        self.context = context
        self.query_one(StatusBar).set_context(context)
        self.preview.adopt_context(context)
        self.preview.show(None)
        self.action_reload()

    # --- actions ----------------------------------------------------------

    def action_reload(self) -> None:
        """F5: fold everything and forget it - the next open refetches."""
        for branch in self.rtree.root.children:
            if isinstance(branch.data, TypeNode):
                self.reset_branch(branch, branch.data)
        self._overview()

    def action_reload_branch(self) -> None:
        """Refetch the *nearest* branch the cursor is in - after a delete, say.

        It may be a plugin sub-branch: after stopping a task, refetching that
        `Tasks` list beats rebuilding the whole cluster type from Cloud Control.
        """
        node = self.rtree.cursor_node
        while node is not None and not isinstance(node.data, TypeNode | ChildNode):
            node = node.parent
        if node is None:
            return
        data = node.data
        if isinstance(data, ChildNode):
            data.reset()
            node.remove_children()
            self.load_children(node, data)
        elif isinstance(data, TypeNode):
            data.reset()
            self.load(node, data)

    def action_help(self) -> None:
        self.app.push_screen(TextDrop("F1  Help - resources", TREE_HELP, "f1"))
