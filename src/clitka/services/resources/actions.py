"""The first real actions: view a resource, copy its identifier, delete it.

These work for every Cloud Control type, so they are the baseline the F9 menu
always has something in. Per-service plugins add their own on top.
"""

from __future__ import annotations

import json

import yaml

from clitka.core import cloudcontrol as cc
from clitka.core.actions import Action, ActionResult, ResourceRef
from clitka.core.context import Context
from clitka.core.output import jsonable


def view_yaml(ctx: Context, ref: ResourceRef) -> ActionResult:
    """Fetch the full resource and show it as YAML (get_resource, not the row)."""
    resource = cc.get_resource(ctx, ref.type_name, ref.identifier)
    document = jsonable({"identifier": resource.identifier, **resource.properties})
    return ActionResult(
        title=f"{ref.type_name} {ref.identifier}",
        body=yaml.safe_dump(document, sort_keys=False).rstrip(),
    )


def view_json(ctx: Context, ref: ResourceRef) -> ActionResult:
    """Same, as JSON - what you would paste into a template or a test."""
    resource = cc.get_resource(ctx, ref.type_name, ref.identifier)
    document = jsonable({"identifier": resource.identifier, **resource.properties})
    return ActionResult(
        title=f"{ref.type_name} {ref.identifier}",
        body=json.dumps(document, indent=2),
    )


def copy_identifier(_ctx: Context, ref: ResourceRef) -> ActionResult:
    """Show the identifier alone, ready to be selected with the mouse.

    ponytail: no clipboard integration. Ceiling: the user still has to select the
    text. Upgrade path: Textual's `App.copy_to_clipboard` (OSC 52) once the
    owner's terminal is known to support it.
    """
    return ActionResult(title="identifier", body=ref.identifier)


def delete(ctx: Context, ref: ResourceRef) -> ActionResult:
    """Delete the resource. The menu confirms before this is ever called."""
    result = cc.delete_resource(ctx, ref.type_name, ref.identifier)
    return ActionResult(
        title=f"delete {ref.type_name} {ref.identifier}",
        body=yaml.safe_dump(jsonable(result), sort_keys=False).rstrip(),
        reload=True,
    )


def _has_identifier(ref: ResourceRef) -> bool:
    return bool(ref.identifier)


ACTIONS: tuple[Action, ...] = (
    Action(
        id="resources.view_yaml",
        label="View as YAML",
        run=view_yaml,
        key="y",
        applies_to=_has_identifier,
    ),
    Action(
        id="resources.view_json",
        label="View as JSON",
        run=view_json,
        key="j",
        applies_to=_has_identifier,
    ),
    Action(
        id="resources.copy_identifier",
        label="Show identifier",
        run=copy_identifier,
        key="i",
        applies_to=_has_identifier,
    ),
    Action(
        id="resources.delete",
        label="Delete resource",
        run=delete,
        key="d",
        applies_to=_has_identifier,
        destructive=True,
    ),
)


def _self_check() -> None:
    ref = ResourceRef.from_row("AWS::S3::Bucket", {"identifier": "b1"})
    assert copy_identifier(Context(), ref).body == "b1"
    assert [a.destructive for a in ACTIONS] == [False, False, False, True]
    assert _has_identifier(ref) and not _has_identifier(ResourceRef("T", ""))
    print("[OK] resource actions self-check passed")


if __name__ == "__main__":
    _self_check()
