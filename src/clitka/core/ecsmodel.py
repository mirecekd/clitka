"""What ECS hands back for clusters and services, as things CLITKA can render.

No boto3 call in here - the same seam as `logsmodel.py`, `ecrmodel.py` and
`ec2model.py`. The *task* half is `core/ecstask.py`, because a task carries the
containers and the "may I exec into this?" rules and the two together would break
the 8 kB rule. `core/ecs.py` and `core/ecsrun.py` are the API side.

The one thing about ECS that shaped this module: **everything is an ARN, and the
ARN is the only identifier the API accepts** - but nobody reads
`arn:aws:ecs:eu-central-1:1:service/prod/api` when they meant `api`. So every type
here keeps the ARN for the API and a name for the human, and `name_of_arn()` is
what derives one from the other.

`stamp` is reused from `logsmodel` rather than written again.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from clitka.core.logsmodel import stamp

__all__ = [
    "ACTIVE",
    "Cluster",
    "Service",
    "cluster_of_arn",
    "moment",
    "name_of_arn",
    "stamp",
]

ACTIVE = "ACTIVE"


def name_of_arn(identifier: str) -> str:
    """The last segment of an ECS ARN; a plain name passes through untouched.

    arn:...:cluster/prod         -> prod
    arn:...:service/prod/api     -> api
    arn:...:task/prod/abc123     -> abc123
    """
    if not identifier.startswith("arn:"):
        return identifier
    return identifier.rsplit("/", 1)[-1]


def cluster_of_arn(identifier: str) -> str:
    """The cluster out of a service or task ARN, or "" when it is not in there.

    A task ARN is `.../task/<cluster>/<id>` and a service ARN is
    `.../service/<cluster>/<name>` - which is the only reason `ecs exec` can work
    from a row that never mentioned its cluster. The older, shorter task ARN form
    (`.../task/<id>`, no cluster) is why this can answer "".
    """
    if not identifier.startswith("arn:"):
        return ""
    parts = identifier.split("/")
    return parts[1] if len(parts) > 2 else ""


def moment(when: Any) -> dt.datetime | None:
    """ECS timestamps arrive as datetimes already; anything else is discarded."""
    return when if isinstance(when, dt.datetime) else None


@dataclass(frozen=True)
class Cluster:
    """One ECS cluster - the top of the clusters -> services -> tasks walk."""

    name: str
    arn: str = ""
    status: str = ""
    running_tasks: int = 0
    pending_tasks: int = 0
    active_services: int = 0
    container_instances: int = 0
    capacity_providers: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return self.name or name_of_arn(self.arn)

    @property
    def empty(self) -> bool:
        """Nothing running and nothing deployed - worth saying out loud."""
        return not (self.running_tasks or self.pending_tasks or self.active_services)

    @property
    def kind(self) -> str:
        """Fargate, EC2 or both - what the capacity providers say it runs on."""
        providers = {one.upper() for one in self.capacity_providers}
        fargate = bool({"FARGATE", "FARGATE_SPOT"} & providers)
        other = bool(providers - {"FARGATE", "FARGATE_SPOT"})
        if fargate and other:
            return "fargate+ec2"
        if fargate:
            return "fargate"
        # No capacity provider at all is the classic EC2 cluster, and a cluster
        # with registered instances is certainly one.
        return "ec2" if (other or self.container_instances) else ""

    def row(self) -> dict[str, Any]:
        """The explorer table row. `identifier` is the column every screen keys on."""
        return {
            "identifier": self.label,
            "status": self.status,
            "services": self.active_services,
            "running": self.running_tasks,
            "pending": self.pending_tasks,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class Service:
    """One ECS service - a desired count, and however many tasks really run."""

    name: str
    arn: str = ""
    cluster: str = ""
    status: str = ""
    desired: int = 0
    running: int = 0
    pending: int = 0
    launch_type: str = ""
    task_definition: str = ""
    created: dt.datetime | None = None
    role_arn: str = ""

    @property
    def label(self) -> str:
        return self.name or name_of_arn(self.arn)

    @property
    def cluster_name(self) -> str:
        """The cluster, from the field if ECS filled it in or from our own ARN."""
        return name_of_arn(self.cluster) or cluster_of_arn(self.arn)

    @property
    def task_definition_label(self) -> str:
        """`my-app:12` - the family and revision, without the ARN around it."""
        return name_of_arn(self.task_definition)

    @property
    def healthy(self) -> bool:
        """As many tasks running as were asked for, and none still coming up."""
        return self.status == ACTIVE and self.running == self.desired and not self.pending

    @property
    def health(self) -> str:
        """A sentence-sized verdict for the row: what is wrong, or "ok"."""
        if self.status != ACTIVE:
            return self.status.lower() or "unknown"
        if self.desired == 0:
            return "scaled to zero"
        if self.pending:
            return f"{self.pending} pending"
        if self.running < self.desired:
            return f"{self.running}/{self.desired} running"
        return "ok"

    def row(self) -> dict[str, Any]:
        return {
            "identifier": self.label,
            "cluster": self.cluster_name,
            "desired": self.desired,
            "running": self.running,
            "pending": self.pending,
            "launch_type": self.launch_type.lower(),
            "task_definition": self.task_definition_label,
            "health": self.health,
        }


def _self_check() -> None:
    # The ARN arithmetic is the whole reason this module is boto3-free.
    assert name_of_arn("arn:aws:ecs:eu-central-1:1:service/prod/api") == "api"
    assert name_of_arn("prod") == "prod"
    assert cluster_of_arn("arn:aws:ecs:eu-central-1:1:task/prod/abc123") == "prod"
    # The old short task ARN carries no cluster - that must answer "", not crash.
    assert cluster_of_arn("arn:aws:ecs:eu-central-1:1:task/abc123") == ""
    assert cluster_of_arn("abc123") == ""

    cluster = Cluster("prod", status=ACTIVE, running_tasks=3, capacity_providers=("FARGATE",))
    assert cluster.label == "prod" and not cluster.empty and cluster.kind == "fargate"
    assert Cluster("m", capacity_providers=("FARGATE", "asg-1")).kind == "fargate+ec2"
    # No capacity provider but registered instances is EC2-backed; neither says
    # nothing rather than guessing.
    assert Cluster("c", container_instances=2).kind == "ec2"
    assert Cluster("c").kind == "" and Cluster("c").empty

    # ECS always reports the task definition as a full ARN.
    definition = "arn:aws:ecs:eu-central-1:1:task-definition/my-app:12"
    service = Service("api", status=ACTIVE, desired=2, running=2, task_definition=definition)
    assert service.healthy and service.health == "ok"
    assert service.task_definition_label == "my-app:12", service.task_definition_label

    # Every unhealthy shape has to read as a sentence, not as a bool.
    assert Service("a", status=ACTIVE, desired=2, running=1).health == "1/2 running"
    assert Service("a", status=ACTIVE, desired=0).health == "scaled to zero"
    assert Service("a").health == "unknown"

    assert moment("2026-08-01") is None and stamp(None) == ""
    print("[OK] ecs model self-check passed")


if __name__ == "__main__":
    _self_check()
