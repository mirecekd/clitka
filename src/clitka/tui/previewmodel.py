"""Turning a resource's properties into the grouped "Overview" the pane shows.

No Textual import, so the grouping and the value formatting are unit-testable
without a screen - the same seam as `tablemodel.py` and `treemodel.py`.

The AWS console shows an EC2 instance as a handful of labelled panels rather than
one flat dump, and that is what `sections_for` approximates: the identity first,
then networking, then state, then everything else, with tags last because they are
usually the longest and the least interesting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

from clitka.core import cloudcontrol as cc
from clitka.core.output import jsonable

VALUE_WIDTH = 76  # a long scalar is trimmed to this before the pane wraps it

# Which group a property lands in, decided by substrings of its name. First hit
# wins, so this is *match* order, deliberately different from display order:
# "Identity" claims "id", which would otherwise swallow VpcId and SubnetId, so
# every more specific group is tried first. The self-check caught exactly that.
#
# ponytail: substring matching, not a per-type schema. Ceiling: a badly named
# property lands in "Other". Upgrade path: let a plugin publish a group map for
# its own types through the preview hook.
GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Networking", ("vpc", "subnet", "securitygroup", "ip", "dns", "port", "cidr", "network")),
    ("State", ("state", "status", "phase", "health", "enabled", "condition")),
    ("Timing", ("time", "date", "created", "modified", "updated", "expir", "ttl", "retention")),
    ("Size", ("size", "count", "capacity", "memory", "storage", "throughput", "bytes")),
    ("Security", ("role", "policy", "kms", "encrypt", "certificate", "auth", "public")),
    ("Identity", ("arn", "id", "name", "identifier", "key", "uri", "url", "endpoint")),
)
TAGS = "Tags"
OTHER = "Other"
# Display order: identity first (what am I looking at), tags last (longest).
ORDER = ("Identity", "State", "Networking", "Security", "Size", "Timing", OTHER, TAGS)


@dataclass(frozen=True)
class Section:
    """One labelled block of key/value rows in the Overview tab."""

    title: str
    rows: tuple[tuple[str, str], ...]

    def lines(self) -> list[str]:
        """Renderable lines: the title, then aligned `key  value` rows."""
        if not self.rows:
            return []
        width = max(len(key) for key, _ in self.rows)
        out = [f"[b]{self.title}[/b]"]
        out.extend(f"  {key.ljust(width)}   {value}" for key, value in self.rows)
        return out


def group_of(key: str) -> str:
    """Which section a property name belongs to."""
    lowered = key.lower()
    if lowered in ("tags", "tag", "taglist", "tagset"):
        return TAGS
    for name, needles in GROUPS:
        if any(needle in lowered for needle in needles):
            return name
    return OTHER


def format_value(value: Any) -> str:
    """A property value as the pane should show it.

    A nested structure becomes indented YAML rather than a JSON one-liner: it is
    what the user would have run `--output yaml` for anyway.
    """
    if value is None:
        return "[dim](none)[/dim]"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, dict | list | tuple):
        plain = jsonable(value)
        if not plain:
            return "[dim](empty)[/dim]"
        text = yaml.safe_dump(plain, sort_keys=False, default_flow_style=False).rstrip()
        return "\n" + "\n".join(f"    {line}" for line in text.splitlines())
    text = str(jsonable(value))
    if not text:
        return "[dim](empty)[/dim]"
    if len(text) > VALUE_WIDTH:
        return text[: VALUE_WIDTH - 3] + "..."
    return text


def sections_for(resource: cc.Resource) -> list[Section]:
    """Group a resource's properties into the panels the Overview tab shows."""
    buckets: dict[str, list[tuple[str, str]]] = {name: [] for name in ORDER}
    buckets["Identity"].append(("identifier", format_value(resource.identifier)))
    buckets["Identity"].append(("type", resource.type_name))
    for key, value in resource.properties.items():
        buckets[group_of(key)].append((key, format_value(value)))
    return [Section(name, tuple(buckets[name])) for name in ORDER if buckets[name]]


def overview(resource: cc.Resource) -> str:
    """The whole Overview tab as one markup string."""
    blocks = ["\n".join(section.lines()) for section in sections_for(resource)]
    return "\n\n".join(blocks)


def raw_yaml(resource: cc.Resource) -> str:
    """The resource as the API returned it - the "Raw" tab."""
    payload = {
        "TypeName": resource.type_name,
        "Identifier": resource.identifier,
        "Properties": jsonable(resource.properties),
    }
    return yaml.safe_dump(payload, sort_keys=False, default_flow_style=False).rstrip()


def _self_check() -> None:
    assert group_of("Arn") == "Identity"
    assert group_of("VpcId") == "Networking", group_of("VpcId")
    assert group_of("InstanceState") == "State"
    assert group_of("CreationDate") == "Timing"
    assert group_of("Tags") == TAGS
    assert group_of("Whatever") == OTHER
    # "id" must not steal a property that is really about networking.
    assert group_of("SubnetId") == "Networking"

    assert format_value(None) == "[dim](none)[/dim]"
    assert format_value(True) == "yes"
    assert format_value({}) == "[dim](empty)[/dim]"
    assert format_value("x" * 200).endswith("...")
    nested = format_value({"a": {"b": 1}})
    assert nested.startswith("\n    a:"), nested

    res = cc.Resource(
        "AWS::EC2::Instance",
        "i-123",
        {
            "InstanceType": "t3.micro",
            "VpcId": "vpc-1",
            "State": {"Name": "running"},
            "Tags": [{"Key": "env", "Value": "dev"}],
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

    # Section.lines aligns the keys, so the values line up in a column.
    lines = Section("T", (("a", "1"), ("long", "2"))).lines()
    assert lines[0] == "[b]T[/b]"
    assert lines[1].index("1") == lines[2].index("2"), lines

    assert "TypeName: AWS::EC2::Instance" in raw_yaml(res)
    # An empty resource must still produce something, not blow up.
    assert overview(cc.Resource("AWS::S3::Bucket", "", {}))
    print("[OK] preview model self-check passed")


if __name__ == "__main__":
    _self_check()
