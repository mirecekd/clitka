"""`x` - the terminal handoff from a resource screen.

Nothing in here suspends a real app or runs `session-manager-plugin`: the mixin's
decisions all happen *outside* the suspend block (that is the rule the PoC
established), so they can be tested on their own.
"""

from __future__ import annotations

import pytest

from clitka.core import actions as act
from clitka.core.context import Context
from clitka.tui import shellhost as sh
from clitka.tui.explorer import ExplorerScreen
from clitka.tui.restree import ResourceTree


def ctx() -> Context:
    return Context(profile="sw-sandbox", region="eu-central-1")


def ref(type_name: str, identifier: str = "", **row):
    return act.ResourceRef(type_name, identifier, row)


class Recording(sh.ShellHost):
    """A ShellHost with the screen bits replaced by a list of what it said."""

    def __init__(self, selected):
        self.context = ctx()
        self._selected = selected
        self.results: list[tuple[str, str]] = []
        self.titles: list[str] = []
        self.ran: list[str] = []

    def selected_ref(self):
        return self._selected

    def _title(self, text: str) -> None:
        self.titles.append(text)

    def _shell_result(self, title: str, body: str) -> None:
        self.results.append((title, body))

    def _shell_run(self, handoff) -> None:
        # The real one suspends the app; here we only record that it got that far.
        self.ran.append(handoff.command())


def test_the_self_check_passes():
    sh._self_check()


# --- what each type opens -------------------------------------------------


def test_an_ec2_instance_opens_an_ssm_session():
    host = Recording(ref(sh.EC2, "i-abc"))
    host.action_connect()
    assert host.results == []
    assert host.ran and "ssm start-session" in host.ran[0]
    assert "i-abc" in host.ran[0]
    assert "--profile sw-sandbox" in host.ran[0]


def test_an_ecs_task_opens_execute_command_with_the_cluster_from_its_arn(monkeypatch):
    """Since the `ecs` plugin landed, `x` on a task describes it first.

    So the opener is stubbed here: what this test still guards is that the ARN
    reaches `shell_for` with the cluster worked out of it, and that the resulting
    handoff is what gets handed over.
    """
    arn = "arn:aws:ecs:eu-central-1:1:task/my-cluster/abc123"
    seen: list[tuple[str, str, str]] = []

    def fake(context, cluster, task, container="", command="/bin/sh"):
        seen.append((cluster, task, container))
        return sh.ho.ecs_exec(context, cluster, task, container=container, command=command)

    monkeypatch.setattr("clitka.core.ecsrun.shell_for", fake)
    host = Recording(ref(sh.ECS_TASK, arn))
    host.action_connect()
    assert seen == [("my-cluster", arn, "")], seen
    assert host.ran and "ecs execute-command" in host.ran[0]
    assert "--cluster my-cluster" in host.ran[0]
    assert "--interactive" in host.ran[0]


def test_a_task_whose_cluster_cannot_be_worked_out_is_refused_with_a_sentence():
    # No stub needed: this is refused before ECS is ever asked.
    host = Recording(ref(sh.ECS_TASK, "abc123"))
    host.action_connect()
    assert host.ran == []
    assert "cluster" in host.results[0][1]


def test_a_live_refusal_from_ecs_reaches_the_user_as_a_sentence(monkeypatch):
    """`refuses_exec` raises ValueError, and that has to be shown *in* the app.

    This is the whole point of describing the task before suspending: otherwise
    the user reads `TargetNotConnectedException` on a bare terminal.
    """

    def refuses(*_args, **_kwargs):
        raise ValueError("this task was not started with execute-command enabled")

    monkeypatch.setattr("clitka.core.ecsrun.shell_for", refuses)
    arn = "arn:aws:ecs:eu-central-1:1:task/my-cluster/abc123"
    host = Recording(ref(sh.ECS_TASK, arn))
    host.action_connect()
    assert host.ran == [], "nothing may be handed over when ECS refused"
    assert "execute-command enabled" in host.results[0][1]


def test_the_cluster_is_worked_out_before_ecs_is_asked():
    arn = "arn:aws:ecs:eu-central-1:1:task/my-cluster/abc123"
    assert sh.task_and_cluster(ref(sh.ECS_TASK, arn)) == (arn, "my-cluster")
    # A row that names the cluster wins over the ARN, and a TaskArn property works.
    assert sh.task_and_cluster(ref(sh.ECS_TASK, "abc", Cluster="c")) == ("abc", "c")
    assert sh.task_and_cluster(ref(sh.ECS_TASK, "", TaskArn=arn)) == (arn, "my-cluster")


def test_an_id_that_is_not_an_instance_is_refused():
    host = Recording(ref(sh.EC2, "not-an-id"))
    host.action_connect()
    assert host.ran == []
    assert "instance id" in host.results[0][1]


def test_a_type_with_no_shell_says_what_does_have_one():
    host = Recording(ref("AWS::S3::Bucket", "my-bucket"))
    host.action_connect()
    assert host.ran == []
    body = host.results[0][1]
    assert sh.EC2 in body and sh.ECS_TASK in body


def test_an_ecs_service_gets_its_own_explanation():
    # "A service is not a shell" is more use than the generic list.
    host = Recording(ref(sh.ECS_SERVICE, "my-service"))
    host.action_connect()
    assert "open one of its tasks" in host.results[0][1]


def test_nothing_selected_is_not_a_crash():
    host = Recording(None)
    host.action_connect()
    assert host.ran == []
    assert host.results[0][1] == sh.NOTHING


# --- the rule the PoC bought ----------------------------------------------


def test_a_missing_binary_is_reported_before_anything_would_be_suspended(monkeypatch):
    """The whole reason `unavailable()` exists: a FileNotFoundError inside the
    suspend block would leave the app suspended with no way back."""
    monkeypatch.setattr(sh.ho.shutil, "which", lambda _name: None)
    host = Recording(ref(sh.EC2, "i-abc"))
    host.action_connect()
    assert host.ran == [], "nothing must be handed over when the binary is missing"
    body = host.results[0][1]
    assert "not on PATH" in body
    # The command is still shown, so the user can see what it was going to run.
    assert "ssm start-session" in body


def test_the_handoff_is_built_only_after_the_binaries_are_there(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(sh.ho.shutil, "which", lambda name: seen.append(name) or f"/usr/bin/{name}")
    host = Recording(ref(sh.EC2, "i-abc"))
    host.action_connect()
    assert "session-manager-plugin" in seen
    assert host.ran


# --- the screens actually get the key -------------------------------------


@pytest.mark.parametrize("screen", [ResourceTree, ExplorerScreen])
def test_both_resource_screens_bind_x_to_connect(screen):
    keys = {binding.key: binding.action for binding in screen.BINDINGS}
    assert keys.get("x") == "connect", keys
    assert issubclass(screen, sh.ShellHost)


@pytest.mark.parametrize("screen", [ResourceTree, ExplorerScreen])
def test_the_mixin_does_not_shadow_another_mixins_method(screen):
    """`ViewEditHost` learned this the hard way: an un-prefixed `_fetch` was
    already owned by `BranchLoader` and the MRO called it with the wrong args."""
    for name in ("_shell_run", "_shell_handoff", "_shell_result"):
        # The name must resolve to ShellHost's own function on both screens.
        assert getattr(screen, name) is getattr(sh.ShellHost, name), name
