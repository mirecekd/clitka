"""The preview model: how a resource's properties are grouped and formatted."""

from __future__ import annotations

import pytest

from clitka.core import cloudcontrol as cc
from clitka.tui import previewmodel as pm

EC2 = cc.Resource(
    "AWS::EC2::Instance",
    "i-0123456789abcdef0",
    {
        "InstanceType": "t3.micro",
        "VpcId": "vpc-abc",
        "SubnetId": "subnet-def",
        "State": {"Name": "running", "Code": 16},
        "LaunchTime": "2026-05-02T10:11:12Z",
        "IamInstanceProfile": {"Arn": "arn:aws:iam::1:instance-profile/p"},
        "Tags": [{"Key": "env", "Value": "dev"}],
        "Mystery": "?",
    },
)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("Arn", "Identity"),
        ("BucketName", "Identity"),
        # "id" belongs to Identity but must not swallow the network properties -
        # this is why the match order differs from the display order.
        ("VpcId", "Networking"),
        ("SubnetId", "Networking"),
        ("PubliclyAccessible", "Security"),
        ("InstanceState", "State"),
        ("CreationDate", "Timing"),
        ("RetentionInDays", "Timing"),
        ("MemorySize", "Size"),
        ("KmsKeyId", "Security"),
        ("Tags", "Tags"),
        ("Mystery", "Other"),
    ],
)
def test_group_of(key, expected):
    assert pm.group_of(key) == expected


def test_format_value_shapes():
    assert pm.format_value(None) == "[dim](none)[/dim]"
    assert pm.format_value(False) == "no"
    assert pm.format_value("") == "[dim](empty)[/dim]"
    assert pm.format_value([]) == "[dim](empty)[/dim]"
    assert pm.format_value(42) == "42"


def test_format_value_trims_a_long_scalar():
    out = pm.format_value("x" * 500)
    assert out.endswith("...")
    assert len(out) == pm.VALUE_WIDTH


def test_format_value_renders_a_nested_structure_as_indented_yaml():
    out = pm.format_value({"Name": "running", "Code": 16})
    assert out.startswith("\n")
    assert all(line.startswith("    ") for line in out.splitlines() if line)
    assert "Name: running" in out


def test_sections_put_identity_first_and_tags_last():
    titles = [section.title for section in pm.sections_for(EC2)]
    assert titles[0] == "Identity"
    assert titles[-1] == "Tags"
    assert titles.index("State") < titles.index("Networking")


def test_identity_section_always_names_the_resource():
    identity = pm.sections_for(EC2)[0]
    keys = [key for key, _ in identity.rows]
    assert keys[:2] == ["identifier", "type"]
    assert dict(identity.rows)["type"] == "AWS::EC2::Instance"


def test_every_property_lands_in_exactly_one_section():
    placed = [key for section in pm.sections_for(EC2) for key, _ in section.rows]
    for key in EC2.properties:
        assert placed.count(key) == 1, key


def test_section_lines_align_the_values():
    lines = pm.Section("Group", (("a", "1"), ("longer", "2"))).lines()
    assert lines[0] == "[b]Group[/b]"
    assert lines[1].index("1") == lines[2].index("2")


def test_empty_section_renders_nothing():
    assert pm.Section("Group", ()).lines() == []


def test_overview_and_raw_survive_an_empty_resource():
    bare = cc.Resource("AWS::S3::Bucket", "", {})
    assert "Identity" in pm.overview(bare)
    assert "TypeName: AWS::S3::Bucket" in pm.raw_yaml(bare)


def test_raw_yaml_is_the_api_shape():
    text = pm.raw_yaml(EC2)
    assert text.startswith("TypeName: AWS::EC2::Instance")
    assert "Identifier: i-0123456789abcdef0" in text
    assert "Properties:" in text
