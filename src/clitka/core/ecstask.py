"""One ECS task, its containers, and whether a shell can be opened in one.

Split out of `core/ecsmodel.py` for the 8 kB rule; boto3-free like it.

**`ecs execute-command` fails in four ways and all four are knowable up front** -
which matters more here than anywhere else, because a failed exec has already
suspended the app and printed a wall of `TargetNotConnectedException` by the time
the user finds out. `refuses_exec()` checks, in order: the task is `RUNNING`, it
was *started* with `--enable-execute-command` (not changeable later), the named
container exists and runs, and its **ExecuteCommandAgent managed agent** is
`RUNNING` - `PENDING` for seconds after a start, `STOPPED` without
`ssmmessages:*`. A sentence each, in the shape `ec2model.Instance.refuses()` set.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from clitka.core.ecsmodel import cluster_of_arn, name_of_arn, stamp

__all__ = ["AGENT", "RUNNING", "Container", "Task", "task_id_of"]

RUNNING = "RUNNING"
# The managed agent `ecs execute-command` talks to. ECS names it exactly so.
AGENT = "ExecuteCommandAgent"


def task_id_of(identifier: str) -> str:
    """A task ARN or a bare id reduced to the id ECS prints in the console."""
    return name_of_arn(identifier)


@dataclass(frozen=True)
class Container:
    """One container inside a task - the thing an exec actually lands in."""

    name: str
    status: str = ""
    image: str = ""
    exit_code: int | None = None
    reason: str = ""
    agent_status: str = ""
    """The `ExecuteCommandAgent` status, or "" when the task has no agent at all."""

    @property
    def running(self) -> bool:
        return self.status.upper() == RUNNING

    @property
    def can_exec(self) -> bool:
        """True when this container could be entered *if* the task allows it."""
        return self.running and self.agent_status.upper() == RUNNING

    @property
    def image_label(self) -> str:
        """The image without the registry - the tag is the news, not the account."""
        return self.image.rsplit("/", 1)[-1] if self.image else ""

    def row(self) -> dict[str, Any]:
        return {
            "identifier": self.name,
            "status": self.status.lower(),
            "image": self.image_label,
            "exec": "yes" if self.can_exec else "no",
            "exit_code": "" if self.exit_code is None else str(self.exit_code),
        }


@dataclass(frozen=True)
class Task:
    """One running (or recently stopped) ECS task."""

    arn: str
    cluster: str = ""
    last_status: str = ""
    desired_status: str = ""
    launch_type: str = ""
    task_definition: str = ""
    group: str = ""
    started_at: dt.datetime | None = None
    stopped_reason: str = ""
    cpu: str = ""
    memory: str = ""
    availability_zone: str = ""
    platform_version: str = ""
    exec_enabled: bool = False
    """`enableExecuteCommand` - decided when the task started, not changeable."""
    containers: tuple[Container, ...] = ()

    @property
    def task_id(self) -> str:
        return task_id_of(self.arn)

    @property
    def short_id(self) -> str:
        """The 12 characters the console and every log line show."""
        return self.task_id[:12]

    @property
    def label(self) -> str:
        """The service (or family) plus the short id - what a human calls a task."""
        return f"{self.service or self.family or 'task'}  {self.short_id}"

    @property
    def cluster_name(self) -> str:
        """From the field if ECS filled it in, otherwise out of our own ARN."""
        return name_of_arn(self.cluster) or cluster_of_arn(self.arn)

    @property
    def service(self) -> str:
        """The owning service. ECS reports it as `service:<name>` in `group`."""
        return self.group.split(":", 1)[1] if self.group.startswith("service:") else ""

    @property
    def family(self) -> str:
        """The family without the revision - `my-app:12` -> `my-app`."""
        return self.task_definition_label.rsplit(":", 1)[0]

    @property
    def task_definition_label(self) -> str:
        return name_of_arn(self.task_definition)

    @property
    def running(self) -> bool:
        return self.last_status.upper() == RUNNING

    @property
    def settled(self) -> bool:
        """False while ECS is still moving it - `PROVISIONING`, `PENDING`, ..."""
        return self.last_status.upper() in (RUNNING, "STOPPED")

    def container(self, name: str = "") -> Container | None:
        """A container by name, or the one an exec should default to.

        With no name: the first that *can* be entered, then the first running one,
        then the first at all - so a sidecar-heavy task lands somewhere useful
        rather than in whatever ECS happened to list first.
        """
        if name:
            return next((one for one in self.containers if one.name == name), None)
        for pick in (lambda c: c.can_exec, lambda c: c.running, lambda _c: True):
            for one in self.containers:
                if pick(one):
                    return one
        return None

    @property
    def exec_targets(self) -> tuple[str, ...]:
        """The containers an exec could actually land in, in ECS's own order."""
        return tuple(one.name for one in self.containers if one.can_exec)

    def refuses_exec(self, container: str = "") -> str:
        """Why `ecs execute-command` cannot work here, or "" when it can.

        A sentence, not a bool: the four modes need four answers, and every one is
        something to *fix* rather than retry.
        """
        if not self.running:
            if not self.settled:
                return f"{self.short_id} is {self.last_status} - wait for it to start"
            state = self.last_status.lower() or "not running"
            return f"{self.short_id} is {state}, so there is no shell in it"
        if not self.exec_enabled:
            return (
                "this task was not started with execute-command enabled - "
                "redeploy the service with `--enable-execute-command`"
            )
        target = self.container(container)
        if target is None:
            names = ", ".join(one.name for one in self.containers) or "(none)"
            return f"no container named {container!r} in this task - it has: {names}"
        if not target.running:
            return f"container {target.name} is {target.status.lower() or 'not running'}"
        if not target.can_exec:
            state = target.agent_status.lower() or "not started"
            hint = (
                "it usually takes a few seconds after the task starts"
                if state == "pending"
                else "give the task role ssmmessages:* and redeploy"
            )
            return f"the execute-command agent in {target.name} is {state} - {hint}"
        return ""

    def row(self) -> dict[str, Any]:
        return {
            "identifier": self.task_id,
            "name": self.service or self.family,
            "status": self.last_status.lower(),
            "task_definition": self.task_definition_label,
            "launch_type": self.launch_type.lower(),
            "az": self.availability_zone,
            "exec": "yes" if not self.refuses_exec() else "no",
            "started": stamp(self.started_at),
        }


def _self_check() -> None:
    """The happy path plus one of each refusal; `tests/test_ecs.py` has the rest."""
    arn = "arn:aws:ecs:eu-central-1:1:task/prod/abc123def456"
    up = Container("app", status=RUNNING, image="1.dkr.ecr.x/my-app:3", agent_status=RUNNING)
    ok = Task(arn, last_status=RUNNING, exec_enabled=True, containers=(up,), group="service:api")
    assert ok.task_id == "abc123def456" and ok.cluster_name == "prod" and ok.service == "api"
    assert up.image_label == "my-app:3" and ok.exec_targets == ("app",)
    assert ok.refuses_exec() == "" and ok.row()["exec"] == "yes"

    # One of each refusal, in the order refuses_exec() checks them.
    assert "wait for it to start" in Task(arn, last_status="PENDING").refuses_exec()
    assert "no shell in it" in Task(arn, last_status="STOPPED").refuses_exec()
    assert "enable-execute-command" in Task(arn, last_status=RUNNING).refuses_exec()
    stale = Container("app", status=RUNNING, agent_status="STOPPED")
    blocked = Task(arn, last_status=RUNNING, exec_enabled=True, containers=(stale,))
    assert "ssmmessages" in blocked.refuses_exec() and blocked.exec_targets == ()
    assert "no container named 'nope'" in ok.refuses_exec("nope")
    print("[OK] ecs task self-check passed")


if __name__ == "__main__":
    _self_check()
