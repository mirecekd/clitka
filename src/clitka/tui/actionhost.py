"""`ActionHost`: the F9 plumbing any resource screen can mix in.

Offer the registered actions for the selected resource, confirm the destructive
ones, run the chosen one off the UI thread, show the result. A screen only has to
provide `selected_ref()`, `_title()` and a `context` - so the second and third
service screen get F9 without copying any of this.
"""

from __future__ import annotations

from clitka.core import actions as act
from clitka.core.context import Context
from clitka.tui.actionmenu import ActionMenu, ConfirmModal
from clitka.tui.resultview import ResultScreen


class ActionHost:
    """Mixed into a `Screen`. Expects `context`, `selected_ref()` and `_title()`."""

    context: Context
    type_name: str

    def selected_ref(self) -> act.ResourceRef | None:  # pragma: no cover - overridden
        raise NotImplementedError

    def _title(self, text: str) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def _after_action(self, reload: bool) -> None:  # pragma: no cover - overridden
        """Called once an action has finished; `reload` is what the action asked for."""
        raise NotImplementedError

    def action_actions(self) -> None:
        """F9: offer whatever the plugins say applies to the selected row."""
        ref = self.selected_ref()
        if ref is None:
            self._title(f"{self.type_name}\nNothing selected - no actions to offer")
            return
        offered = act.available(act.registered(), ref)
        self.app.push_screen(  # type: ignore[attr-defined]
            ActionMenu(offered, f"{ref.type_name} {ref.identifier}"), self._chosen
        )

    def _chosen(self, action: act.Action | None) -> None:
        ref = self.selected_ref()
        if action is None or ref is None:
            return
        if not action.destructive:
            self._start(action, ref)
            return
        detail = (
            f"profile: {self.context.profile or '(default)'}  "
            f"region: {self.context.effective_region}"
        )
        self.app.push_screen(  # type: ignore[attr-defined]
            ConfirmModal(f"{action.label}: {ref.type_name} '{ref.identifier}'?", detail),
            lambda ok: self._start(action, ref) if ok else None,
        )

    def _start(self, action: act.Action, ref: act.ResourceRef) -> None:
        """Run the action off the UI thread - any of them may call AWS.

        The worker is deliberately NOT exclusive: an exclusive one would cancel
        the screen's own listing worker.
        """
        self._title(f"{self.type_name}\n{action.label} - running...")
        self.run_worker(  # type: ignore[attr-defined]
            lambda: self._run(action, ref), thread=True, exclusive=False, group="action"
        )

    def _run(self, action: act.Action, ref: act.ResourceRef) -> None:
        try:
            result = action.run(self.context, ref)
        except Exception as exc:
            self.app.call_from_thread(self._action_failed, action, exc)  # type: ignore[attr-defined]
            return
        self.app.call_from_thread(self._action_done, result)  # type: ignore[attr-defined]

    def _action_done(self, result: act.ActionResult) -> None:
        self.app.push_screen(ResultScreen(self.context, result))  # type: ignore[attr-defined]
        self._after_action(result.reload)

    def _action_failed(self, action: act.Action, exc: Exception) -> None:
        self._title(f"{self.type_name}\n[ERROR] {action.label}: {exc}")


def _self_check() -> None:
    """The contract is "the screen overrides these three" - check it is abstract."""
    host = ActionHost()

    for name, args in (("selected_ref", ()), ("_title", ("x",)), ("_after_action", (True,))):
        try:
            getattr(host, name)(*args)
        except NotImplementedError:
            continue
        raise AssertionError(f"{name} should be abstract")
    print("[OK] action host self-check passed")


if __name__ == "__main__":
    _self_check()
