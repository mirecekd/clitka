"""The landing screen: a tree of resource types that expand into their resources.

The owner's model (2026-07-30): the screen lists only the *interesting* types.
Open one and its resources unfold underneath, loaded on demand and appearing page
by page; close it and they fold away but are kept. Anything not on the list is one
`:` away and is then added as a further branch.

`BranchLoader` does the fetching, `ActionHost` does F9. This file is the keyboard
and the layout.
"""

from __future__ import annotations

from collections.abc import Sequence

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Static, Tree
from textual.widgets.tree import TreeNode

from clitka.core import actions as act
from clitka.core.context import Context
from clitka.tui.actionhost import ActionHost
from clitka.tui.dropdown import TextDrop
from clitka.tui.keybar import KeyBar
from clitka.tui.restypes import TREE_HELP, TREE_TYPES
from clitka.tui.status import StatusBar
from clitka.tui.treeload import NOT_LOADED, BranchLoader
from clitka.tui.treemodel import ResourceNode, TypeNode

Payload = TypeNode | ResourceNode


class ResourceTree(ActionHost, BranchLoader, Screen[None]):
    """The tree of resource types. F9 acts on the resource under the cursor."""

    DEFAULT_CSS = """
    ResourceTree #tree-title {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    ResourceTree Tree {
        height: 1fr;
        padding: 0 1;
    }
    """
    BINDINGS = [
        # Textual binds enter to "select", which only posts a NodeSelected message
        # and expands nothing. `space` does toggle, but enter is what a user
        # reaches for, so it is claimed here.
        Binding("enter", "toggle", "Open / close", show=False, priority=True),
        Binding("f1", "help", "Help", show=False),
        # A Screen shadows the App's bindings, so the app-wide keys are forwarded.
        Binding("f2", "app.switch_profile", "Profile", show=False),
        Binding("f3", "app.switch_region", "Region", show=False),
        Binding("f4", "app.login", "Login", show=False),
        Binding("f5", "reload", "Refresh", show=False),
        Binding("f9", "actions", "Actions", show=False),
        Binding("f10", "quit", "Quit", show=False),
    ]

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
        yield tree
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

    def _add_type(self, type_name: str) -> TreeNode[Payload]:
        node = TypeNode(type_name)
        branch = self.rtree.root.add(node.label(), data=node, expand=False)
        self.placeholder(branch, NOT_LOADED)
        return branch

    def add_type(self, type_name: str) -> None:
        """`:` picked a type - add it, or jump to it if it is already a branch."""
        for branch in self.rtree.root.children:
            data = branch.data
            if isinstance(data, TypeNode) and data.type_name == type_name:
                self._reveal(branch)
                return
        self.types.append(type_name)
        self._reveal(self._add_type(type_name))

    def _reveal(self, branch: TreeNode[Payload]) -> None:
        """Open a branch and put the cursor on it, without toggling it shut."""
        branch.expand()
        self.rtree.scroll_to_node(branch)
        for index, line in enumerate(self.rtree._tree_lines):
            if line.path[-1] is branch:
                self.rtree.cursor_line = index
                break

    def on_tree_node_expanded(self, event: Tree.NodeExpanded[Payload]) -> None:
        data = event.node.data
        if isinstance(data, TypeNode) and not data.loaded and not data.loading:
            self.load(event.node, data)

    def adopt_context(self, context: Context) -> None:
        """F2/F3 switched profile or region - everything loaded is now stale."""
        self.context = context
        self.query_one(StatusBar).set_context(context)
        self.action_reload()

    # --- what F9 acts on --------------------------------------------------

    @property
    def type_name(self) -> str:
        """`ActionHost` uses this for its headings."""
        data = self._selected()
        if isinstance(data, ResourceNode | TypeNode):
            return data.type_name
        return "Resources"

    def _selected(self) -> Payload | None:
        node = self.rtree.cursor_node
        return None if node is None else node.data

    def selected_ref(self) -> act.ResourceRef | None:
        """Only a leaf is a resource; a type branch has nothing to act on."""
        data = self._selected()
        if not isinstance(data, ResourceNode):
            return None
        return act.ResourceRef.from_row(data.type_name, data.resource.row())

    def _after_action(self, reload: bool) -> None:
        if reload:
            self.action_reload_branch()
        else:
            self._title(self.type_name)

    # --- actions ----------------------------------------------------------

    def action_toggle(self) -> None:
        """enter: open or close the type under the cursor.

        A leaf is left alone, so enter on a resource stays free for a future
        details screen; F9 is what acts on one today.
        """
        node = self.rtree.cursor_node
        if node is not None and isinstance(node.data, TypeNode):
            node.toggle()

    def action_reload(self) -> None:
        """F5: fold everything and forget it - the next open refetches."""
        for branch in self.rtree.root.children:
            if isinstance(branch.data, TypeNode):
                self.reset_branch(branch, branch.data)
        self._overview()

    def action_reload_branch(self) -> None:
        """Refetch just the branch the cursor is in - after a delete, say."""
        node = self.rtree.cursor_node
        while node is not None and not isinstance(node.data, TypeNode):
            node = node.parent
        if node is None or not isinstance(node.data, TypeNode):
            return
        node.data.reset()
        self.load(node, node.data)

    def action_help(self) -> None:
        self.app.push_screen(TextDrop("F1  Help - resources", TREE_HELP, "f1"))
