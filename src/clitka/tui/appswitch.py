"""`ContextSwitcher`: everything that changes *who* CLITKA is acting as.

F2 (profile), F3 (region) and F4 (sign in) all end in the same place - a new
`Context`, a repainted status bar and every screen told to re-list itself - so
they live together, mixed into `ClitkaApp`. Split out of `tui/app.py` to keep both
files under 8 kB; the seam is real: `app.py` is the shell, this is the identity.

The switches are in-memory only, by the owner's explicit call. `clitka ctx use`
remains the only thing that writes `~/.config/clitka/config.toml`.
"""

from __future__ import annotations

from clitka.core.awsconfig import load_aws_config
from clitka.core.context import Context
from clitka.tui.dropdown import TextDrop
from clitka.tui.dropmenu import DropMenu
from clitka.tui.logindrop import LoginDrop
from clitka.tui.status import StatusBar
from clitka.tui.switch import (
    PROFILE_HINT,
    PROFILE_TITLE,
    REGION_HINT,
    REGION_TITLE,
    profile_items,
    region_items,
)


class ContextSwitcher:
    """Mixed into `ClitkaApp`. Expects `context` and a mounted `StatusBar`."""

    context: Context

    # --- F2 / F3 ----------------------------------------------------------

    def action_switch_profile(self) -> None:
        """F2: drop the profile list out from under the menu bar."""
        self._drop(
            PROFILE_TITLE,
            profile_items(load_aws_config(), self.context.profile),
            "f2",
            PROFILE_HINT,
            self._profile_chosen,
        )

    def action_switch_region(self) -> None:
        """F3: drop the region list out from under the menu bar."""
        self._drop(
            REGION_TITLE,
            region_items(self._regions(), self.context.effective_region),
            "f3",
            REGION_HINT,
            self._region_chosen,
        )

    def _regions(self) -> list[str]:
        """Region names botocore knows about; an empty list if it cannot say."""
        try:
            return sorted(self.context.session.get_available_regions("ec2"))
        except Exception:
            pass
        # A broken profile must not take the panel down - offer what we have.
        # `effective_region` builds a session too, so it needs its own guard;
        # the self-check caught exactly that.
        try:
            here = self.context.effective_region
        except Exception:
            return []
        return [here] if here else []

    def _drop(self, title, items, key, hint, then) -> None:
        if not items:
            self.push_screen(TextDrop(title, "Nothing to choose from.", key))  # type: ignore[attr-defined]
            return
        self.push_screen(DropMenu(title, items, key, hint), then)  # type: ignore[attr-defined]

    def _profile_chosen(self, profile: object) -> None:
        """A new profile means a new Context - and a new identity to resolve."""
        if not isinstance(profile, str) or profile == self.context.profile:
            return
        # ponytail: the switch is in-memory only, by the owner's explicit call.
        # Ceiling: it is forgotten on exit; `clitka ctx use` is the way to persist.
        self.context = self.context.with_profile(profile)
        self._context_changed()

    def _region_chosen(self, region: object) -> None:
        if not isinstance(region, str) or region == self.context.region:
            return
        self.context = self.context.with_region(region)
        self._context_changed()

    # --- F4: sign in ------------------------------------------------------

    def action_login(self) -> None:
        """F4: run the SSO device flow right here, then refresh everything."""
        self.push_screen(LoginDrop(self.context), self._logged_in)  # type: ignore[attr-defined]

    def offer_login(self, why: str = "") -> None:
        """A screen hit an expired login - open the panel for it, once."""
        if any(isinstance(screen, LoginDrop) for screen in self.screen_stack):  # type: ignore[attr-defined]
            return
        drop = LoginDrop(self.context)
        if why:
            drop.lines.insert(0, f"[dim]{why}[/dim]")
        self.push_screen(drop, self._logged_in)  # type: ignore[attr-defined]

    def _logged_in(self, ok: object) -> None:
        if ok:
            # The old session still holds the credential resolver that failed.
            self.context = self.context.renewed()
            self._context_changed()

    # --- the one place a context change is announced ----------------------

    def _context_changed(self) -> None:
        """Repaint the status bar, re-resolve the identity, reload the screens."""
        bar = self.query_one(StatusBar)  # type: ignore[attr-defined]
        bar.set_context(self.context)
        bar.set_pending()
        self.refresh_identity()  # type: ignore[attr-defined]
        for screen in self.screen_stack:  # type: ignore[attr-defined]
            adopt = getattr(screen, "adopt_context", None)
            if adopt is not None:
                adopt(self.context)


def _self_check() -> None:
    """The contract: the mixin supplies exactly these actions and no state."""
    for name in ("action_switch_profile", "action_switch_region", "action_login"):
        assert callable(getattr(ContextSwitcher, name)), name
    assert callable(ContextSwitcher.offer_login)
    assert callable(ContextSwitcher._context_changed)

    # `_regions` must never raise, even on a profile that cannot build a session.
    class Fake(ContextSwitcher):
        pass

    fake = Fake()
    fake.context = Context(profile="no-such-profile-exists")
    assert fake._regions() == []
    print("[OK] context switcher self-check passed")


if __name__ == "__main__":
    _self_check()
