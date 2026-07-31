"""`TreeSelection`: what is under the cursor, and what the preview pane shows.

Mixed into `restree.ResourceTree`, next to `BranchLoader` (fetching) and
`ActionHost` (F9). Split out when the split-pane preview pushed `restree.py` over
the 8 kB rule, and the seam turned out to be real: this file is the answer to
"what did the user pick", `restree.py` is the layout and the key bindings.

The owner's rule lives here: only enter, space or a click fills the preview -
never a cursor movement - so holding the down arrow costs no API calls.
"""

from __future__ import annotations

from textual.widgets import Tree

from clitka.core import actions as act
from clitka.tui.preview import PreviewPane
from clitka.tui.treemodel import ResourceNode, TypeNode


class TreeSelection:
    """Expects `self.rtree`, `self.query_one`, `self.focused` and `self._title`."""

    @property
    def preview(self) -> PreviewPane:
        """The 2/3 detail pane on the right."""
        return self.query_one(PreviewPane)  # type: ignore[attr-defined]

    # --- what is selected -------------------------------------------------

    @property
    def type_name(self) -> str:
        """`ActionHost` uses this for its headings."""
        data = self._selected()
        if isinstance(data, ResourceNode | TypeNode):
            return data.type_name
        return "Resources"

    def _selected(self) -> TypeNode | ResourceNode | None:
        node = self.rtree.cursor_node  # type: ignore[attr-defined]
        return None if node is None else node.data

    def selected_ref(self) -> act.ResourceRef | None:
        """Only a leaf is a resource; a type branch has nothing to act on."""
        data = self._selected()
        if not isinstance(data, ResourceNode):
            return None
        return act.ResourceRef.from_row(data.type_name, data.resource.row())

    def _after_action(self, reload: bool) -> None:
        if reload:
            self.action_reload_branch()  # type: ignore[attr-defined]
        else:
            self._title(self.type_name)  # type: ignore[attr-defined]

    # --- filling the preview ----------------------------------------------

    def action_toggle(self) -> None:
        """enter: open or close a type, or preview the resource under the cursor.

        This is the *only* keyboard path into the preview (the owner's call).
        """
        node = self.rtree.cursor_node  # type: ignore[attr-defined]
        if node is None:
            return
        if isinstance(node.data, TypeNode):
            node.toggle()
            return
        if isinstance(node.data, ResourceNode):
            self.preview.show(self.selected_ref())

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        """A mouse click also previews - but must not double-toggle a branch.

        `action_toggle` already handles enter and Textual posts NodeSelected for
        both, so only the leaf case is handled here.
        """
        data = event.node.data
        if isinstance(data, ResourceNode):
            self.preview.show(act.ResourceRef.from_row(data.type_name, data.resource.row()))

    def action_focus_preview(self) -> None:
        """tab: move between the tree and the preview, and back."""
        pane = self.preview
        focused = self.focused  # type: ignore[attr-defined]
        if focused is not None and pane in focused.ancestors_with_self:
            self.rtree.focus()  # type: ignore[attr-defined]
            return
        for child in pane.query("VerticalScroll"):
            child.focus()
            return
        pane.focus()


def _self_check() -> None:
    """The contract: the mixin needs a host, but its selection logic is pure."""
    from clitka.core import cloudcontrol as cc

    class Fake(TreeSelection):
        def __init__(self, data) -> None:
            self.data = data

        @property
        def rtree(self):  # a stand-in for the Tree widget
            payload = self.data

            class Node:
                data = payload

            class Stub:
                cursor_node = Node()

            return Stub()

    # A type branch is not a resource: F9 has nothing to act on.
    branch = Fake(TypeNode("AWS::S3::Bucket"))
    assert branch.type_name == "AWS::S3::Bucket"
    assert branch.selected_ref() is None

    leaf = Fake(ResourceNode("AWS::S3::Bucket", cc.Resource("AWS::S3::Bucket", "b1", {"A": "1"})))
    ref = leaf.selected_ref()
    assert ref is not None and ref.identifier == "b1"
    assert ref.row["A"] == "1"

    # Nothing selected at all must not raise.
    assert Fake(None).type_name == "Resources"
    assert Fake(None).selected_ref() is None
    print("[OK] tree selection self-check passed")


if __name__ == "__main__":
    _self_check()
