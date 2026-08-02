"""The resource tree's keyboard map and its CSS - constants, no logic.

Split out of `restree.py` for the 8 kB rule when the sub-branch dispatch landed.
The seam is thin but real: everything here is *declaration*, and `restree.py` is
now only layout plus the handlers.

Two things not to "tidy up":

- **`enter` is claimed by us on purpose.** Textual binds it to `select_cursor`,
  which only posts `NodeSelected` and expands nothing.
- **A `Screen`'s BINDINGS shadow the App's**, so every app-wide letter key has to
  be re-declared here as `app.<action>` or it stops working inside the screen.
"""

from __future__ import annotations

from textual.binding import Binding

# Both panes are framed at ALL times; only the frame's colour says who holds the
# keyboard - yellow ($warning) is active (owner's call, 2026-07-31). Bordering on
# focus only made the contents jump by a cell on every move.
TREE_CSS = """
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

TREE_BINDINGS = [
    Binding("enter", "toggle", "Open / preview", show=False, priority=True),
    Binding("tab", "focus_preview", "Preview / tree", show=False, priority=True),
    Binding("t", "tail", "Live tail", show=False),
    Binding("f1", "help", "Help", show=False),
    Binding("f3", "view", "View", show=False),
    Binding("f4", "edit", "Edit", show=False),
    Binding("x", "connect", "Shell", show=False),
    # Forwarded to the App, which a Screen would otherwise shadow.
    Binding("p,P", "app.switch_profile", "Profile", show=False),
    Binding("r,R", "app.switch_region", "Region", show=False),
    Binding("w,W", "app.switch_window", "Window", show=False),
    Binding("f5", "reload", "Refresh", show=False),
    Binding("f9", "actions", "Actions", show=False),
    Binding("f10", "quit", "Quit", show=False),
]


def _self_check() -> None:
    keys = [binding.key for binding in TREE_BINDINGS]
    assert len(set(keys)) == len(keys), keys
    # `enter` must be ours, or nothing in the tree ever opens.
    assert "enter" in keys
    # The `x` handoff has no menu-bar slot, so losing this binding is silent.
    assert "x" in keys
    # Every app-wide letter key has to be forwarded, or a Screen swallows it.
    forwarded = {b.key: b.action for b in TREE_BINDINGS if b.action.startswith("app.")}
    assert set(forwarded) == {"p,P", "r,R", "w,W"}, forwarded
    assert "ResourceTree Tree:focus" in TREE_CSS, "the focus outline is the only cue"
    print("[OK] tree keys self-check passed")


if __name__ == "__main__":
    _self_check()
