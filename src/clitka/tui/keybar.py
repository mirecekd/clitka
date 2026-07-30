"""The bottom function-key bar, in the spirit of Midnight Commander.

Textual's own `Footer` renders whatever bindings a screen declares, which makes
the bar move around as screens change. CLITKA wants the opposite: the same six
slots always in the same place, so the bar is rendered from a fixed list and
each screen only says which slots it actually enables.
"""

from __future__ import annotations

from textual.widgets import Static

# (key label, action label). Order is fixed - muscle memory is the point.
SLOTS: tuple[tuple[str, str], ...] = (
    ("F1", "Help"),
    ("F2", "Profile"),
    ("F3", "Region"),
    ("F5", "Refresh"),
    ("F9", "Actions"),
    ("F10", "Quit"),
)


ALL_KEYS = frozenset(key for key, _ in SLOTS)


def render_bar(enabled: frozenset[str] | None = None) -> str:
    """Render the bar; keys not in `enabled` are shown dimmed."""
    cells = []
    for key, label in SLOTS:
        if enabled is None or key in enabled:
            cells.append(f"[b]{key}[/b] {label}")
        else:
            cells.append(f"[dim]{key} {label}[/dim]")
    return "  ".join(cells)


class KeyBar(Static):
    """A fixed six-slot key legend docked at the bottom."""

    DEFAULT_CSS = """
    KeyBar {
        dock: bottom;
        height: 1;
        background: $panel;
        padding: 0 1;
    }
    """

    def __init__(self, enabled: frozenset[str] | None = None) -> None:
        super().__init__()
        self.enabled = enabled or ALL_KEYS

    def set_enabled(self, enabled: frozenset[str]) -> None:
        self.enabled = enabled
        if self.is_mounted:
            self.refresh()

    def line(self) -> str:
        return render_bar(self.enabled)

    def render(self) -> str:
        return self.line()


def _self_check() -> None:
    full = render_bar()
    for key, label in SLOTS:
        assert key in full and label in full, (key, full)
    partial = render_bar(frozenset({"F1", "F10"}))
    assert "[b]F1[/b]" in partial, partial
    assert "[dim]F9 Actions[/dim]" in partial, partial
    print("[OK] key bar self-check passed")


if __name__ == "__main__":
    _self_check()
