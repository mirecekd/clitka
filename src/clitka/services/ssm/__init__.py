"""Systems Manager as CLITKA's eighth pluggy plugin - and the last item of M4.

Like the seven before it, this adds a CLI group, F9 actions and preview tabs with
a single line changed outside the package: one entry in
`plugins.BUILTIN_SERVICES`. Eight for eight.

It is also the first plugin whose main design question was not about an API but
about a *screen*: a `SecureString` exists to be secret, and this app paints
resources into a terminal. See `actions.py` for why nothing here decrypts.
"""

from __future__ import annotations

from typing import Any

from clitka.core.hookspecs import hookimpl
from clitka.services.ssm.actions import ACTIONS, PREVIEWS
from clitka.services.ssm.cli import app


@hookimpl
def clitka_service_name() -> str:
    return "ssm"


@hookimpl
def clitka_cli_app() -> Any:
    return app


@hookimpl
def clitka_actions() -> list[Any]:
    """F9 on a parameter or a document. Nothing here decrypts or runs anything."""
    return list(ACTIONS)


@hookimpl
def clitka_previews() -> list[Any]:
    """The `Parameter` and `Document` tabs in the preview pane."""
    return list(PREVIEWS)
