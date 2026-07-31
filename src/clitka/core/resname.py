"""What a resource is *called* - the human-readable name behind an identifier.

`i-0abc1234...` tells nobody which machine that is; its `Name` tag does, which is
why the EC2 console shows the tag and not the id (the owner's request, 2026-07-31).
Cloud Control has no notion of a name, so this is the one place that guesses it,
and `cloudcontrol.Resource.name()` is the only caller.

No boto3 and no Textual import, so the guessing is unit-testable on its own.
"""

from __future__ import annotations

from typing import Any

# Where a tag list hides. AWS uses `[{"Key": ..., "Value": ...}]` for most types
# and a plain mapping for a few.
NAME_TAG_KEYS = ("Tags", "TagList", "TagSet", "tags")

# ponytail: a short list of well-known name properties, not a per-type schema.
# Ceiling: an odd type keeps only its identifier. Upgrade path: let a plugin
# publish the name property for its own types, the way `clitka_previews` works.
NAME_PROPERTIES = (
    "Name",
    "FunctionName",
    "BucketName",
    "TableName",
    "ClusterName",
    "RepositoryName",
    "StackName",
    "LogGroupName",
    "StateMachineName",
    "TopicName",
    "QueueName",
    "RoleName",
    "DBInstanceIdentifier",
)


def name_from_tags(value: Any) -> str:
    """The value of the `Name` tag, from either tag shape AWS uses."""
    if isinstance(value, dict):
        return str(value.get("Name", "") or "")
    if isinstance(value, list | tuple):
        for tag in value:
            if isinstance(tag, dict) and str(tag.get("Key", "")) == "Name":
                return str(tag.get("Value", "") or "")
    return ""


def name_of(identifier: str, properties: dict[str, Any]) -> str:
    """A human-readable name, or "" when the identifier is all there is.

    The `Name` tag wins - that is what the console shows - then the per-service
    name properties. A property that merely repeats the identifier is not a name.
    """
    for key in NAME_TAG_KEYS:
        found = name_from_tags(properties.get(key))
        if found:
            return found
    for key in NAME_PROPERTIES:
        value = properties.get(key)
        if value and isinstance(value, str) and value != identifier:
            return value
    return ""


def _self_check() -> None:
    assert name_from_tags([{"Key": "Name", "Value": "web-01"}]) == "web-01"
    assert name_from_tags({"Name": "web-01"}) == "web-01"
    assert name_from_tags([{"Key": "env", "Value": "dev"}]) == ""
    assert name_from_tags(None) == "" and name_from_tags("nonsense") == ""
    # An empty tag value is not a name.
    assert name_from_tags([{"Key": "Name", "Value": ""}]) == ""

    assert name_of("i-0abc", {"Tags": [{"Key": "Name", "Value": "web-01"}]}) == "web-01"
    assert name_of("i-0abc", {"InstanceType": "t3.micro"}) == ""
    # The tag wins over a name property.
    tagged = {"Tags": [{"Key": "Name", "Value": "tagged"}], "FunctionName": "prop"}
    assert name_of("f", tagged) == "tagged"
    assert name_of("arn:aws:s3:::b1", {"BucketName": "b1"}) == "b1"
    # A property that only repeats the identifier tells the user nothing new.
    assert name_of("b1", {"BucketName": "b1"}) == ""
    assert name_of("", {}) == ""
    print("[OK] resource name self-check passed")


if __name__ == "__main__":
    _self_check()
