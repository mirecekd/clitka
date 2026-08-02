"""S3 core: the key arithmetic, and every trap the live round turned up.

The interesting tests here are the ones that pin a *measured* shape rather than a
documented one - `Contents` being absent, a `CommonPrefix` arriving whole, and
`moment` having to be ECR's and not CloudWatch's. All three were found against
`sw-sandbox`, not by reading the API reference.
"""

from __future__ import annotations

import datetime as dt

import pytest
from botocore.stub import Stubber

from clitka.core import s3
from clitka.core.context import Context
from clitka.core.s3model import BUCKET, OBJECT, PREFIX, Bucket, Location, S3Object

WHEN = dt.datetime(2026, 4, 29, 22, 20, 46, tzinfo=dt.UTC)


@pytest.fixture
def ctx():
    return Context(profile="demo", region="eu-central-1")


@pytest.fixture
def stub(ctx, monkeypatch):
    """A stubbed `s3` client wired into the context, the ECR test pattern."""
    import boto3

    client = boto3.Session(region_name="eu-central-1").client(
        "s3", aws_access_key_id="a", aws_secret_access_key="b"
    )
    stubber = Stubber(client)
    monkeypatch.setattr(s3, "_client", lambda _ctx: client)
    stubber.activate()
    yield stubber
    stubber.deactivate()


# --- the model ------------------------------------------------------------


def test_the_pseudo_types_are_ours_and_the_bucket_type_is_awss():
    from clitka.tui.restypes import COMMON_TYPES, TREE_TYPES

    assert BUCKET in COMMON_TYPES and BUCKET in TREE_TYPES
    # Cloud Control cannot list either of these, so nothing generic may offer them.
    assert PREFIX not in COMMON_TYPES and PREFIX not in TREE_TYPES
    assert OBJECT not in COMMON_TYPES and OBJECT not in TREE_TYPES


def test_a_prefix_label_is_the_last_segment_not_the_whole_path():
    """A `CommonPrefix` arrives whole, so a tree would repeat its own ancestry."""
    assert Location("b", "logs/2026/08/").label == "08/"
    assert Location("b", "logs/a.txt").label == "a.txt"
    assert Location("b").label == "b"


def test_walking_up_from_the_root_terminates():
    root = Location("b")
    assert root.parent() == root or root.parent().key == ""


def test_an_identifier_round_trips_through_parse():
    for identifier in ("b", "b/logs/", "b/logs/a.txt"):
        assert Location.parse(identifier).identifier == identifier


def test_the_object_row_carries_the_bucket_for_f9():
    """The ECS `Cluster` lesson: the row is all a later action gets."""
    row = S3Object(Location("my-bucket", "logs/a.txt"), size=2048).row()
    assert row["Bucket"] == "my-bucket"
    assert row["Name"] == "a.txt"
    assert row["size"] == "2.0K"


def test_a_console_made_folder_placeholder_is_recognised():
    assert S3Object(Location("b", "logs/"), size=0).is_placeholder
    assert not S3Object(Location("b", "logs/a.txt"), size=0).is_placeholder


# --- the API --------------------------------------------------------------


def test_a_bucket_listing_keeps_the_creation_date(ctx, stub):
    """The live bug: `logsmodel.moment` blanked it, `ecrmodel.moment` keeps it."""
    stub.add_response(
        "list_buckets",
        {"Buckets": [{"Name": "b-one", "CreationDate": WHEN, "BucketArn": "arn:aws:s3:::b-one"}]},
        {},
    )
    found = s3.list_buckets(ctx)
    assert [one.name for one in found] == ["b-one"]
    assert found[0].created == WHEN
    assert found[0].row()["created"].startswith("2026-04-29")
    # A listing never knows the region - it costs a call per bucket.
    assert found[0].region == ""


def test_buckets_come_back_sorted_case_insensitively(ctx, stub):
    stub.add_response(
        "list_buckets",
        {"Buckets": [{"Name": "Zeta"}, {"Name": "alpha"}]},
        {},
    )
    assert [one.name for one in s3.list_buckets(ctx)] == ["alpha", "Zeta"]


def test_a_folders_only_level_has_no_contents_key_at_all(ctx, stub):
    """Measured: `Contents` is ABSENT, not empty. `raw["Contents"]` would raise."""
    stub.add_response(
        "list_objects_v2",
        {"CommonPrefixes": [{"Prefix": "scanner/"}], "IsTruncated": False},
        {"Bucket": "aci", "Delimiter": "/", "MaxKeys": s3.PAGE},
    )
    found = s3.browse(ctx, "aci")
    assert [one.identifier for one in found.folders] == ["aci/scanner/"]
    assert found.files == []
    assert not found.capped


def test_a_files_only_level_has_no_common_prefixes_key(ctx, stub):
    stub.add_response(
        "list_objects_v2",
        {
            "Contents": [
                {"Key": "probe.txt", "Size": 27, "LastModified": WHEN, "StorageClass": "STANDARD"}
            ],
            "IsTruncated": False,
        },
        {"Bucket": "flat", "Delimiter": "/", "MaxKeys": s3.PAGE},
    )
    found = s3.browse(ctx, "flat")
    assert found.folders == []
    assert [one.location.identifier for one in found.files] == ["flat/probe.txt"]
    assert found.files[0].modified == WHEN
    assert found.files[0].storage_class == "STANDARD"


def test_an_empty_bucket_is_not_an_error(ctx, stub):
    stub.add_response(
        "list_objects_v2",
        {"IsTruncated": False},
        {"Bucket": "empty", "Delimiter": "/", "MaxKeys": s3.PAGE},
    )
    found = s3.browse(ctx, "empty")
    assert found.total == 0 and not found.capped


def test_browsing_a_prefix_sends_it_as_prefix(ctx, stub):
    """A folder's own identifier is what the next call needs - that is the recursion."""
    stub.add_response(
        "list_objects_v2",
        {"CommonPrefixes": [{"Prefix": "scanner/req-1/"}], "IsTruncated": False},
        {"Bucket": "aci", "Delimiter": "/", "MaxKeys": s3.PAGE, "Prefix": "scanner/"},
    )
    found = s3.browse(ctx, "aci/scanner/")
    assert [one.label for one in found.folders] == ["req-1/"]


def test_the_folder_placeholder_is_not_listed_inside_itself(ctx, stub):
    """A console "create folder" leaves a zero-byte key equal to the prefix."""
    stub.add_response(
        "list_objects_v2",
        {
            "Contents": [
                {"Key": "logs/", "Size": 0, "LastModified": WHEN},
                {"Key": "logs/a.txt", "Size": 5, "LastModified": WHEN},
            ],
            "IsTruncated": False,
        },
        {"Bucket": "b", "Delimiter": "/", "MaxKeys": s3.PAGE, "Prefix": "logs/"},
    )
    found = s3.browse(ctx, "b/logs/")
    assert [one.location.key for one in found.files] == ["logs/a.txt"]


def test_a_truncated_level_is_followed_to_the_next_page(ctx, stub):
    for token, keys, truncated in (("t1", ["a"], True), (None, ["b"], False)):
        expected = {"Bucket": "b", "Delimiter": "/", "MaxKeys": s3.PAGE}
        if token != "t1":
            expected["ContinuationToken"] = "t1"
        response: dict = {
            "Contents": [{"Key": key, "Size": 1, "LastModified": WHEN} for key in keys],
            "IsTruncated": truncated,
        }
        if truncated:
            response["NextContinuationToken"] = "t1"
        stub.add_response("list_objects_v2", response, expected)
    found = s3.browse(ctx, "b")
    assert [one.location.key for one in found.files] == ["a", "b"]


def test_a_huge_level_is_capped_and_says_so(ctx, stub):
    """The branch-picker lesson: what does this do with 50 000 rows?"""
    stub.add_response(
        "list_objects_v2",
        {
            "Contents": [
                {"Key": f"k{index}", "Size": 1, "LastModified": WHEN} for index in range(10)
            ],
            "IsTruncated": True,
            "NextContinuationToken": "more",
        },
        {"Bucket": "big", "Delimiter": "/", "MaxKeys": s3.PAGE},
    )
    found = s3.browse(ctx, "big", limit=10)
    assert found.capped, "a truncated listing must admit it"
    assert found.total == 10


def test_get_bucket_calls_the_location_api_and_names_us_east_1(ctx, stub):
    """`LocationConstraint` is absent for us-east-1 - a value, not a failure."""
    stub.add_response("get_bucket_location", {}, {"Bucket": "old"})
    assert s3.get_bucket(ctx, "old").region == "us-east-1"

    stub.add_response(
        "get_bucket_location", {"LocationConstraint": "eu-north-1"}, {"Bucket": "far"}
    )
    assert s3.get_bucket(ctx, "far").region == "eu-north-1"


def test_get_bucket_accepts_a_uri_or_a_key_and_uses_only_the_bucket(ctx, stub):
    stub.add_response("get_bucket_location", {"LocationConstraint": "eu-west-1"}, {"Bucket": "b"})
    assert s3.get_bucket(ctx, "s3://b/logs/a.txt").region == "eu-west-1"


def test_the_self_checks_pass():
    from clitka.core import s3model

    s3model._self_check()
    s3._self_check()


def test_the_bucket_row_never_pretends_to_know_the_region():
    assert Bucket("b").row()["region"] == ""
    assert Bucket("b", region="eu-north-1").row()["region"] == "eu-north-1"
