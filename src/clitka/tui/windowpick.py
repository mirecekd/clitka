"""`w`: how far back the log preview looks - the time window picker.

The `Events` preview tab used to be hard-wired to the last 60 minutes. `w` drops
the same kind of panel P and R use out from under the menu bar, with the presets
from `core/timerange.py` on single keys and `c` for a typed duration.

Mixed into `ClitkaApp` beside `ContextSwitcher`, so the key works on every screen.
A screen that shows anything time-dependent picks the change up by growing an
`adopt_window()` method - `ResourceTree` clears its preview cache and rebuilds the
tab that is showing.
"""

from __future__ import annotations

from clitka.core import timerange as tr
from clitka.tui.dropdown import MenuItem, TextDrop
from clitka.tui.dropmenu import DropMenu
from clitka.tui.picker import CommandPalette

TITLE = "W  Time window - this session only"
HINT = "how far back the log preview and F9 look; `clitka logs search --since` for the CLI"
CUSTOM = "custom"
CUSTOM_KEY = "c"
CUSTOM_PROMPT = "duration (90m, 2h, 3d, 2w, 1mo, 1y)"


def window_items(current: tr.TimeRange | None = None) -> list[MenuItem]:
    """One row per preset, the active one marked, plus `c  custom...`."""
    active = current or tr.current()
    items = [
        MenuItem(
            label=preset.label,
            value=preset.label,
            key=preset.key,
            detail=tr.human(preset.minutes),
            current=preset.minutes == active.minutes,
        )
        for preset in tr.PRESETS
    ]
    items.append(
        MenuItem(
            label="custom...",
            value=CUSTOM,
            key=CUSTOM_KEY,
            detail="type a duration",
            # Marked when the window in force is not one of the presets.
            current=not any(item.current for item in items),
        )
    )
    return items


def resolve(chosen: object) -> tr.TimeRange | None:
    """Turn what the menu or the palette dismissed with into a window.

    None for "cancelled", and None for anything unparseable - a typo must not
    silently widen the window to something expensive.
    """
    if not isinstance(chosen, str) or not chosen or chosen == CUSTOM:
        return None
    try:
        return tr.parse(chosen)
    except ValueError:
        return None


class WindowSwitcher:
    """Mixed into `ClitkaApp`. Needs `push_screen` and `screen_stack`."""

    def action_switch_window(self) -> None:
        """W: drop the time window list out from under the menu bar."""
        # `filterable=False`: 13 rows is over the filter threshold, but every one
        # of them is on a single key and a focused filter box would eat them.
        self.push_screen(  # type: ignore[attr-defined]
            DropMenu(TITLE, window_items(), "w", HINT, filterable=False),
            self._window_chosen,
        )

    def _window_chosen(self, chosen: object) -> None:
        if chosen == CUSTOM:
            self._ask_window()
            return
        window = resolve(chosen)
        if window is not None:
            self.apply_window(window)

    def _ask_window(self) -> None:
        """`c`: type a duration. The presets double as the palette's candidates."""
        self.push_screen(  # type: ignore[attr-defined]
            CommandPalette([preset.label for preset in tr.PRESETS], CUSTOM_PROMPT),
            self._window_typed,
        )

    def _window_typed(self, typed: object) -> None:
        window = resolve(typed)
        if window is None:
            if isinstance(typed, str) and typed:
                self.push_screen(  # type: ignore[attr-defined]
                    TextDrop(TITLE, f"[red]{_why(typed)}[/red]", "w")
                )
            return
        self.apply_window(window)

    def apply_window(self, window: tr.TimeRange) -> None:
        """Select the window and tell every screen that cares."""
        tr.select(window)
        for screen in self.screen_stack:  # type: ignore[attr-defined]
            adopt = getattr(screen, "adopt_window", None)
            if adopt is not None:
                adopt(window)


def _why(typed: str) -> str:
    """The parser's own complaint, which is already written for a human."""
    try:
        tr.parse(typed)
    except ValueError as exc:
        return str(exc)
    return ""


def _self_check() -> None:
    tr.reset()
    items = window_items()
    assert [item.value for item in items][:2] == ["5m", "15m"]
    assert items[-1].value == CUSTOM
    # The default is 1h, so that row is the marked one and `custom` is not.
    marked = [item.value for item in items if item.current]
    assert marked == ["1h"], marked

    # A window nobody offers marks `custom...` instead.
    odd = window_items(tr.parse("90m"))
    assert [item.value for item in odd if item.current] == [CUSTOM]

    keys = [item.key for item in items]
    assert len(set(keys)) == len(keys), keys

    assert resolve("3h").minutes == 180.0  # type: ignore[union-attr]
    assert resolve("90m").minutes == 90.0  # type: ignore[union-attr]
    assert resolve(None) is None
    assert resolve("") is None
    assert resolve(CUSTOM) is None
    assert resolve("tomorrow") is None
    assert "duration" in _why("tomorrow")

    for name in ("action_switch_window", "apply_window"):
        assert callable(getattr(WindowSwitcher, name)), name
    print("[OK] window picker self-check passed")


if __name__ == "__main__":
    _self_check()
