"""What hangs in the resource tree - the node payloads and their labels.

No Textual import, so the labelling and the "is this thing loaded yet" logic are
testable without a screen. The screen itself is `tui/restree.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from clitka.core import cloudcontrol as cc


@dataclass
class TypeNode:
    """A resource *type* - a branch that loads its children when expanded."""

    type_name: str
    loaded: bool = False
    loading: bool = False
    count: int = 0
    error: str = ""
    capped: bool = False
    resources: list[cc.Resource] = field(default_factory=list)

    def label(self) -> str:
        """`AWS::S3::Bucket (98)`, or what went wrong instead."""
        if self.error:
            return f"{self.type_name}   [red][ERROR] {self.error}[/red]"
        if self.loading:
            return f"{self.type_name}   [dim]loading {self.count}...[/dim]"
        if not self.loaded:
            return self.type_name
        if self.count == 0:
            return f"{self.type_name}   [dim](none)[/dim]"
        more = "+" if self.capped else ""
        return f"{self.type_name}   [dim]({self.count}{more})[/dim]"

    def reset(self) -> None:
        """Forget everything - used by F5 on the branch."""
        self.loaded = False
        self.loading = False
        self.count = 0
        self.error = ""
        self.capped = False
        self.resources = []


@dataclass(frozen=True)
class ResourceNode:
    """One actual resource - a leaf. F9 acts on this."""

    type_name: str
    resource: cc.Resource

    def label(self) -> str:
        """`name (identifier)  detail` - the name leads, because it is what a
        human recognises the resource by. An EC2 instance is the reason: `i-0abc...`
        says nothing, its `Name` tag says everything (owner's request).
        """
        identifier = self.resource.identifier or "(no identifier)"
        name = self.resource.name()
        head = f"[b]{name}[/b]   [dim]{identifier}[/dim]" if name else identifier
        detail = summarise(self.resource)
        return f"{head}   [dim]{detail}[/dim]" if detail else head


def summarise(resource: cc.Resource, limit: int = 2) -> str:
    """A couple of the resource's own properties, for the leaf label.

    ponytail: the first few keys that are not just the identifier (or the name we
    already show) again, in the order AWS returned them. Ceiling: not necessarily
    the most interesting ones. Upgrade path: a per-type list of preferred
    properties.
    """
    name = resource.name()
    parts: list[str] = []
    for key, value in resource.properties.items():
        if str(value) == resource.identifier or (name and str(value) == name):
            continue
        text = str(value)
        if not text or text == "{}" or text == "[]":
            continue
        parts.append(f"{key}={text[:40]}")
        if len(parts) >= limit:
            break
    return "  ".join(parts)


def _self_check() -> None:
    node = TypeNode("AWS::S3::Bucket")
    assert node.label() == "AWS::S3::Bucket"
    node.loading, node.count = True, 40
    assert "loading 40" in node.label()
    node.loading, node.loaded, node.count = False, True, 98
    assert node.label() == "AWS::S3::Bucket   [dim](98)[/dim]"
    node.capped = True
    assert "(98+)" in node.label()
    node.count, node.capped = 0, False
    assert "(none)" in node.label()
    node.error = "AccessDenied"
    assert "[ERROR] AccessDenied" in node.label()
    node.reset()
    assert node.label() == "AWS::S3::Bucket" and not node.loaded

    res = cc.Resource("AWS::S3::Bucket", "my-bucket", {"BucketName": "my-bucket", "Arn": "arn:x"})
    leaf = ResourceNode("AWS::S3::Bucket", res)
    assert leaf.label().startswith("my-bucket")
    # BucketName repeats the identifier, so Arn is what is worth showing.
    assert "Arn=arn:x" in leaf.label()
    assert "BucketName" not in leaf.label()
    bare = ResourceNode("T", cc.Resource("T", "", {}))
    assert bare.label() == "(no identifier)"

    # An EC2 instance leads with its Name tag, not with the instance id.
    ec2 = ResourceNode(
        "AWS::EC2::Instance",
        cc.Resource(
            "AWS::EC2::Instance",
            "i-0abc",
            {"Tags": [{"Key": "Name", "Value": "web-01"}], "InstanceType": "t3.micro"},
        ),
    )
    label = ec2.label()
    assert label.startswith("[b]web-01[/b]"), label
    assert "i-0abc" in label

    print("[OK] tree model self-check passed")


if __name__ == "__main__":
    _self_check()
