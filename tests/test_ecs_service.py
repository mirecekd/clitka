"""The `ecs` plugin: the hooks, the F9 actions, the preview tabs and the CLI.

The sixth plugin, and the seam held again: registering this package changed
exactly one line outside it (`plugins.BUILTIN_SERVICES`).

Two tests here exist for reasons the earlier plugins established the hard way:

- **the F9 single-key namespace is global per resource**, and this plugin claims
  keys on *three* types, so each one is checked against everything else that
  applies to the same ref (`ec2.details` on `d` was nearly a delete), and
- **`AWS::ECS::Task` is not a Cloud Control type**, so a test asserts the plugin
  is the only route to a task at all.
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
from clitka.services.ecs import actions as ea

runner = CliRunner()
STARTED = dt.datetime(2026, 7, 1, 9, 30, tzinfo=dt.UTC)
AGENT_UP = [{"name": "ExecuteCommandAgent", "lastStatus": "RUNNING"}]
TASK_ARN = "arn:aws:ecs:eu-central-1:1:task/prod/abc123def456"


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    return Context(region="eu-central-1")


@pytest.fixture
def cluster_ref():
    return ResourceRef.from_row(ea.CLUSTER, {"identifier": "prod"})


@pytest.fixture
def task_ref():
    return ResourceRef(ea.TASK, TASK_ARN, {})


def raw_cluster(**extra) -> dict:
    out = {
        "clusterName": "prod",
        "clusterArn": "arn:aws:ecs:eu-central-1:1:cluster/prod",
        "status": "ACTIVE",
        "runningTasksCount": 2,
        "activeServicesCount": 1,
        "capacityProviders": ["FARGATE"],
    }
    out.update(extra)
    return out


def raw_service(**extra) -> dict:
    out = {
        "serviceName": "api",
        "serviceArn": "arn:aws:ecs:eu-central-1:1:service/prod/api",
        "clusterArn": "arn:aws:ecs:eu-central-1:1:cluster/prod",
        "status": "ACTIVE",
        "desiredCount": 2,
        "runningCount": 2,
        "launchType": "FARGATE",
        "taskDefinition": "arn:aws:ecs:eu-central-1:1:task-definition/my-app:12",
    }
    out.update(extra)
    return out


def raw_task(status: str = "RUNNING", on: bool = True, agent: bool = True, **extra) -> dict:
    box: dict = {"name": "app", "lastStatus": "RUNNING", "image": "1.dkr.ecr.x/my-app:3"}
    if agent:
        box["managedAgents"] = AGENT_UP
    out = {
        "taskArn": TASK_ARN,
        "clusterArn": "arn:aws:ecs:eu-central-1:1:cluster/prod",
        "lastStatus": status,
        "launchType": "FARGATE",
        "taskDefinitionArn": "arn:aws:ecs:eu-central-1:1:task-definition/my-app:12",
        "group": "service:api",
        "startedAt": STARTED,
        "enableExecuteCommand": on,
        "containers": [box],
    }
    out.update(extra)
    return out


def arm_task(stub, **kwargs) -> None:
    stub.add_response(
        "describe_tasks", {"tasks": [raw_task(**kwargs)]}, {"cluster": ANY, "tasks": ANY}
    )


def arm_task_list(stub, **kwargs) -> None:
    stub.add_response(
        "list_tasks",
        {"taskArns": [TASK_ARN]},
        {"cluster": ANY, "maxResults": ANY, "desiredStatus": "RUNNING"},
    )
    arm_task(stub, **kwargs)


def test_the_self_check_passes():
    ea._self_check()


# --- the plugin seam ------------------------------------------------------


def test_the_plugin_publishes_its_cli_group():
    assert "ecs" in dict(plugins.service_apps())


def test_the_actions_and_the_previews_reach_the_registry():
    ids = {action.id for action in plugins.actions()}
    assert {"ecs.cluster", "ecs.services", "ecs.tasks", "ecs.task", "ecs.shell"} <= ids
    tabs = {tab.id for tab in plugins.previews()}
    assert {"ecs.tasks", "ecs.service", "ecs.task"} <= tabs


def test_the_ecs_group_is_in_the_root_help():
    result = runner.invoke(root_app, ["--help"])
    assert result.exit_code == 0 and "ecs" in result.stdout


def test_no_action_or_tab_id_collides_across_six_plugins():
    ids = [action.id for action in plugins.actions()]
    assert len(set(ids)) == len(ids), ids
    tabs = [tab.id for tab in plugins.previews()]
    assert len(set(tabs)) == len(tabs), tabs


@pytest.mark.parametrize("type_name", [ea.CLUSTER, ea.SERVICE, ea.TASK])
def test_no_single_key_is_claimed_twice_on_any_ecs_type(type_name):
    """The menu runs the FIRST action whose key matches, so a shared key is a bug.

    `resources.*` applies to anything with an identifier, so all three ECS types
    inherit `y j i d` - and a clash would mean pressing one key did the other
    plugin's thing. This is the trap that made `ec2.details` `e` rather than `d`.
    """
    ref = ResourceRef(type_name, "arn:aws:ecs:eu-central-1:1:service/prod/api", {})
    keys = [one.key for one in plugins.actions() if one.key and one.applies_to(ref)]
    assert len(set(keys)) == len(keys), sorted(keys)


def test_a_task_is_reachable_only_through_this_plugin():
    """Cloud Control has no `AWS::ECS::Task`, which is why the plugin exists.

    So it must not be a tree branch or a palette fallback - nothing there could
    list it - and this plugin must be what publishes actions for it.
    """
    from clitka.tui import restypes

    assert ea.TASK not in restypes.COMMON_TYPES
    assert ea.TASK not in restypes.TREE_TYPES
    ref = ResourceRef(ea.TASK, TASK_ARN, {})
    owners = {one.id.split(".", 1)[0] for one in plugins.actions() if one.applies_to(ref)}
    assert "ecs" in owners


def test_the_actions_only_offer_themselves_on_their_own_types():
    bucket = ResourceRef.from_row("AWS::S3::Bucket", {"identifier": "b"})
    assert not any(action.applies_to(bucket) for action in ea.ACTIONS)


def test_nothing_in_this_plugin_mutates():
    # Every action here reads or hands over a command, so none may be destructive.
    assert not any(action.destructive for action in ea.ACTIONS)


def test_the_cluster_is_found_from_a_service_or_a_task_ref():
    # Without this an exec cannot be built from a tree row at all.
    service = ResourceRef(ea.SERVICE, "arn:aws:ecs:eu-central-1:1:service/prod/api", {})
    assert ea.cluster_name(service) == "prod"
    assert ea.cluster_name(ResourceRef(ea.TASK, TASK_ARN, {})) == "prod"
    assert ea.cluster_name(ResourceRef(ea.TASK, "abc", {"Cluster": "dev"})) == "dev"


# --- the F9 actions -------------------------------------------------------


def test_the_cluster_action_shows_what_matters(ctx, cluster_ref):
    with Stubber(ctx.client("ecs")) as stub:
        stub.add_response(
            "describe_clusters", {"clusters": [raw_cluster()]}, {"clusters": ANY, "include": ANY}
        )
        result = ea.show_cluster(ctx, cluster_ref)
    assert result.title == "prod - cluster"
    for expected in ("ACTIVE", "fargate", "2"):
        assert expected in result.body


def test_the_services_action_lists_them_with_their_health(ctx, cluster_ref):
    with Stubber(ctx.client("ecs")) as stub:
        stub.add_response(
            "list_services",
            {"serviceArns": ["arn:aws:ecs:eu-central-1:1:service/prod/api"]},
            {"cluster": ANY, "maxResults": ANY},
        )
        stub.add_response(
            "describe_services",
            {"services": [raw_service(runningCount=1)]},
            {"cluster": ANY, "services": ANY},
        )
        result = ea.show_services(ctx, cluster_ref)
    assert "api" in result.body and "1/2 running" in result.body


def test_the_tasks_action_is_the_only_route_to_a_task(ctx, cluster_ref):
    with Stubber(ctx.client("ecs")) as stub:
        arm_task_list(stub)
        result = ea.show_tasks(ctx, cluster_ref)
    assert "abc123def456" in result.body
    # And it says whether a shell would open, which is the point of listing them.
    assert "yes" in result.body


def test_an_empty_cluster_says_how_to_see_the_dead_tasks(ctx, cluster_ref):
    with Stubber(ctx.client("ecs")) as stub:
        stub.add_response(
            "list_tasks",
            {"taskArns": []},
            {"cluster": ANY, "maxResults": ANY, "desiredStatus": "RUNNING"},
        )
        result = ea.show_tasks(ctx, cluster_ref)
    assert "--stopped" in result.body


def test_the_task_action_shows_the_containers_and_the_exec_verdict(ctx, task_ref):
    with Stubber(ctx.client("ecs")) as stub:
        arm_task(stub)
        result = ea.show_task(ctx, task_ref)
    assert "app" in result.body and "my-app:3" in result.body
    assert "ready" in result.body


def test_the_task_action_shows_the_refusal_rather_than_a_green_ready(ctx, task_ref):
    with Stubber(ctx.client("ecs")) as stub:
        arm_task(stub, on=False)
        result = ea.show_task(ctx, task_ref)
    assert "enable-execute-command" in result.body and "ready" not in result.body


def test_the_shell_hint_hands_over_the_command(ctx, task_ref):
    with Stubber(ctx.client("ecs")) as stub:
        arm_task(stub)
        result = ea.show_shell_hint(ctx, task_ref)
    assert "aws ecs execute-command" in result.body
    # `x` is what opens it - the action only says how.
    assert "x" in result.body


def test_the_shell_hint_says_why_not_when_ecs_refuses(ctx, task_ref):
    """A refusal is the answer, not a crash - the same rule as `ec2.power`."""
    with Stubber(ctx.client("ecs")) as stub:
        arm_task(stub, status="STOPPED")
        result = ea.show_shell_hint(ctx, task_ref)
    assert "No shell here" in result.body and "no shell in it" in result.body


# --- the preview tabs -----------------------------------------------------


def test_the_tabs_apply_to_the_right_types():
    by_id = {tab.id: tab for tab in ea.PREVIEWS}
    cluster = ResourceRef(ea.CLUSTER, "prod", {})
    service = ResourceRef(ea.SERVICE, "arn:aws:ecs:x:1:service/prod/api", {})
    task = ResourceRef(ea.TASK, TASK_ARN, {})
    # `Tasks` is on both a cluster and a service; the detail tabs are one each.
    assert by_id["ecs.tasks"].applies_to(cluster) and by_id["ecs.tasks"].applies_to(service)
    assert by_id["ecs.service"].applies_to(service) and not by_id["ecs.service"].applies_to(task)
    assert by_id["ecs.task"].applies_to(task) and not by_id["ecs.task"].applies_to(cluster)


def test_the_task_tab_is_the_same_block_as_the_action(ctx, task_ref):
    with Stubber(ctx.client("ecs")) as stub:
        arm_task(stub)
        body = ea.build_task_tab(ctx, task_ref)
    assert "my-app:12" in body and "app" in body


# --- the CLI --------------------------------------------------------------


def cli(monkeypatch, ctx, *args, **kwargs):
    """Run `clitka ecs ...` against a Context whose clients are stubbed."""
    monkeypatch.setattr("clitka.core.context.Context.from_env", staticmethod(lambda **_: ctx))
    return runner.invoke(root_app, ["ecs", *args], **kwargs)


def test_clusters_prints_the_name_and_what_runs(monkeypatch, ctx):
    with Stubber(ctx.client("ecs")) as stub:
        stub.add_response(
            "list_clusters",
            {"clusterArns": ["arn:aws:ecs:eu-central-1:1:cluster/prod"]},
            {"maxResults": ANY},
        )
        stub.add_response(
            "describe_clusters", {"clusters": [raw_cluster()]}, {"clusters": ANY, "include": ANY}
        )
        result = cli(monkeypatch, ctx, "clusters")
    assert result.exit_code == 0 and "prod" in result.stdout


def test_services_can_show_only_the_unhealthy_ones(monkeypatch, ctx):
    with Stubber(ctx.client("ecs")) as stub:
        stub.add_response(
            "list_services",
            {"serviceArns": ["arn:aws:ecs:eu-central-1:1:service/prod/api"]},
            {"cluster": ANY, "maxResults": ANY},
        )
        stub.add_response(
            "describe_services", {"services": [raw_service()]}, {"cluster": ANY, "services": ANY}
        )
        result = cli(monkeypatch, ctx, "services", "prod", "--unhealthy")
    assert result.exit_code == 0
    # It is fully up, so `--unhealthy` must not list it.
    assert "api" not in result.stdout


def test_tasks_lists_the_running_ones(monkeypatch, ctx):
    with Stubber(ctx.client("ecs")) as stub:
        arm_task_list(stub)
        result = cli(monkeypatch, ctx, "tasks", "prod")
    assert result.exit_code == 0 and "abc123def456" in result.stdout


def test_get_says_whether_an_exec_would_work(monkeypatch, ctx):
    with Stubber(ctx.client("ecs")) as stub:
        arm_task(stub)
        result = cli(monkeypatch, ctx, "get", "prod", TASK_ARN)
    assert result.exit_code == 0 and "ready" in result.stdout


def test_exec_dry_run_prints_the_command_without_running_anything(monkeypatch, ctx):
    with Stubber(ctx.client("ecs")) as stub:
        arm_task(stub)
        result = cli(monkeypatch, ctx, "exec", "prod", TASK_ARN, "--dry-run")
    assert result.exit_code == 0
    assert "ecs execute-command" in result.stdout and "--interactive" in result.stdout


def test_exec_exits_non_zero_when_the_task_cannot_be_entered(monkeypatch, ctx):
    """No terminal is handed over, and the user gets a sentence on stderr."""
    with Stubber(ctx.client("ecs")) as stub:
        arm_task(stub, on=False)
        result = cli(monkeypatch, ctx, "exec", "prod", TASK_ARN)
        stub.assert_no_pending_responses()
    assert result.exit_code == 1


def test_a_missing_task_exits_non_zero(monkeypatch, ctx):
    with Stubber(ctx.client("ecs")) as stub:
        stub.add_response("describe_tasks", {"tasks": []}, {"cluster": ANY, "tasks": ANY})
        result = cli(monkeypatch, ctx, "get", "prod", "gone")
    assert result.exit_code == 1
