"""The generic Cloud Control resources explorer, registered as a plugin."""

from __future__ import annotations

from typing import Any

from clitka.core.hookspecs import hookimpl
from clitka.services.resources.actions import ACTIONS
from clitka.services.resources.cli import app


@hookimpl
def clitka_service_name() -> str:
    return "resources"


@hookimpl
def clitka_cli_app() -> Any:
    return app


@hookimpl
def clitka_actions() -> list[Any]:
    """The generic view / show-identifier / delete actions for the F9 menu."""
    return list(ACTIONS)
