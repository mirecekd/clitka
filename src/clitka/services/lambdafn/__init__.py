"""Lambda as CLITKA's third pluggy plugin.

The package is `lambdafn` because `lambda` is a Python keyword, but the CLI group
it publishes is `lambda` - `clitka lambda list` - because that is what a user
types. The service name in the hook is what decides that, not the module name.

Like the `logs` plugin, this adds a CLI group, F9 actions and preview tabs with a
single line changed outside the package: one entry in `plugins.BUILTIN_SERVICES`.
"""

from __future__ import annotations

from typing import Any

from clitka.core.hookspecs import hookimpl
from clitka.services.lambdafn.actions import ACTIONS, PREVIEWS
from clitka.services.lambdafn.cli import app


@hookimpl
def clitka_service_name() -> str:
    return "lambda"


@hookimpl
def clitka_cli_app() -> Any:
    return app


@hookimpl
def clitka_actions() -> list[Any]:
    """Configuration / environment / how-to-invoke, offered on a function by F9."""
    return list(ACTIONS)


@hookimpl
def clitka_previews() -> list[Any]:
    """The `Function` and `Recent logs` tabs in the preview pane."""
    return list(PREVIEWS)
