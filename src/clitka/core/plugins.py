"""Plugin registry: discovers built-in and third-party service modules."""

from __future__ import annotations

import importlib
from functools import lru_cache
from typing import Any

import pluggy

from clitka.core.hookspecs import ENTRY_POINT_GROUP, HOOK_NAMESPACE, ClitkaSpec

# Built-in service modules, loaded in this order. Extend as milestones land.
BUILTIN_SERVICES: tuple[str, ...] = (
    "clitka.services.resources",
    "clitka.services.logs",
    "clitka.services.lambdafn",
    "clitka.services.ecr",
    "clitka.services.ec2",
    "clitka.services.ecs",
    "clitka.services.apigw",
    "clitka.services.ssm",
    "clitka.services.s3",
)


@lru_cache(maxsize=1)
def get_manager() -> pluggy.PluginManager:
    """Build the plugin manager once per process."""
    pm = pluggy.PluginManager(HOOK_NAMESPACE)
    pm.add_hookspecs(ClitkaSpec)
    for dotted in BUILTIN_SERVICES:
        try:
            module = importlib.import_module(dotted)
        except ImportError as exc:  # a broken built-in must not kill the CLI
            print(f"[ERROR] cannot load built-in service {dotted}: {exc}")
            continue
        pm.register(module, name=dotted)
    # ponytail: third-party plugins are trusted implicitly. Ceiling: no sandbox,
    # no version negotiation. Upgrade path: declare a plugin API version hook.
    pm.load_setuptools_entrypoints(ENTRY_POINT_GROUP)
    return pm


def service_apps() -> list[tuple[str, Any]]:
    """Return (name, typer_app) pairs for every registered service."""
    pm = get_manager()
    names = [n for n in pm.hook.clitka_service_name() if n]
    apps = [a for a in pm.hook.clitka_cli_app() if a is not None]
    if len(names) != len(apps):
        # Hook results come back per-plugin in registration order; a plugin that
        # answers one hook but not the other breaks the pairing, so ask per plugin.
        pairs: list[tuple[str, Any]] = []
        for _, plugin in pm.list_name_plugin():
            name = getattr(plugin, "clitka_service_name", None)
            app = getattr(plugin, "clitka_cli_app", None)
            if callable(name) and callable(app):
                pairs.append((name(), app()))
        return pairs
    return list(zip(names, apps, strict=True))


def resource_kinds() -> list[Any]:
    """Flatten resource kinds from all services."""
    return [kind for group in get_manager().hook.clitka_resource_kinds() for kind in group]


def actions() -> list[Any]:
    """Flatten actions from all services."""
    return [action for group in get_manager().hook.clitka_actions() for action in group]


def previews() -> list[Any]:
    """Flatten preview tabs from all services."""
    return [tab for group in get_manager().hook.clitka_previews() for tab in group]


def listers() -> list[Any]:
    """Flatten child listers (tree sub-branches) from all services."""
    return [one for group in get_manager().hook.clitka_listers() for one in group]


def viewers() -> list[Any]:
    """Flatten viewers (how F3 reads a type) from all services."""
    return [one for group in get_manager().hook.clitka_viewers() for one in group]


def _self_check() -> None:
    pm = get_manager()
    assert pm.project_name == HOOK_NAMESPACE
    assert service_apps() == [] or all(isinstance(n, str) for n, _ in service_apps())
    print(f"[OK] plugins self-check passed ({len(pm.get_plugins())} plugin(s))")


if __name__ == "__main__":
    _self_check()
