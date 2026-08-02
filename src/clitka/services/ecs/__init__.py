"""ECS as CLITKA's sixth pluggy plugin - and the first that Cloud Control cannot do.

Like `logs`, `lambdafn`, `ecr` and `ec2` before it, this adds a CLI group, F9
actions and preview tabs with a single line changed outside the package: one entry
in `plugins.BUILTIN_SERVICES`. Six for six.

What makes it different from those five: **`AWS::ECS::Task` does not exist as a
Cloud Control resource type**, so this is the first plugin that supplies a listing
nothing else in CLITKA could provide, rather than decorating one the generic
explorer already had. That is also what finally gives the `x` handoff
(`ecs execute-command`, written and tested since 2026-08-01) something to act on.
"""

from __future__ import annotations

from typing import Any

from clitka.core.hookspecs import hookimpl
from clitka.services.ecs.actions import ACTIONS, PREVIEWS
from clitka.services.ecs.cli import app
from clitka.services.ecs.listers import LISTERS


@hookimpl
def clitka_service_name() -> str:
    return "ecs"


@hookimpl
def clitka_cli_app() -> Any:
    return app


@hookimpl
def clitka_actions() -> list[Any]:
    """Cluster / services / tasks on a cluster, details and how-to-shell on a task."""
    return list(ACTIONS)


@hookimpl
def clitka_previews() -> list[Any]:
    """`Tasks` on a cluster or service, plus `Service` and `Task` detail tabs."""
    return list(PREVIEWS)


@hookimpl
def clitka_listers() -> list[Any]:
    """`Services` and `Tasks` sub-branches - what makes a task *clickable*.

    The owner's report (2026-08-01): none of the tasks could be reached by
    clicking, because the F9 action and the preview tab only ever printed them as
    text. These put real, selectable nodes in the tree, so `x` works on one.
    """
    return list(LISTERS)
