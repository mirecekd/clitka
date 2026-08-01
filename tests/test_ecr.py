"""ECR: the model, the paginated API layer and the delete.

No network: the API layer goes through `botocore.stub.Stubber`, the same way
`test_lambda.py` does.
"""

from __future__ import annotations

import datetime as dt

import pytest
from botocore.stub import Stubber

from clitka.core import ecr
from clitka.core import ecrmodel as em
from clitka.core import ecrops as eo
from clitka.core.context import Context
from clitka.core.errors import AwsError, ReadOnlyError

ARN = "arn:aws:ecr:eu-central-1:111122223333:repository/my-app"
URI = "111122223333.dkr.ecr.eu-central-1.amazonaws.com/my-app"
DIGEST = "sha256:0123456789abcdef0123456789abcdef01234567"


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    return Context(region="eu-central-1")


def repo(name: str = "my-app", **extra):
    """A `repository` entry as DescribeRepositories returns it."""
    raw = {
        "repositoryName": name,
        "repositoryArn": ARN.replace("my-app", name),
        "repositoryUri": URI.replace("my-app", name),
        "registryId": "111122223333",
        "imageTagMutability": "MUTABLE",
        "imageScanningConfiguration": {"scanOnPush": False},
    }
    raw.update(extra)
    return raw


def image(digest: str = DIGEST, **extra):
    """An `imageDetails` entry as DescribeImages returns it."""
    raw = {
        "repositoryName": "my-app",
        "imageDigest": digest,
        "imageSizeInBytes": 1024,
        "imagePushedAt": dt.datetime(2026, 8, 1, 6, 0, tzinfo=dt.UTC),
    }
    raw.update(extra)
    return raw


# --- the self-checks ------------------------------------------------------


def test_the_self_checks_pass():
    em._self_check()
    eo._self_check()
    ecr._self_check()


# --- the model ------------------------------------------------------------


def test_the_registry_comes_out_of_the_uri():
    """`docker login` is aimed at the registry host, not the repository."""
    assert em.Repository("my-app", uri=URI).registry == URI.split("/", 1)[0]
    # A repository built from a listing that omitted the URI must not explode.
    assert em.Repository("my-app").registry == ""


def test_the_region_comes_out_of_the_arn():
    assert em.Repository("my-app", arn=ARN).region == "eu-central-1"
    assert em.Repository("my-app").region == ""


def test_an_untagged_image_says_so_and_shows_its_digest():
    # An untagged image is normal - and is exactly what a cleanup looks for.
    bare = em.Image(DIGEST)
    assert bare.untagged and bare.label.startswith("(untagged) 0123456789ab")
    assert em.Image(DIGEST, tags=("latest", "v3")).label == "latest, v3"


def test_an_image_is_always_referenced_by_digest():
    """Deleting by tag removes the image every other tag also points at."""
    tagged = em.Image(DIGEST, tags=("latest", "v3"))
    assert tagged.reference == DIGEST
    assert tagged.reference not in ("latest", "v3")


def test_the_worst_finding_wins_and_a_zero_count_is_not_a_finding():
    assert em.Image(DIGEST, findings={"LOW": 3, "HIGH": 1}).worst == "HIGH"
    assert em.Image(DIGEST, findings={"CRITICAL": 0, "LOW": 1}).worst == "LOW"
    assert em.Image(DIGEST).worst == ""


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("my-app", "my-app"),
        ("team/my-app", "team/my-app"),
        (ARN, "my-app"),
        ("arn:aws:ecr:eu-central-1:1:repository/team/my-app", "team/my-app"),
        (URI, "my-app"),
    ],
)
def test_repo_name_of(identifier, expected):
    assert em.repo_name_of(identifier) == expected


# --- repositories ---------------------------------------------------------


def test_iter_repositories_follows_the_token(ctx):
    client = ctx.client("ecr")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_repositories",
            {"repositories": [repo("one")], "nextToken": "more"},
            {"maxResults": ecr.PAGE},
        )
        stub.add_response(
            "describe_repositories",
            {"repositories": [repo("two")]},
            {"maxResults": ecr.PAGE, "nextToken": "more"},
        )
        names = [one.name for one in ecr.iter_repositories(ctx)]
    assert names == ["one", "two"]


def test_list_repositories_stops_at_the_limit(ctx):
    client = ctx.client("ecr")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_repositories",
            {"repositories": [repo("one"), repo("two")], "nextToken": "more"},
            {"maxResults": ecr.PAGE},
        )
        found = ecr.list_repositories(ctx, limit=1)
    assert [one.name for one in found] == ["one"]


def test_get_repository_accepts_a_uri(ctx):
    # A hand-typed palette entry or a docker command line supplies a URI.
    client = ctx.client("ecr")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_repositories",
            {"repositories": [repo()]},
            {"repositoryNames": ["my-app"]},
        )
        found = ecr.get_repository(ctx, URI)
    assert found.name == "my-app" and found.registry == URI.split("/", 1)[0]


def test_a_denied_listing_becomes_an_aws_error(ctx):
    client = ctx.client("ecr")
    with Stubber(client) as stub:
        stub.add_client_error(
            "describe_repositories",
            service_error_code="AccessDeniedException",
            http_status_code=403,
        )
        with pytest.raises(AwsError) as caught:
            list(ecr.iter_repositories(ctx))
    assert caught.value.code == "AccessDeniedException"


# --- images ---------------------------------------------------------------


def test_images_come_back_newest_push_first(ctx):
    older = image("sha256:aaa", imagePushedAt=dt.datetime(2026, 7, 1, tzinfo=dt.UTC))
    newer = image("sha256:bbb", imagePushedAt=dt.datetime(2026, 8, 1, tzinfo=dt.UTC))
    client = ctx.client("ecr")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_images",
            {"imageDetails": [older, newer]},
            {"repositoryName": "my-app", "maxResults": ecr.PAGE},
        )
        found = ecr.list_images(ctx, "my-app")
    assert [one.digest for one in found] == ["sha256:bbb", "sha256:aaa"]


def test_an_image_with_no_push_time_sorts_last_instead_of_crashing(ctx):
    # An image mid-push has no imagePushedAt at all, and `None` must not reach
    # the sort key as a datetime.
    nopush = image("sha256:nopush")
    del nopush["imagePushedAt"]
    client = ctx.client("ecr")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_images",
            {"imageDetails": [nopush, image()]},
            {"repositoryName": "my-app", "maxResults": ecr.PAGE},
        )
        found = ecr.list_images(ctx, "my-app")
    assert found[-1].digest == "sha256:nopush"


def test_a_repository_uri_is_reduced_before_the_call(ctx):
    # Cloud Control hands over a name, but the CLI accepts a URI.
    client = ctx.client("ecr")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_images",
            {"imageDetails": [image()]},
            {"repositoryName": "my-app", "maxResults": ecr.PAGE},
        )
        found = ecr.list_images(ctx, URI)
    assert len(found) == 1


def test_a_scan_summary_is_read_but_never_required(ctx):
    client = ctx.client("ecr")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_images",
            {
                "imageDetails": [
                    image(
                        imageScanStatus={"status": "COMPLETE"},
                        imageScanFindingsSummary={"findingSeverityCounts": {"HIGH": 2}},
                    )
                ]
            },
            {"repositoryName": "my-app", "maxResults": ecr.PAGE},
        )
        found = ecr.list_images(ctx, "my-app")
    assert found[0].worst == "HIGH" and found[0].row()["scan"] == "HIGH"


# --- deleting -------------------------------------------------------------


def test_delete_reports_both_halves_of_the_answer(ctx):
    """BatchDeleteImage answers 200 with a `failures` list - the Lambda trap again."""
    client = ctx.client("ecr")
    with Stubber(client) as stub:
        stub.add_response(
            "batch_delete_image",
            {
                "imageIds": [{"imageDigest": DIGEST}],
                "failures": [
                    {
                        "imageId": {"imageDigest": "sha256:gone"},
                        "failureCode": "ImageNotFound",
                        "failureReason": "Requested image not found",
                    }
                ],
            },
            {
                "repositoryName": "my-app",
                "imageIds": [{"imageDigest": DIGEST}, {"imageDigest": "sha256:gone"}],
            },
        )
        result = ecr.delete_images(ctx, "my-app", [DIGEST, "sha256:gone"])
    assert result["deleted"] == [DIGEST]
    assert "ImageNotFound" in result["failures"][0]


def test_a_delete_with_nothing_to_delete_never_reaches_aws(ctx):
    client = ctx.client("ecr")
    with Stubber(client) as stub:  # no response queued: a call would fail the test
        with pytest.raises(ValueError, match="no image digest"):
            ecr.delete_images(ctx, "my-app", [])
        stub.assert_no_pending_responses()


def test_read_only_mode_refuses_to_delete(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    read_only = Context(region="eu-central-1", read_only=True)
    with pytest.raises(ReadOnlyError, match="my-app"):
        ecr.delete_images(read_only, URI, [DIGEST])


# --- the login helper -----------------------------------------------------


def test_the_login_command_names_the_profile_and_the_region():
    command = ecr.login_command(Context(profile="sw-sandbox", region="eu-central-1"), "reg.example")
    assert "--profile sw-sandbox" in command
    assert "--region eu-central-1" in command
    assert command.endswith("reg.example")
    # The password must go through stdin, never through argv.
    assert "--password-stdin" in command


def test_the_login_command_survives_having_no_profile():
    assert "--profile" not in ecr.login_command(Context(region="eu-west-1"))
