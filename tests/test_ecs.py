"""`core/ecsmodel.py`, `ecstask.py`, `ecs.py`, `ecsrun.py`.

Two things get the most attention here, because they are the two things ECS gets
wrong if nobody looks:

1. **every list call returns ARNs only**, so each generator has to pair the list
   with a `Describe*` and respect its batch limit (10 for services), and
2. **`ecs execute-command` fails in four different ways**, all of which must be
   refused with a sentence *before* the terminal is handed over.
"""

from __future__ import annotations

import datetime as dt

import pytest
from botocore.stub import ANY, Stubber

from clitka.core import ecs, ecsrun, ecstask
from clitka.core import ecsmodel as em
from clitka.core.context import Context

STARTED = dt.datetime(2026, 7, 1, 9, 30, tzinfo=dt.UTC)
AGENT_UP = [{"name": "ExecuteCommandAgent", "lastStatus": "RUNNING"}]
TASK_ARN = "arn:aws:ecs:eu-central-1:1:task/prod/abc123def456"


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    return Context(region="eu-central-1")


def raw_task(status: str = "RUNNING", on: bool = True, agent: bool = True, **extra) -> dict:
    box: dict = {"name": "app", "lastStatus": "RUNNING", "image": "1.dkr.ecr.x/my-app:3"}
    if agent:
        box["managedAgents"] = AGENT_UP
    out = {
        "taskArn": TASK_ARN,
        "clusterArn": "arn:aws:ecs:eu-central-1:1:cluster/prod",
        "lastStatus": status,
        "desiredStatus": "RUNNING",
        "launchType": "FARGATE",
        "taskDefinitionArn": "arn:aws:ecs:eu-central-1:1:task-definition/my-app:12",
        "group": "service:api",
        "startedAt": STARTED,
        "enableExecuteCommand": on,
        "containers": [box],
    }
    out.update(extra)
    return out


def test_the_self_checks_pass():
    em._self_check()
    ecstask._self_check()
    ecsrun._self_check()
    ecs._self_check()


# --- the model ------------------------------------------------------------


def test_an_ecs_arn_yields_both_the_name_and_the_cluster():
    assert em.name_of_arn("arn:aws:ecs:x:1:service/prod/api") == "api"
    assert em.cluster_of_arn("arn:aws:ecs:x:1:service/prod/api") == "prod"
    assert em.cluster_of_arn("arn:aws:ecs:x:1:task/prod/abc") == "prod"
    # The older short task ARN has no cluster in it - that must answer "".
    assert em.cluster_of_arn("arn:aws:ecs:x:1:task/abc") == ""
    assert em.name_of_arn("plain-name") == "plain-name"


def test_service_health_is_a_sentence_not_a_flag():
    healthy = em.Service("api", status=em.ACTIVE, desired=2, running=2)
    assert healthy.healthy and healthy.health == "ok"
    for service, said in (
        (em.Service("a", status=em.ACTIVE, desired=2, running=1), "1/2 running"),
        (em.Service("a", status=em.ACTIVE, desired=2, running=1, pending=1), "1 pending"),
        (em.Service("a", status="DRAINING"), "draining"),
        (em.Service("a"), "unknown"),
    ):
        assert service.health == said and not service.healthy


def test_a_service_scaled_to_zero_is_healthy_and_says_so_anyway():
    """0 running out of 0 desired IS doing what it was told - but say which.

    `healthy` and `health` answer different questions, and this is the one case
    where they disagree in tone: nothing is wrong, and yet "ok" would hide that
    the service is deliberately off. The row shows the sentence.
    """
    idle = em.Service("a", status=em.ACTIVE, desired=0, running=0)
    assert idle.healthy, "nothing is broken about a service that was scaled down"
    assert idle.health == "scaled to zero", "but the row must not just say ok"


def test_a_cluster_says_what_it_runs_on():
    assert em.Cluster("c", capacity_providers=("FARGATE_SPOT",)).kind == "fargate"
    assert em.Cluster("c", capacity_providers=("FARGATE", "asg")).kind == "fargate+ec2"
    # A classic EC2 cluster names no provider but has instances registered.
    assert em.Cluster("c", container_instances=3).kind == "ec2"
    assert em.Cluster("c").kind == "" and em.Cluster("c").empty


# --- the four exec refusals -----------------------------------------------


def task(status: str = "RUNNING", on: bool = True, *boxes: ecstask.Container) -> ecstask.Task:
    up = ecstask.Container("app", status="RUNNING", agent_status="RUNNING")
    return ecstask.Task(TASK_ARN, last_status=status, exec_enabled=on, containers=boxes or (up,))


def test_a_task_that_is_ready_refuses_nothing():
    one = task()
    assert one.refuses_exec() == "" and one.exec_targets == ("app",)
    assert one.row()["exec"] == "yes"


def test_a_task_that_is_still_starting_is_told_to_wait():
    # "wait" and "it is gone" are different answers to different problems.
    for state in ("PENDING", "PROVISIONING", "ACTIVATING"):
        assert "wait for it to start" in task(state).refuses_exec(), state


def test_a_stopped_task_has_no_shell_in_it():
    assert "no shell in it" in task("STOPPED").refuses_exec()


def test_execute_command_cannot_be_switched_on_afterwards_so_it_says_redeploy():
    said = task(on=False).refuses_exec()
    assert "redeploy" in said and "--enable-execute-command" in said


def test_the_managed_agent_is_the_fourth_failure_and_reads_differently_each_way():
    def agent(state: str) -> str:
        box = ecstask.Container("app", status="RUNNING", agent_status=state)
        return task("RUNNING", True, box).refuses_exec()

    # PENDING right after a start is a "wait"; STOPPED (or absent) is an IAM problem.
    assert "a few seconds" in agent("PENDING")
    assert "ssmmessages" in agent("STOPPED")
    assert "ssmmessages" in agent("")


def test_a_dead_sidecar_is_skipped_but_still_refused_when_asked_for_by_name():
    dead = ecstask.Container("sidecar", status="STOPPED", exit_code=1)
    up = ecstask.Container("app", status="RUNNING", agent_status="RUNNING")
    both = task("RUNNING", True, dead, up)
    # The default pick lands where an exec can work, not on whatever came first.
    assert both.container() is up and both.refuses_exec() == ""
    assert "sidecar is stopped" in both.refuses_exec("sidecar")
    assert "no container named 'nope'" in both.refuses_exec("nope")
    assert both.exec_targets == ("app",)


def test_a_task_with_no_containers_reported_does_not_crash():
    bare = ecstask.Task(TASK_ARN, last_status="RUNNING", exec_enabled=True)
    assert bare.refuses_exec() != "" and bare.container() is None


# --- the listings ---------------------------------------------------------


def test_clusters_are_listed_as_arns_then_described(ctx):
    client = ctx.client("ecs")
    with Stubber(client) as stub:
        stub.add_response(
            "list_clusters",
            {"clusterArns": ["arn:aws:ecs:eu-central-1:1:cluster/prod"]},
            {"maxResults": ANY},
        )
        stub.add_response(
            "describe_clusters",
            {"clusters": [{"clusterName": "prod", "status": "ACTIVE", "runningTasksCount": 3}]},
            {"clusters": ANY, "include": ANY},
        )
        found = list(ecs.iter_clusters(ctx))
    assert [one.name for one in found] == ["prod"]
    assert found[0].running_tasks == 3


def test_services_are_described_ten_at_a_time(ctx):
    """`DescribeServices` takes ten; an eleventh ARN in one call is rejected."""
    arns = [f"arn:aws:ecs:eu-central-1:1:service/prod/s{n}" for n in range(12)]
    client = ctx.client("ecs")
    with Stubber(client) as stub:
        stub.add_response(
            "list_services", {"serviceArns": arns}, {"cluster": ANY, "maxResults": ANY}
        )
        # Two describes, not one: ten then two.
        stub.add_response(
            "describe_services",
            {"services": [{"serviceName": f"s{n}"} for n in range(10)]},
            {"cluster": ANY, "services": arns[:10]},
        )
        stub.add_response(
            "describe_services",
            {"services": [{"serviceName": f"s{n}"} for n in (10, 11)]},
            {"cluster": ANY, "services": arns[10:]},
        )
        found = list(ecs.iter_services(ctx, "prod"))
        stub.assert_no_pending_responses()
    assert len(found) == 12


def test_the_cluster_listing_follows_the_next_token(ctx):
    client = ctx.client("ecs")
    with Stubber(client) as stub:
        stub.add_response(
            "list_clusters", {"clusterArns": ["a"], "nextToken": "more"}, {"maxResults": ANY}
        )
        stub.add_response(
            "describe_clusters",
            {"clusters": [{"clusterName": "a"}]},
            {
                "clusters": ANY,
                "include": ANY,
            },
        )
        stub.add_response(
            "list_clusters", {"clusterArns": ["b"]}, {"maxResults": ANY, "nextToken": "more"}
        )
        stub.add_response(
            "describe_clusters",
            {"clusters": [{"clusterName": "b"}]},
            {
                "clusters": ANY,
                "include": ANY,
            },
        )
        found = list(ecs.iter_clusters(ctx))
    assert [one.name for one in found] == ["a", "b"]


def test_tasks_are_running_only_unless_stopped_is_asked_for(ctx):
    """`ListTasks` hides stopped tasks - so `--stopped` is a second call."""
    client = ctx.client("ecs")
    with Stubber(client) as stub:
        stub.add_response(
            "list_tasks",
            {"taskArns": [TASK_ARN]},
            {"cluster": ANY, "maxResults": ANY, "desiredStatus": "RUNNING"},
        )
        stub.add_response("describe_tasks", {"tasks": [raw_task()]}, {"cluster": ANY, "tasks": ANY})
        stub.add_response(
            "list_tasks",
            {"taskArns": ["arn:aws:ecs:eu-central-1:1:task/prod/dead"]},
            {"cluster": ANY, "maxResults": ANY, "desiredStatus": "STOPPED"},
        )
        stub.add_response(
            "describe_tasks",
            {"tasks": [raw_task("STOPPED", taskArn="arn:aws:ecs:eu-central-1:1:task/prod/dead")]},
            {"cluster": ANY, "tasks": ANY},
        )
        found = ecs.list_tasks(ctx, "prod", include_stopped=True)
        stub.assert_no_pending_responses()
    # Running first, whatever order the two calls answered in.
    assert [one.running for one in found] == [True, False]


def test_a_service_filter_sends_the_name_not_the_arn(ctx):
    # ListTasks rejects an ARN in serviceName - so it has to be reduced first.
    client = ctx.client("ecs")
    with Stubber(client) as stub:
        stub.add_response(
            "list_tasks",
            {"taskArns": []},
            {
                "cluster": ANY,
                "maxResults": ANY,
                "desiredStatus": "RUNNING",
                "serviceName": "api",
            },
        )
        list(ecs.iter_tasks(ctx, "prod", "arn:aws:ecs:eu-central-1:1:service/prod/api"))
        stub.assert_no_pending_responses()


def test_a_described_task_carries_its_agent_status_and_its_cluster(ctx):
    client = ctx.client("ecs")
    with Stubber(client) as stub:
        stub.add_response("describe_tasks", {"tasks": [raw_task()]}, {"cluster": ANY, "tasks": ANY})
        one = ecs.get_task(ctx, "prod", TASK_ARN)
    assert one.cluster_name == "prod" and one.service == "api"
    assert one.refuses_exec() == "", one.refuses_exec()
    assert one.containers[0].image_label == "my-app:3"


def test_a_task_without_the_managed_agent_is_refused_after_a_real_describe(ctx):
    client = ctx.client("ecs")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_tasks", {"tasks": [raw_task(agent=False)]}, {"cluster": ANY, "tasks": ANY}
        )
        one = ecs.get_task(ctx, "prod", TASK_ARN)
    assert "ssmmessages" in one.refuses_exec()


def test_a_missing_task_is_a_lookup_error(ctx):
    client = ctx.client("ecs")
    with Stubber(client) as stub:
        stub.add_response("describe_tasks", {"tasks": []}, {"cluster": ANY, "tasks": ANY})
        with pytest.raises(LookupError):
            ecs.get_task(ctx, "prod", "gone")


# --- shell_for: the describe-before-suspend rule ---------------------------


def test_shell_for_builds_the_exec_command_for_a_ready_task(ctx):
    client = ctx.client("ecs")
    with Stubber(client) as stub:
        stub.add_response("describe_tasks", {"tasks": [raw_task()]}, {"cluster": ANY, "tasks": ANY})
        handoff = ecsrun.shell_for(ctx, "prod", TASK_ARN)
    assert handoff.argv[:3] == ["aws", "ecs", "execute-command"], handoff.argv
    assert "--interactive" in handoff.argv and "prod" in handoff.argv
    # The container it picked is named explicitly, so a sidecar cannot win later.
    assert handoff.argv[-2:] == ["--container", "app"], handoff.argv


def test_shell_for_refuses_before_any_terminal_is_handed_over(ctx):
    """The reason this function exists at all: the refusal has to happen here."""
    client = ctx.client("ecs")
    with Stubber(client) as stub:
        stub.add_response(
            "describe_tasks", {"tasks": [raw_task(on=False)]}, {"cluster": ANY, "tasks": ANY}
        )
        with pytest.raises(ValueError, match="enable-execute-command"):
            ecsrun.shell_for(ctx, "prod", TASK_ARN)


def test_shell_for_honours_a_named_container(ctx):
    client = ctx.client("ecs")
    with Stubber(client) as stub:
        stub.add_response("describe_tasks", {"tasks": [raw_task()]}, {"cluster": ANY, "tasks": ANY})
        with pytest.raises(ValueError, match="no container named"):
            ecsrun.shell_for(ctx, "prod", TASK_ARN, container="nope")
