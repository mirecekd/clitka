"""Hook specifications every CLITKA service module implements.

A service module never imports the CLI or the TUI; it only answers these hooks.
That is what keeps `core` free of per-service knowledge.
"""

from __future__ import annotations

from typing import Any

import pluggy

HOOK_NAMESPACE = "clitka"
ENTRY_POINT_GROUP = "clitka.services"

hookspec = pluggy.HookspecMarker(HOOK_NAMESPACE)
hookimpl = pluggy.HookimplMarker(HOOK_NAMESPACE)


class ClitkaSpec:
    """The contract between core and a service module."""

    @hookspec
    def clitka_service_name(self) -> str:
        """Return the CLI subcommand name, e.g. `logs`, `s3`, `dynamodb`."""

    @hookspec
    def clitka_cli_app(self) -> Any:
        """Return a `typer.Typer` instance to mount under the service name."""

    @hookspec
    def clitka_resource_kinds(self) -> list[Any]:
        """Return the resource kinds this service exposes in the TUI explorer."""

    @hookspec
    def clitka_actions(self) -> list[Any]:
        """Return Action objects offered in the F9 context menu."""
