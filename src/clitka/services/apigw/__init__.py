"""API Gateway as CLITKA's seventh pluggy plugin.

Like the six before it, this adds a CLI group, F9 actions and preview tabs with a
single line changed outside the package: one entry in `plugins.BUILTIN_SERVICES`.
Seven for seven.

What is new here: it is the first plugin that talks to **two AWS services at once**
(`apigateway` and `apigatewayv2`, one console page and nothing else in common), and
the first whose real work is a plain **HTTP request** rather than an AWS API call -
because `aws apigateway test-invoke-method` bypasses the entire edge.
"""

from __future__ import annotations

from typing import Any

from clitka.core.hookspecs import hookimpl
from clitka.services.apigw.actions import ACTIONS, PREVIEWS
from clitka.services.apigw.cli import app


@hookimpl
def clitka_service_name() -> str:
    return "apigw"


@hookimpl
def clitka_cli_app() -> Any:
    return app


@hookimpl
def clitka_actions() -> list[Any]:
    """Routes / stages / how-to-invoke, offered by F9 on either kind of API."""
    return list(ACTIONS)


@hookimpl
def clitka_previews() -> list[Any]:
    """The `Routes` and `Stages` tabs in the preview pane."""
    return list(PREVIEWS)
