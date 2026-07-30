"""The bottom status bar: who am I, where am I, and is this read-only.


The identity comes from STS, which is a network call, so the bar renders
immediately with what the Context already knows and is refreshed by the app once
the account has been resolved on a worker thread.

ponytail: plain attributes plus an explicit `refresh()` instead of Textual
reactives - the widget has exactly one writer (the app). Also, `region` is a
`Widget` property, so a reactive of that name would shadow it. Ceiling: no
automatic re-render if someone mutates a field directly; upgrade path is
`reactive()` with non-clashing names.
"""

from __future__ import annotations

from textual.widgets import Static

from clitka.core.context import Context


def format_account(account: str) -> str:
    """1234-5678-9012 reads better than 123456789012 in a header."""
    if len(account) == 12 and account.isdigit():
        return f"{account[:4]}-{account[4:8]}-{account[8:]}"
    return account or "(unknown)"


class StatusBar(Static):
    """One line: CLITKA | profile | account | region | identity | read-only."""

    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;

        height: 1;
        background: $primary;
        color: $text;
        padding: 0 1;
    }
    """

    def __init__(self, context: Context | None = None) -> None:
        super().__init__()
        self.profile = "(default)"
        self.aws_region = "(unset)"
        self.account = "(resolving)"
        self.identity = ""
        self.read_only = False
        if context is not None:
            self.set_context(context)

    def set_context(self, context: Context) -> None:
        """Fill in what is known without making a network call."""
        self.profile = context.profile or "(default)"
        self.aws_region = context.region or "(unset)"
        self.read_only = context.read_only
        self._repaint()

    def set_region(self, region: str | None) -> None:
        self.aws_region = region or "(unset)"
        self._repaint()

    def set_identity(self, account: str, identity: str) -> None:
        self.account = format_account(account)
        self.identity = identity
        self._repaint()

    def set_pending(self) -> None:
        self.account = "(resolving)"
        self.identity = ""
        self._repaint()

    def _repaint(self) -> None:
        if self.is_mounted:
            self.refresh()

    def line(self) -> str:
        """The rendered text - separated out so it is testable without a screen."""
        parts = [
            "CLITKA",
            f"profile: {self.profile}",
            f"acct: {self.account}",
            f"region: {self.aws_region}",
        ]
        if self.identity:
            parts.append(f"as: {self.identity}")
        if self.read_only:
            parts.append("READ-ONLY")
        return " | ".join(parts)

    def render(self) -> str:
        return self.line()


def _self_check() -> None:
    assert format_account("123456789012") == "1234-5678-9012"
    assert format_account("") == "(unknown)"
    assert format_account("aws") == "aws"
    bar = StatusBar(Context(profile="p", region="eu-central-1", read_only=True))
    bar.set_identity("123456789012", "mirek")
    line = bar.line()
    assert "profile: p" in line, line
    assert "region: eu-central-1" in line, line
    assert "1234-5678-9012" in line, line
    assert "as: mirek" in line, line
    assert "READ-ONLY" in line, line
    print("[OK] status bar self-check passed")


if __name__ == "__main__":
    _self_check()
