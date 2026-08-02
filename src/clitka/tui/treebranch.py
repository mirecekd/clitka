"""`BranchKeeper`: which type branches the tree has, and how they are added.

Split out of `restree.py` for the 8 kB rule when `adopt_types` landed, and the
seam is worth having on its own: this mixin answers *which branches exist*, while
`restree.py` is layout plus the keyboard and `treeload.BranchLoader` is what fills
one. Three callers, three reasons a branch appears:

- `on_mount` - the configured list (or the built-in one)
- `:` - one more type, for this session only (`add_type`)
- `C` - a new list, saved to disk (`adopt_types`)

The screen supplies `rtree`, `types`, `placeholder()`, `preview` and `_overview()`.
"""

from __future__ import annotations

from collections.abc import Sequence

from textual.widgets.tree import TreeNode

from clitka.tui.treeload import NOT_LOADED
from clitka.tui.treemodel import TypeNode


class BranchKeeper:
    """Mixed into `ResourceTree`, before the loaders it leans on."""

    types: list[str]

    def _add_type(self, type_name: str) -> TreeNode:
        """Add one folded branch, with the placeholder a branch needs to exist.

        A childless node is a *leaf* in Textual and can never be expanded, so the
        placeholder is not decoration - without it the branch could not open.
        """
        node = TypeNode(type_name)
        branch = self.rtree.root.add(node.label(), data=node, expand=False)  # type: ignore[attr-defined]
        self.placeholder(branch, NOT_LOADED)  # type: ignore[attr-defined]
        return branch

    def add_type(self, type_name: str) -> None:
        """`:` picked a type - add it, or jump to it if it is already a branch.

        This is session-only and deliberately does not save: `C` is the screen
        that decides what the tree *is*.
        """
        for branch in self.rtree.root.children:  # type: ignore[attr-defined]
            data = branch.data
            if isinstance(data, TypeNode) and data.type_name == type_name:
                self._reveal(branch)
                return
        self.types.append(type_name)
        self._reveal(self._add_type(type_name))

    def adopt_types(self, types: Sequence[str]) -> None:
        """The `C` panel changed which branches belong here - rebuild them.

        Everything loaded is thrown away, exactly as F5 does: a branch the user
        just removed must not linger with its resources still in it, and one just
        added has nothing to keep. Types added with `:` during this session are
        deliberately *not* preserved - `C` is a statement about what the tree is.
        """
        self.types = list(types)
        self.rtree.root.remove_children()  # type: ignore[attr-defined]
        for type_name in self.types:
            self._add_type(type_name)
        if self.rtree.root.children:  # type: ignore[attr-defined]
            self.rtree.cursor_line = 0  # type: ignore[attr-defined]
        self.preview.show(None)  # type: ignore[attr-defined]
        self._overview()  # type: ignore[attr-defined]

    def _reveal(self, branch: TreeNode) -> None:
        """Open a branch and put the cursor on it, without toggling it shut.

        `Tree.select_node()` would *toggle* - the trap that once made the tree open
        with its first branch already unfolded. Move `cursor_line` instead.
        """
        branch.expand()
        self.rtree.scroll_to_node(branch)  # type: ignore[attr-defined]
        for index, line in enumerate(self.rtree._tree_lines):  # type: ignore[attr-defined]
            if line.path[-1] is branch:
                self.rtree.cursor_line = index  # type: ignore[attr-defined]
                break


def _self_check() -> None:
    for name in ("_add_type", "add_type", "adopt_types", "_reveal"):
        assert callable(getattr(BranchKeeper, name)), name
    # `adopt_types` is the third of the `adopt_*` family; the other two live on
    # `TreeSelection`. Keeping the name in step is what lets `configpanel`
    # announce a change without knowing which screen is listening.
    assert BranchKeeper.adopt_types.__name__ == "adopt_types"
    print("[OK] branch keeper self-check passed")


if __name__ == "__main__":
    _self_check()
