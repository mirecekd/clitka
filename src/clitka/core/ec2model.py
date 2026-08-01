"""What EC2 hands back, as things CLITKA can render.

No boto3 call in here - the same seam as `logsmodel.py`, `lambdamodel.py` and
`ecrmodel.py`, so the state machine, the `Name` tag and the "may I stop this?"
question are testable without a network. `core/ec2.py` is the API side.

The one thing about EC2 that shaped this module: **an instance has a state, and
the state decides which operations are legal.** `start` on a running instance is
a no-op AWS accepts silently; `stop` on a `pending` one is an error. So the
model answers `can_start` / `can_stop` / `can_reboot` and the actions ask.

`stamp` is reused from `logsmodel` rather than written again.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from clitka.core.logsmodel import stamp
from clitka.core.resname import name_from_tags

__all__ = [
    "RUNNING",
    "STOPPED",
    "Instance",
    "instance_id_of",
    "stamp",
]

RUNNING = "running"
STOPPED = "stopped"

# The states AWS reports, and what may be done from each. A state that is not
# listed here is transitional (`pending`, `stopping`, `shutting-down`), and
# nothing may be done until it settles.
_MAY_START = (STOPPED,)
_MAY_STOP = (RUNNING,)
_MAY_REBOOT = (RUNNING,)

# `f"{verb}ed"` would say "stoped". Spelling out three words is cheaper than an
# inflection rule, and the self-check found this the first time it ran.
_PAST = {"start": "started", "stop": "stopped", "reboot": "rebooted"}


@dataclass(frozen=True)
class Instance:
    """One EC2 instance, as the explorer, the CLI and the preview show it."""

    instance_id: str
    name: str = ""
    state: str = ""
    instance_type: str = ""
    private_ip: str = ""
    public_ip: str = ""
    availability_zone: str = ""
    launched: dt.datetime | None = None
    key_name: str = ""
    vpc_id: str = ""
    subnet_id: str = ""
    platform: str = ""
    state_reason: str = ""

    @property
    def label(self) -> str:
        """What a human calls this instance: its `Name` tag, or the id."""
        return self.name or self.instance_id

    @property
    def running(self) -> bool:
        return self.state == RUNNING

    @property
    def settled(self) -> bool:
        """False while AWS is still moving it between states."""
        return self.state in (RUNNING, STOPPED)

    @property
    def can_start(self) -> bool:
        return self.state in _MAY_START

    @property
    def can_stop(self) -> bool:
        return self.state in _MAY_STOP

    @property
    def can_reboot(self) -> bool:
        return self.state in _MAY_REBOOT

    def refuses(self, what: str) -> str:
        """Why `what` cannot be done right now, or "" when it can.

        A sentence rather than a bool, because "it is already running" and "it is
        still starting" are different problems and the user needs to know which.
        """
        allowed = {"start": self.can_start, "stop": self.can_stop, "reboot": self.can_reboot}
        if allowed.get(what, False):
            return ""
        if not self.settled:
            return f"{self.label} is {self.state or 'in an unknown state'} - wait for it to settle"
        done = _PAST.get(what, what)
        return f"{self.label} is {self.state or 'in an unknown state'}, so it cannot be {done}"

    def row(self) -> dict[str, Any]:
        """The explorer table row. `identifier` is the column every screen keys on."""
        return {
            "identifier": self.instance_id,
            "name": self.name,
            "state": self.state,
            "type": self.instance_type,
            "private_ip": self.private_ip,
            "public_ip": self.public_ip,
            "az": self.availability_zone,
            "launched": stamp(self.launched),
        }


def instance_id_of(identifier: str) -> str:
    """An id, an ARN or a `Name`-tag guess reduced to the plain instance id.

    Cloud Control identifies an instance by its id, but an ARN can arrive from a
    CLI argument, and a hand-typed name is passed through untouched so
    `core/ec2.py` can look it up by tag.
    """
    if identifier.startswith("arn:") and "/" in identifier:
        return identifier.rsplit("/", 1)[1]
    return identifier


def _self_check() -> None:
    running = Instance(
        "i-0abc1234",
        name="web-01",
        state=RUNNING,
        instance_type="t3.micro",
        private_ip="10.0.0.5",
    )
    assert running.label == "web-01" and running.running and running.settled
    assert running.can_stop and running.can_reboot and not running.can_start
    assert running.refuses("stop") == "" and running.refuses("reboot") == ""
    assert "cannot be started" in running.refuses("start")
    assert running.row()["identifier"] == "i-0abc1234"
    assert running.row()["name"] == "web-01"

    stopped = Instance("i-0abc1234", state=STOPPED)
    # With no Name tag the id is the only thing to call it.
    assert stopped.label == "i-0abc1234"
    assert stopped.can_start and not stopped.can_stop and not stopped.can_reboot
    assert "cannot be stopped" in stopped.refuses("stop")

    # A transitional state forbids everything, and says so differently.
    moving = Instance("i-0abc1234", name="web-01", state="pending")
    assert not (moving.can_start or moving.can_stop or moving.can_reboot)
    assert not moving.settled
    assert "wait for it to settle" in moving.refuses("start")
    # An instance whose state AWS did not report must not crash either.
    assert "unknown state" in Instance("i-1").refuses("start")
    # An unknown verb is refused rather than silently allowed.
    assert running.refuses("terminate") != ""

    assert instance_id_of("i-0abc") == "i-0abc"
    assert instance_id_of("arn:aws:ec2:eu-central-1:1:instance/i-0abc") == "i-0abc"
    assert instance_id_of("web-01") == "web-01"
    # `Name` tag handling is shared with the tree, not restated here.
    assert name_from_tags([{"Key": "Name", "Value": "web-01"}]) == "web-01"
    print("[OK] ec2 model self-check passed")


if __name__ == "__main__":
    _self_check()
