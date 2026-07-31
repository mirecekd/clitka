"""The landing screen: a tree of resource types beside a preview of what is picked.

The owner's model: 1/3 tree on the left, 2/3 detail on the right. The tree lists
only the *interesting* types; open one and its resources unfold underneath, loaded
on demand and appearing page by page; close it and they fold away but are kept.
Anything not on the list is one `:` away and is then added as a further branch.
Pressing enter on a resource previews it - moving the cursor never fetches.

Three mixins do the work: `BranchLoader` fetches, `TreeSelection` answers "what is
picked" and fills the preview, `ActionHost` is the whole F9 flow. This file is the
layout and the keyboard.
"""

from __future__ import annotations

from collections.abc import Sequence

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Static, Tree
from textual.widgets.tree import TreeNode

from clitka.core.context import Context
from clitka.tui.actionhost import ActionHost
from clitka.tui.dropdown import TextDrop
from clitka.tui.keybar import KeyBar
from clitka.tui.preview import PreviewPane
from clitka.tui.restypes import TREE_HELP, TREE_TYPES
from clitka.tui.status import StatusBar
from clitka.tui.treeload import NOT_LOADED, BranchLoader
from clitka.tui.treemodel import ResourceNode, TypeNode
from clitka.tui.treesel import TreeSelection
from clitka.tui.viewedit import ViewEditHost

Payload = TypeNode | ResourceNode


# `TreeSelection` comes FIRST: it supplies `selected_ref` / `_title` /
# `_after_action`, which `ActionHost` declares abstract. Put ActionHost ahead of
# it and the MRO picks the NotImplementedError stubs instead - F9 then dies on
# every keypress.
class ResourceTree(TreeSelection, ViewEditHost, ActionHost, BranchLoader, Screen[None]):
    """The tree of resource types. F9 acts on the resource under the cursor."""

    # Both panes are framed at ALL times and only the colour of the frame says
    # who holds the keyboard - yellow ($warning) is the active one (owner's call,
    # 2026-07-31). Drawing the border on focus only also moved the contents by a
    # cell every time the focus changed.
    DEFAULT_CSS = """
    ResourceTree #tree-title {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    ResourceTree Tree {
        height: 1fr;
        padding: 0 1;
        border: round $panel;
    }
    ResourceTree #split {
        height: 1fr;
    }
    ResourceTree #tree-side {
        width: 1fr;
        min-width: 32;
    }
    ResourceTree Tree:focus {
        border: round $warning;
    }
    """

    BINDINGS = [
        # Textual binds enter to "select", which only posts a NodeSelected message
        # and expands nothing. `space` does toggle, but enter is what a user
        # reaches for, so it is claimed here.
        Binding("enter", "toggle", "Open / preview", show=False, priority=True),
        Binding("tab", "focus_preview", "Preview / tree", show=False, priority=True),
        Binding("t", "tail", "Live tail", show=False),
        Binding("f1", "help", "Help", show=False),
        Binding("f3", "view", "View", show=False),
        Binding("f4", "edit", "Edit", show=False),
        # A Screen shadows the App's bindings, so the app-wide keys are forwarded.
        Binding("p,P", "app.switch_profile", "Profile", show=False),
        Binding("r,R", "app.switch_region", "Region", show=False),
        Binding("w,W", "app.switch_window", "Window", show=False),
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
