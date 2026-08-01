"""The `ecr` plugin: the hooks, the F9 actions, the preview tab and the CLI.

The fourth plugin, and the seam held again: registering this package changed
exactly one line outside it (`plugins.BUILTIN_SERVICES`), and the proof is that
`clitka ecr` appears in the CLI and the actions reach the F9 menu without either
importing the plugin.
"""

from __future__ import annotations

import datetime as dt

import pytest
from botocore.stub import ANY, Stubber
from typer.testing import CliRunner

from clitka.cli.main import app as root_app
from clitka.core import plugins
from clitka.core.actions import ResourceRef
from clitka.core.context import Context
from clitka.services.ecr import actions as ea

runner = CliRunner()
ARN = "arn:aws:ecr:eu-central-1:111122223333:repository/my-app"
URI = "111122223333.dkr.ecr.eu-central-1.amazonaws.com/my-app"
DIGEST = "sha256:0123456789abcdef0123456789abcdef01234567"


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    return Context(region="eu-central-1")


@pytest.fixture
def ref():
    return ResourceRef.from_row(ea.TYPE_NAME, {"identifier": "my-app"})


def repository(**extra):
    raw = {
        "repositoryName": "my-app",
        "repositoryArn": ARN,
        "repositoryUri": URI,
        "registryId": "111122223333",
        "imageTagMutability": "IMMUTABLE",
        "imageScanningConfiguration": {"scanOnPush": True},
        "createdAt": dt.datetime(2026, 7, 1, tzinfo=dt.UTC),
    }
    raw.update(extra)
    return raw


def image(digest: str = DIGEST, **extra):
    raw = {
        "repositoryName": "my-app",
        "imageDigest": digest,
        "imageSizeInBytes": 2048,
        "imagePushedAt": dt.datetime(2026, 8, 1, tzinfo=dt.UTC),
    }
    raw.update(extra)
    return raw


def test_the_self_check_passes():
    ea._self_check()


# --- the plugin seam ------------------------------------------------------


def test_the_plugin_publishes_its_cli_group():
    assert "ecr" in dict(plugins.service_apps())


def test_the_actions_and_the_preview_reach_the_registry():
    ids = {action.id for action in plugins.actions()}
    assert {"ecr.config", "ecr.images", "ecr.cleanup", "ecr.login"} <= ids
    assert "ecr.images" in {tab.id for tab in plugins.previews()}


def test_the_ecr_group_is_in_the_root_help():
    result = runner.invoke(root_app, ["--help"])
    assert result.exit_code == 0
    assert "ecr" in result.stdout


def test_no_action_or_tab_id_collides_across_four_plugins():
    ids = [action.id for action in plugins.actions()]
    assert len(set(ids)) == len(ids), ids
    tabs = [tab.id for tab in plugins.previews()]
    assert len(set(tabs)) == len(tabs), tabs


def test_the_actions_only_offer_themselves_on_a_repository():
    bucket = ResourceRef.from_row("AWS::S3::Bucket", {"identifier": "b"})
    assert not any(action.applies_to(bucket) for action in ea.ACTIONS)


# --- the F9 actions -------------------------------------------------------


def test_the_config_action_shows_what_matters(ctx, ref):
    client = ctx.client("ecr")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_repositories",
            {"repositories": [repository()]},
            {"repositoryNames": ["my-app"]},
        )
        result = ea.show_config(ctx, ref)
    assert result.title == "my-app - configuration"
    for expected in ("immutable", "yes", URI):
        assert expected in result.body


def test_the_images_action_counts_the_untagged_ones(ctx, ref):
    client = ctx.client("ecr")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_images",
            {"imageDetails": [image(imageTags=["latest"]), image("sha256:bare")]},
            {"repositoryName": "my-app", "maxResults": ANY},
        )
        result = ea.show_images(ctx, ref)
    assert "2 image(s), 1 untagged" in result.body
    assert "latest" in result.body and "(untagged)" in result.body


def test_an_empty_repository_reads_as_empty_not_as_an_error(ctx, ref):
    client = ctx.client("ecr")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_images",
            {"imageDetails": []},
            {"repositoryName": "my-app", "maxResults": ANY},
        )
        result = ea.show_images(ctx, ref)
    assert "no images" in result.body


def test_the_cleanup_action_hands_over_a_command_it_does_not_run(ctx, ref):
    client = ctx.client("ecr")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_images",
            {"imageDetails": [image("sha256:bare"), image("sha256:two")]},
            {"repositoryName": "my-app", "maxResults": ANY},
        )
        result = ea.show_cleanup(ctx, ref)
    # No batch_delete_image is armed: if it deleted anything the test would fail.
    assert "2 untagged image(s)" in result.body
    assert "ecr delete my-app --untagged" in result.body


def test_the_cleanup_action_says_so_when_there_is_nothing_to_clean(ctx, ref):
    client = ctx.client("ecr")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_images",
            {"imageDetails": [image(imageTags=["latest"])]},
            {"repositoryName": "my-app", "maxResults": ANY},
        )
        result = ea.show_cleanup(ctx, ref)
    assert "Nothing to clean up" in result.body


def test_the_login_action_survives_a_denied_describe(ctx, ref):
    # It is a hint, not a listing: a denial must still produce the command shape.
    client = ctx.client("ecr")
    with Stubber(client) as stub:
        stub.add_client_error(
            "describe_repositories",
            service_error_code="AccessDeniedException",
            http_status_code=403,
        )
        result = ea.show_login(ctx, ref)
    assert "--password-stdin" in result.body


# --- the preview tab ------------------------------------------------------


def test_the_images_tab_is_the_same_block_as_the_action(ctx, ref):
    client = ctx.client("ecr")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_images",
            {"imageDetails": [image(imageTags=["v1"])]},
            {"repositoryName": "my-app", "maxResults": ANY},
        )
        body = ea.build_images_tab(ctx, ref)
    assert "v1" in body and "1 image(s)" in body


# --- the CLI --------------------------------------------------------------


def cli(monkeypatch, ctx, *args):
    """Run `clitka ecr ...` against a Context whose clients are stubbed."""
    monkeypatch.setattr("clitka.core.context.Context.from_env", staticmethod(lambda **_: ctx))
    return runner.invoke(root_app, ["ecr", *args])


def test_repos_prints_a_table(monkeypatch, ctx):
    client = ctx.client("ecr")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_repositories", {"repositories": [repository()]}, {"maxResults": ANY}
        )
        result = cli(monkeypatch, ctx, "repos")
    assert result.exit_code == 0
    assert "my-app" in result.stdout


def test_images_with_digests_prints_bare_digests_for_piping(monkeypatch, ctx):
    client = ctx.client("ecr")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_images",
            {"imageDetails": [image()]},
            {"repositoryName": "my-app", "maxResults": ANY},
        )
        result = cli(monkeypatch, ctx, "images", "my-app", "--digests")
    assert result.exit_code == 0
    assert result.stdout.strip() == DIGEST


def test_images_untagged_filters_the_listing(monkeypatch, ctx):
    client = ctx.client("ecr")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_images",
            {"imageDetails": [image(imageTags=["latest"]), image("sha256:bare")]},
            {"repositoryName": "my-app", "maxResults": ANY},
        )
        result = cli(monkeypatch, ctx, "images", "my-app", "--untagged", "--digests")
    assert result.exit_code == 0
    assert result.stdout.strip() == "sha256:bare"


def test_delete_asks_first_and_an_answer_of_no_deletes_nothing(monkeypatch, ctx):
    client = ctx.client("ecr")
    with Stubber(client) as stub:  # nothing armed: a delete would fail the test
        result = runner.invoke(
            root_app, ["ecr", "delete", "my-app", DIGEST], input="n\n", obj={"context": ctx}
        )
        stub.assert_no_pending_responses()
    assert result.exit_code != 0


def test_delete_with_yes_reports_the_failures_and_exits_non_zero(monkeypatch, ctx):
    client = ctx.client("ecr")
    with Stubber(client) as stub:
        stub.add_response(
            "batch_delete_image",
            {
                "failures": [
                    {
                        "imageId": {"imageDigest": DIGEST},
                        "failureCode": "ImageNotFound",
                        "failureReason": "gone",
                    }
                ],
            },
            {"repositoryName": "my-app", "imageIds": [{"imageDigest": DIGEST}]},
        )
        result = cli(monkeypatch, ctx, "delete", "my-app", DIGEST, "--yes")
    assert result.exit_code == 1


def test_delete_with_nothing_to_delete_complains(monkeypatch, ctx):
    result = cli(monkeypatch, ctx, "delete", "my-app", "--yes")
    assert result.exit_code == 1


def test_login_prints_the_one_liner(monkeypatch, ctx):
    result = cli(monkeypatch, ctx, "login")
    assert result.exit_code == 0
    assert "--password-stdin" in result.stdout
