"""ECR as CLITKA's fourth pluggy plugin.

Like `logs` and `lambdafn` before it, this adds a CLI group, F9 actions and a
preview tab with a single line changed outside the package: one entry in
`plugins.BUILTIN_SERVICES`. Four for four - the seam is not a coincidence.
"""

from __future__ import annotations

from typing import Any

from clitka.core.hookspecs import hookimpl
from clitka.services.ecr.actions import ACTIONS, PREVIEWS
from clitka.services.ecr.cli import app


@hookimpl
def clitka_service_name() -> str:
    return "ecr"


@hookimpl
def clitka_cli_app() -> Any:
    return app


@hookimpl
def clitka_actions() -> list[Any]:
    """Configuration / images / cleanup / docker login, offered by F9 on a repo."""
    return list(ACTIONS)


@hookimpl
def clitka_previews() -> list[Any]:
    """The `Images` tab in the preview pane."""
    return list(PREVIEWS)
