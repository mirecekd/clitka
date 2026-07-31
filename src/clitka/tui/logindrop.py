"""Sign in to IAM Identity Center without leaving the TUI.

The AWS Toolkit for VS Code starts the device flow for you the moment it notices
the login is gone; a red "Token has expired and refresh failed" in a tree branch
is a dead end. This panel is the terminal answer: it drops out from under the menu
bar, prints the verification URL and user code, opens the browser on `o`, and
polls in a thread worker until the confirmation lands.

Escape must be instant, so the poll never sleeps for longer than `SLICE` at a
time and checks the cancel flag between slices - `core.sso.login` takes the sleep
function as an argument precisely so this is possible without touching the flow.
"""

from __future__ import annotations

import time
import webbrowser

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from clitka.core import sso
from clitka.core.awsconfig import load_aws_config
from clitka.core.context import Context
from clitka.core.errors import ClitkaError
from clitka.core.ssotargets import SsoTarget, target_for_profile
from clitka.tui.dropdown import DropPanel

SLICE = 0.25  # how long a cancelled login may keep sleeping, in seconds
TITLE = "F4  Sign in (IAM Identity Center)"
HINT = "o = open browser    escape = cancel"


class Cancelled(ClitkaError):
    """Raised inside the worker's sleep to abandon a login the user gave up on."""


def target_for(context: Context) -> SsoTarget:
    """Where the current profile logs in. Raises ConfigError if it is not SSO."""
    profile = context.profile
    if not profile:
        raise ClitkaError("no profile selected - pick one with F2 first")
    return target_for_profile(load_aws_config(), profile)


class LoginDrop(DropPanel):
    """The device authorization flow as a drop-down panel.

    Dismisses with `True` once the token is cached, `None` if the user gave up or
    the login failed.
    """

    TOGGLE_KEY = "f4"

    DEFAULT_CSS = """
    LoginDrop > Vertical {
        min-width: 64;
    }
    LoginDrop VerticalScroll {
        height: auto;
        max-height: 100%;
        padding: 0 1;
    }
    """
    BINDINGS = [
        Binding("escape", "close", "Cancel", show=False),
        Binding("o", "open_browser", "Open browser", show=False),
    ]

    def __init__(self, context: Context, force: bool = False) -> None:
        super().__init__()
        self.context = context
        self.force = force
        self.url = ""
        self.lines: list[str] = ["Resolving the login target..."]
        self._give_up = False

    # --- layout -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(TITLE, classes="title")
            with VerticalScroll():
                yield Static(self.body(), id="login-text")
            yield Static(HINT, classes="hint")

    def body(self) -> str:
        return "\n".join(self.lines)

    def on_mount(self) -> None:
        super().on_mount()
        self.run_worker(self._login, thread=True, exclusive=False, group="sso-login")

    def _repaint(self) -> None:
        """Update the text - if the panel is still there.

        A dismissed `ModalScreen` still reports `is_mounted == True` while its
        children are gone, so the lookup has to be defensive.
        """
        try:
            self.query_one("#login-text", Static).update(self.body())
        except Exception:
            return

    def say(self, text: str) -> None:
        self.lines.append(text)
        self._repaint()

    # --- the flow ---------------------------------------------------------

    def _sleep(self, seconds: float) -> None:
        """Sleep in slices so escape does not have to wait out a poll interval."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._give_up:
                raise Cancelled("login cancelled")
            time.sleep(min(SLICE, max(deadline - time.monotonic(), 0.0)))

    def _report(self, message: str) -> None:
        if self._give_up:
            return
        for line in message.splitlines():
            stripped = line.strip()
            if stripped.startswith(("http://", "https://")):
                self.url = stripped
            self.app.call_from_thread(self.say, line)

    def _login(self) -> None:
        try:
            target = target_for(self.context)
            self.app.call_from_thread(self.say, f"{target.key}  [dim]{target.start_url}[/dim]")
            sso.login(
                target,
                report=self._report,
                force=self.force,
                sleep=self._sleep,
            )
        except Cancelled:
            return
        except Exception as exc:
            if not self._give_up:
                self.app.call_from_thread(self._failed, exc)
            return
        if not self._give_up:
            self.app.call_from_thread(self.dismiss, True)

    def _failed(self, exc: Exception) -> None:
        self.say(f"[red][ERROR] {exc}[/red]")

    # --- keys -------------------------------------------------------------

    def action_open_browser(self) -> None:
        if self.url:
            webbrowser.open(self.url)
            self.say("[dim]browser opened[/dim]")

    def action_close(self) -> None:
        self._give_up = True
        self.dismiss(None)

    def on_key(self, event) -> None:
        """`o` must reach the binding, so do not let DropPanel eat every key."""
        if event.key == self.TOGGLE_KEY:
            event.stop()
            event.prevent_default()
            self.action_close()


def _self_check() -> None:
    drop = LoginDrop(Context(profile="sw-sandbox"))
    assert drop.TOGGLE_KEY == "f4"
    drop.lines = ["one"]
    assert drop.body() == "one"

    # A URL in a report line is remembered so `o` has something to open, and the
    # cancel flag stops the reporting rather than crashing on a closed panel.
    drop._give_up = True
    drop._report("https://example.awsapps.com/start")
    assert drop.url == ""

    # The sleep slicing is what makes escape instant.
    fresh = LoginDrop(Context(profile="p"))
    fresh._give_up = True
    started = time.monotonic()
    try:
        fresh._sleep(10.0)
    except Cancelled:
        pass
    else:  # pragma: no cover
        raise AssertionError("a cancelled login kept sleeping")
    assert time.monotonic() - started < 1.0

    # No profile is a config problem, not a traceback.
    try:
        target_for(Context())
    except ClitkaError as exc:
        assert "no profile" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a missing profile should be refused")
    print("[OK] login drop self-check passed")


if __name__ == "__main__":
    _self_check()
