"""Turning a resource into the "Overview" and "Raw" text the pane shows.

No Textual import, so the assembly is unit-testable without a screen - the same
seam as `tablemodel.py` and `treemodel.py`. The taxonomy it assembles with (which
group a property belongs to, how a value reads) is `previewgroups.py`.

The names below are re-exported: `preview.py`, the tests and the docs all reach
for `pm.GROUPS` / `pm.format_value`, and there is no reason to make them care that
the constants moved next door for the 8 kB rule.
"""

from __future__ import annotations

from typing import Any

import yaml

from clitka.core import cloudcontrol as cc
from clitka.core import preview as pv
from clitka.core.actions import ResourceRef
from clitka.core.output import jsonable
from clitka.tui.previewgroups import (
    GROUPS,
    ORDER,
    OTHER,
    TAGS,
    VALUE_WIDTH,
    Section,
    format_value,
    group_of,
)

__all__ = [
    "GROUPS",
    "ORDER",
    "OTHER",
    "TAGS",
    "VALUE_WIDTH",
    "Section",
    "core_tabs",
    "format_value",
    "group_of",
    "overview",
    "raw_yaml",
    "resource_from",
    "sections_for",
    "slug",
]


def sections_for(resource: cc.Resource) -> list[Section]:
    """Group a resource's properties into the panels the Overview tab shows."""
    buckets: dict[str, list[tuple[str, str]]] = {name: [] for name in ORDER}
    # The name first, when there is one: on an EC2 instance the identifier alone
    # tells nobody which machine this is (owner's request).
    found = resource.name()
    if found:
        buckets["Identity"].append(("name", format_value(found)))
    buckets["Identity"].append(("identifier", format_value(resource.identifier)))
    buckets["Identity"].append(("type", resource.type_name))
    for key, value in resource.properties.items():
        buckets[group_of(key)].append((key, format_value(value)))
    return [Section(name, tuple(buckets[name])) for name in ORDER if buckets[name]]


def overview(resource: cc.Resource) -> str:
    """The whole Overview tab as one markup string."""
    blocks = ["\n".join(section.lines()) for section in sections_for(resource)]
    return "\n\n".join(blocks)


def slug(tab_id: str) -> str:
    """A tab id as something Textual will accept as a widget id.

    Textual only allows letters, digits, `_` and `-`, and a plugin namespaces its
    tabs with a dot (`logs.events`) - which raises `BadIdentifier` at *runtime*,
    the first time that tab is offered. Found on a real log group.
    """
    return "".join(char if char.isalnum() or char in "_-" else "-" for char in tab_id)


DERIVED = ("identifier", "name")
"""Row keys the table adds itself - they are not properties AWS returned."""


def resource_from(type_name: str, identifier: str, row: dict[str, Any]) -> cc.Resource:
    """Rebuild a `cc.Resource` from the row the tree already carries - no API call.

    `identifier` and the derived `name` column are dropped again, so the Raw tab
    shows only what the API actually said.
    """
    properties = {key: value for key, value in row.items() if key not in DERIVED}
    return cc.Resource(type_name, identifier, properties)


def raw_yaml(resource: cc.Resource) -> str:
    """The resource as the API returned it - the "Raw" tab."""
    payload = {
        "TypeName": resource.type_name,
        "Identifier": resource.identifier,
        "Properties": jsonable(resource.properties),
    }
    return yaml.safe_dump(payload, sort_keys=False, default_flow_style=False).rstrip()


def core_tabs() -> list[pv.PreviewTab]:
    """The two tabs every resource gets, built from the row the tree already has.

    Neither calls AWS (`lazy=False`), which is what lets the pane fill them on the
    UI thread - a plugin tab that does call AWS sets `lazy=True` instead.
    """
    return [
        pv.PreviewTab(
            pv.OVERVIEW,
            "Overview",
            lambda _ctx, ref: overview(_from_ref(ref)),
            lazy=False,
        ),
        pv.PreviewTab(
            pv.RAW,
            "Raw",
            lambda _ctx, ref: raw_yaml(_from_ref(ref)),
            lazy=False,
        ),
    ]


def _from_ref(ref: ResourceRef) -> cc.Resource:
    return resource_from(ref.type_name, ref.identifier, ref.row)


def _self_check() -> None:
    # The taxonomy has its own self-check; this one is about the assembly.
    res = cc.Resource(
        "AWS::EC2::Instance",
        "i-123",
        {
            "InstanceType": "t3.micro",
            "VpcId": "vpc-1",
            "State": {"Name": "running"},
            "Tags": [{"Key": "Name", "Value": "web-01"}],
        },
    )
    names = [section.title for section in sections_for(res)]
    assert names[0] == "Identity", names
    assert names[-1] == TAGS, names
    assert "Networking" in names and "State" in names

    text = overview(res)
    assert "[b]Identity[/b]" in text
    assert "i-123" in text
    assert "t3.micro" in text
    # The Name tag is what a human recognises the instance by, so it leads.
    assert text.index("web-01") < text.index("t3.micro"), text

    # Textual refuses a dot in a widget id, and a plugin namespaces its tabs.
    assert slug("logs.events") == "logs-events"
    assert slug("overview") == "overview"
    assert all(char.isalnum() or char in "_-" for char in slug("a b.c:d"))

    rebuilt = resource_from("AWS::S3::Bucket", "b1", {"identifier": "b1", "Arn": "arn:x"})
    assert rebuilt.identifier == "b1"
    assert rebuilt.properties == {"Arn": "arn:x"}, "identifier must not be a property"
    # `name` is derived by the table too - the Raw tab must not show it.
    derived = resource_from("AWS::EC2::Instance", "i-1", {"identifier": "i-1", "name": "web-01"})
    assert derived.properties == {}

    assert "TypeName: AWS::EC2::Instance" in raw_yaml(res)

    # An empty resource must still produce something, not blow up.
    assert overview(cc.Resource("AWS::S3::Bucket", "", {}))
    assert GROUPS and VALUE_WIDTH > 0 and OTHER in ORDER  # the re-exports are live
    print("[OK] preview model self-check passed")


if __name__ == "__main__":
    _self_check()
