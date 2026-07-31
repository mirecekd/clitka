"""CloudWatch Logs as CLITKA's second pluggy plugin.

Proof that the plugin seam is real: this package adds a CLI group, F9 actions and
a preview tab without a single change to `core` or `tui`.
"""

from __future__ import annotations

from typing import Any

from clitka.core.hookspecs import hookimpl
from clitka.services.logs.actions import ACTIONS, PREVIEWS
from clitka.services.logs.cli import app


@hookimpl
def clitka_service_name() -> str:
    return "logs"


@hookimpl
def clitka_cli_app() -> Any:
    return app


@hookimpl
def clitka_actions() -> list[Any]:
    """Events / streams / how-to-tail, offered on a log group in the F9 menu."""
    return list(ACTIONS)


@hookimpl
def clitka_previews() -> list[Any]:
    """The `Events` tab in the preview pane beside the tree."""
    return list(PREVIEWS)
