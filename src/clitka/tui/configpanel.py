"""`C`: the one panel that writes CLITKA's own config file.

Mixed into `ClitkaApp` beside `ContextSwitcher` and `WindowSwitcher`, so the key
works on every screen. The rows live in `tui/configmodel.py` (no Textual) and the
branch multi-select in `tui/branchpick.py`; this file is the panel and the writing.

Why a *panel* and not a form: everything here is one keystroke - toggle a flag, or
promote the session's profile / region / window to a default. There is nothing to
type except a resource type name, and `:` already does that well.

The standing rule is intact: `P`, `R` and `W` still change the running session
only. This panel is the deliberate act of making one of them stick, which is why
each row names the value it would save rather than saying "profile...".
"""

from __future__ import annotations

from dataclasses import replace

from clitka.core import clitkaconfig
from clitka.core import timerange as tr
from clitka.core.clitkaconfig import ClitkaConfig
from clitka.core.context import Context
from clitka.tui import configmodel as cm
from clitka.tui.branchpick import BranchPicker
from clitka.tui.dropdown import TextDrop
from clitka.tui.dropmenu import DropMenu
from clitka.tui.restypes import COMMON_TYPES, TREE_TYPES
from clitka.tui.switch import type_names

__all__ = ["BranchPicker", "ConfigPanel"]


class ConfigPanel:
    """Mixed into `ClitkaApp`. Needs `push_screen`, `screen_stack` and `context`."""

    context: Context

    def action_configure(self) -> None:
        """C: the settings panel. Everything it does is written to disk."""
        cfg = clitkaconfig.load()
        rows = cm.settings_items(cfg, self.context.profile, self._session_region())
        self.push_screen(  # type: ignore[attr-defined]
            DropMenu(cm.TITLE, rows, "c", cm.HINT, filterable=False),
            self._config_chosen,
        )

    def _session_region(self) -> str | None:
        """The region a call would use, or None if the profile cannot say."""
        try:
            return self.context.effective_region
        except Exception:
            # `effective_region` builds a boto3 session, so a broken profile
            # raises here - the `appswitch._regions` lesson.
            return self.context.region

    def _config_chosen(self, chosen: object) -> None:
        if not isinstance(chosen, str) or not chosen:
            return
        if chosen == cm.BRANCHES:
            self._pick_branches()
            return
        cfg = clitkaconfig.load()
        if chosen == cm.SAVE_PROFILE:
            saved = clitkaconfig.update(profile=self.context.profile)
        elif chosen == cm.SAVE_REGION:
            saved = clitkaconfig.update(region=self._session_region())
        elif chosen == cm.SAVE_WINDOW:
            saved = clitkaconfig.update(default_window=tr.current().label)
        elif chosen == cm.READ_ONLY:
            # A toggle cannot go through `update()`: it drops None, and it drops a
            # False with it - so switching a flag *off* would silently do nothing.
            saved = self._write(cfg, read_only=not cfg.read_only)
        elif chosen == cm.REMEMBER:
            saved = self._write(cfg, remember_last=not cfg.remember_last)
        elif chosen == cm.RESET:
            saved = self._write(cfg, tree_types=[])
            self._announce_types(list(TREE_TYPES))
        else:
            return
        self._saved(saved)

    def _write(self, cfg: ClitkaConfig, **changes: object) -> ClitkaConfig:
        """Save a config with these fields replaced - False and [] included."""
        updated = replace(cfg, **changes)  # type: ignore[arg-type]
        clitkaconfig.save(updated)
        return updated

    # --- the explorer's branches -----------------------------------------

    def _pick_branches(self) -> None:
        """`b`: choose the types the explorer opens with.

        The candidates are the `:` palette's, so they are whatever this account
        really exposes where `ListTypes` is allowed and `COMMON_TYPES` where it is
        not. Fetched on a worker for the same reason the palette does it:
        `ListTypes` takes seconds on a real account.
        """
        chosen = clitkaconfig.load().tree_types or list(TREE_TYPES)
        picker = BranchPicker(sorted(set(COMMON_TYPES) | set(chosen)), chosen)
        self.push_screen(picker, self._branches_chosen)  # type: ignore[attr-defined]
        self.run_worker(  # type: ignore[attr-defined]
            lambda: self._load_candidates(picker), thread=True, exclusive=False, group="types"
        )

    def _load_candidates(self, picker: BranchPicker) -> None:
        found = type_names(self.context, COMMON_TYPES)
        self.call_from_thread(self._fill_candidates, picker, sorted(found))  # type: ignore[attr-defined]

    def _fill_candidates(self, picker: BranchPicker, found: list[str]) -> None:
        # The panel may have closed while ListTypes was in flight - the
        # `CommandPalette` trap: a dismissed ModalScreen still reports
        # `is_mounted`, but its children are gone, so the query raises.
        try:
            picker.offer(found)
        except Exception:
            return

    def _branches_chosen(self, chosen: object) -> None:
        if not isinstance(chosen, list):
            return
        saved = self._write(clitkaconfig.load(), tree_types=list(chosen))
        self._announce_types(saved.tree_types or list(TREE_TYPES))
        self._saved(saved)

    def _announce_types(self, types: list[str]) -> None:
        """Tell any screen that shows branches to rebuild itself.

        The same shape as `adopt_context` / `adopt_window`: a screen that does not
        care needs no code at all.
        """
        for screen in self.screen_stack:  # type: ignore[attr-defined]
            adopt = getattr(screen, "adopt_types", None)
            if adopt is not None:
                adopt(types)

    def _saved(self, cfg: ClitkaConfig) -> None:
        """Say what was written, and where - a silent write is not a write."""
        self.push_screen(  # type: ignore[attr-defined]
            TextDrop(cm.TITLE, cm.summary(cfg, str(clitkaconfig.config_path())), "c")
        )


def _self_check() -> None:
    for name in ("action_configure", "_config_chosen", "_pick_branches"):
        assert callable(getattr(ConfigPanel, name)), name

    # `_write` must be able to say False and []. `clitkaconfig.update` drops both,
    # so a toggle written through it could only ever be switched on - which is the
    # bug this check exists for.
    class Fake(ConfigPanel):
        def __init__(self) -> None:
            self.written: list[ClitkaConfig] = []

        def _write(self, cfg, **changes):
            updated = replace(cfg, **changes)
            self.written.append(updated)
            return updated

        def _saved(self, cfg) -> None:
            pass

        def _announce_types(self, types) -> None:
            pass

    fake = Fake()
    fake.context = Context(profile="no-such-profile-exists")
    # A profile that cannot build a session must not take the panel down.
    assert fake._session_region() is None

    original = clitkaconfig.load
    clitkaconfig.load = lambda *a, **k: ClitkaConfig(read_only=True)  # type: ignore[assignment]
    try:
        fake._config_chosen(cm.READ_ONLY)
        assert fake.written[-1].read_only is False, "a toggle must switch off too"
        fake._config_chosen(cm.RESET)
        assert fake.written[-1].tree_types == [], "reset must clear the list"
        # Anything unrecognised is ignored rather than written.
        before = len(fake.written)
        fake._config_chosen("nonsense")
        fake._config_chosen(None)
        assert len(fake.written) == before
    finally:
        clitkaconfig.load = original  # type: ignore[assignment]

    print("[OK] config panel self-check passed")


if __name__ == "__main__":
    _self_check()
