"""The generic Cloud Control resources explorer, registered as a plugin."""

from __future__ import annotations

from typing import Any

from clitka.core.hookspecs import hookimpl
from clitka.services.resources.cli import app


@hookimpl
def clitka_service_name() -> str:
    return "resources"


@hookimpl
def clitka_cli_app() -> Any:
    return app
