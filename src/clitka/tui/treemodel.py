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


@dataclass
class ResourceNode:
    """One actual resource - a leaf, unless a plugin hangs children under it.

    Deliberately NOT frozen: `expanded_children` has to be settable, and it lives
    on the payload rather than in a set beside the tree so that F5 - which throws
    the nodes away - forgets it automatically.
    """

    type_name: str
    resource: cc.Resource
    expanded_children: bool = False
    """True once `ChildLoader` has hung this resource's sub-branches under it."""

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


def sort_key(resource: cc.Resource) -> tuple[int, str, str]:
    """How resources are ordered in a branch: containers first, then by the label.

    The leaf shows the **name** first and only falls back to the identifier, so
    that is what the order has to follow - sorting by identifier would look
    random next to a column of names. Case-insensitive, because a mixture of
    `Asrp-...` and `amplify-...` otherwise splits into two alphabets, and the
    identifier breaks the tie so the order is stable for two resources sharing
    a name.

    A trailing `/` sorts **first**, because every file browser ever written puts
    the folders above the files and an S3 prefix is exactly that. Nothing else in
    CLITKA has an identifier ending in a slash, so this costs every other type
    nothing. `ChildLoader._children_done` sorts with this function itself, so a
    plugin could not impose the order from outside even if it wanted to.
    """
    label = resource.name() or resource.identifier
    container = 0 if label.endswith("/") or resource.identifier.endswith("/") else 1
    return (container, label.casefold(), resource.identifier.casefold())


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
    # The sub-branch flag must start off and be settable - a frozen node could not.
    assert leaf.expanded_children is False
    leaf.expanded_children = True

    # The order follows the label, which leads with the name where there is one.
    def made(identifier: str, **props: object) -> cc.Resource:
        return cc.Resource("T", identifier, dict(props))

    named = made("i-3", Tags=[{"Key": "Name", "Value": "beta"}])
    unordered = [made("i-1"), named, made("i-2")]
    ordered = [one.identifier for one in sorted(unordered, key=sort_key)]
    # i-3 sorts under "beta", its Name tag - which is what its leaf leads with -
    # so it comes first, not third. Sorting by identifier would put it last.
    assert ordered == ["i-3", "i-1", "i-2"], ordered
    # Case must not split the list into two alphabets.
    mixed = [made("Zeta"), made("alpha")]
    assert [one.identifier for one in sorted(mixed, key=sort_key)] == ["alpha", "Zeta"]

    # Folders first: an S3 prefix ends in `/` and belongs above the files, however
    # the alphabet feels about it. `a.log` would otherwise sort above `logs/`.
    files = [made("b/a.log"), made("b/logs/"), made("b/z.txt"), made("b/archive/")]
    assert [one.identifier for one in sorted(files, key=sort_key)] == [
        "b/archive/",
        "b/logs/",
        "b/a.log",
        "b/z.txt",
    ]

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
