"""EC2 as CLITKA's fifth pluggy plugin.

Like `logs`, `lambdafn` and `ecr` before it, this adds a CLI group, F9 actions and
a preview tab with a single line changed outside the package: one entry in
`plugins.BUILTIN_SERVICES`. Five for five.

It is also the first plugin whose F9 actions *mutate* - see `actions.py` for why
that is allowed here and why terminate is still not on the menu.
"""

from __future__ import annotations

from typing import Any

from clitka.core.hookspecs import hookimpl
from clitka.services.ec2.actions import ACTIONS, PREVIEWS
from clitka.services.ec2.cli import app


@hookimpl
def clitka_service_name() -> str:
    return "ec2"


@hookimpl
def clitka_cli_app() -> Any:
    return app


@hookimpl
def clitka_actions() -> list[Any]:
    """Details / start / stop / reboot, offered by F9 on an instance."""
    return list(ACTIONS)


@hookimpl
def clitka_previews() -> list[Any]:
    """The `Instance` tab in the preview pane."""
    return list(PREVIEWS)
