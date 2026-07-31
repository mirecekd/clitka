"""The top function-key menu bar, in the spirit of Turbo Vision.

Textual's own `Footer` renders whatever bindings a screen declares, which makes
the bar move around as screens change. CLITKA wants the opposite: the same
slots always in the same place, so the bar is rendered from a fixed list and
each screen only says which slots it actually enables.

It is docked at the *top* so the drop-down panels (F1/F2/F3/F4) can slide out from

directly underneath the slot that was pressed. The status bar sits at the bottom:
the menu on top is "what I can do", the status below is "where I am".
"""

from __future__ import annotations

from textual.widgets import Static

# (key label, action label). Order is fixed - muscle memory is the point.
SLOTS: tuple[tuple[str, str], ...] = (
    ("F1", "Help"),
    ("F2", "Profile"),
    ("F3", "Region"),
    ("F4", "Login"),
    ("F5", "Refresh"),
    ("F9", "Actions"),
    ("F10", "Quit"),
)


ALL_KEYS = frozenset(key for key, _ in SLOTS)


def render_bar(enabled: frozenset[str] | None = None, open_key: str | None = None) -> str:
    """Render the bar; keys not in `enabled` are dimmed, `open_key` is reversed.

    `open_key` marks the slot whose drop-down panel is currently showing, which
    is what turns a passive legend into a real menu bar. It is matched
    case-insensitively, because Textual reports key names as "f1" while the slots
    are labelled "F1".
    """
    wanted = (open_key or "").upper()
    cells = []
    for key, label in SLOTS:
        if wanted and key == wanted:
            cells.append(f"[reverse]{key} {label}[/reverse]")
        elif enabled is None or key in enabled:
            cells.append(f"[b]{key}[/b] {label}")
        else:
            cells.append(f"[dim]{key} {label}[/dim]")
    return "  ".join(cells)


class KeyBar(Static):
    """A fixed six-slot key legend docked at the top."""

    DEFAULT_CSS = """
    KeyBar {
        dock: top;
        height: 1;
        background: $panel;
        padding: 0 1;
    }
    """

    def __init__(self, enabled: frozenset[str] | None = None) -> None:
        super().__init__()
        self.enabled = enabled or ALL_KEYS
        self.open_key: str | None = None

    def set_enabled(self, enabled: frozenset[str]) -> None:
        self.enabled = enabled
        self._repaint()

    def set_open(self, open_key: str | None) -> None:
        """Highlight the slot whose panel is open, or clear the highlight."""
        self.open_key = open_key
        self._repaint()

    def _repaint(self) -> None:
        if self.is_mounted:
            self.refresh()

    def line(self) -> str:
        return render_bar(self.enabled, self.open_key)

    def render(self) -> str:
        return self.line()


def _self_check() -> None:
    full = render_bar()
    for key, label in SLOTS:
        assert key in full and label in full, (key, full)
    partial = render_bar(frozenset({"F1", "F10"}))
    assert "[b]F1[/b]" in partial, partial
    assert "[dim]F9 Actions[/dim]" in partial, partial
    opened = render_bar(open_key="F2")
    assert "[reverse]F2 Profile[/reverse]" in opened, opened
    assert "[b]F1[/b]" in opened, opened
    # Textual reports "f1", the slots are labelled "F1".
    assert "[reverse]F1 Help[/reverse]" in render_bar(open_key="f1")

    print("[OK] key bar self-check passed")


if __name__ == "__main__":
    _self_check()
