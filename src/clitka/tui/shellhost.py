"""`ShellHost`: what `x` does - hand the terminal to a shell on the resource.

The TUI half of `core/handoff.py`. A remote interactive shell cannot live in a
Textual widget, so the app steps aside with `App.suspend()`, the child gets the
real terminal, and the app repaints when it exits. The PoC that established this
works is written up at the top of `core/handoff.py`.

Mixed into any screen that already has `context` and `selected_ref()` - the same
contract `ActionHost` and `ViewEditHost` use.

Two rules that came out of the PoC and must not be "simplified":

1. **Check the binaries before suspending.** A `FileNotFoundError` raised inside
   the suspend block leaves the app suspended with no way back.
2. **Suspend around the child, not around the decision.** Everything that can
   fail with a message happens outside, so the message is shown *in* the app.
"""

from __future__ import annotations

from clitka.core import actions as act
from clitka.core import handoff as ho
from clitka.core.context import Context

NOTHING = "Nothing selected - move the cursor onto a resource first."

# What each type opens. The value is called with (context, ref) and returns a
# Handoff, or raises ValueError with a sentence for the user.
EC2 = "AWS::EC2::Instance"
ECS_TASK = "AWS::ECS::Task"
ECS_SERVICE = "AWS::ECS::Service"


def _ec2(context: Context, ref: act.ResourceRef) -> ho.Handoff:
    target = ref.identifier or str(ref.row.get("InstanceId", ""))
    if not target.startswith("i-"):
        raise ValueError(f"{target or '(no id)'} does not look like an instance id")
    return ho.ssm_session(context, target)


def task_and_cluster(ref: act.ResourceRef) -> tuple[str, str]:
    """The task and the cluster it lives in, or `ValueError` when it cannot be told.

    Pure, so the ARN arithmetic is testable without a network - `_ecs_task` then
    only adds the live check on top.
    """
    task = ref.identifier or str(ref.row.get("TaskArn", ""))
    cluster = str(ref.row.get("Cluster") or ref.row.get("ClusterName") or "")
    if not cluster:
        # An ECS task ARN carries its cluster: .../task/<cluster>/<id>
        parts = task.split("/")
        cluster = parts[1] if len(parts) > 2 else ""
    if not cluster:
        raise ValueError("cannot tell which cluster this task is in - use `clitka` in a shell")
    return task, cluster


def _ecs_task(context: Context, ref: act.ResourceRef) -> ho.Handoff:
    """An `ecs execute-command` shell - but only after ECS has been asked.

    Since the `ecs` plugin landed this goes through `ecsrun.shell_for`, which
    **describes the task first** and raises `ValueError` with a sentence when an
    exec cannot work: the task is not running, execute-command was never enabled,
    or the managed agent is not up. That check belongs here, outside the suspend
    block (rule 1) - otherwise the user gets a wall of
    `TargetNotConnectedException` after the app has already stepped aside.
    """
    from clitka.core.ecsrun import shell_for

    task, cluster = task_and_cluster(ref)
    container = str(ref.row.get("ContainerName") or "")
    return shell_for(context, cluster, task, container=container)


OPENERS = {
    EC2: _ec2,
    ECS_TASK: _ecs_task,
}

# A type that could plausibly want a shell but cannot have one from here, with
# the reason. Better than the generic "not supported" line.
EXPLAINED = {
    ECS_SERVICE: (
        "A service is not a shell - open one of its tasks instead.\n"
        "`AWS::ECS::Task` is one `:` away."
    ),
}

NOT_A_SHELL = """\
Nothing to connect to on this type.

`x` opens an interactive shell where one exists:

  AWS::EC2::Instance   an SSM session (needs the SSM agent and an instance profile)
  AWS::ECS::Task       ecs execute-command (needs --enable-execute-command)

Both need the `aws` CLI v2 and `session-manager-plugin` on PATH.
"""


class ShellHost:
    """Mixed into a `Screen`. Expects `context`, `selected_ref()` and `_title()`."""

    context: Context

    def selected_ref(self) -> act.ResourceRef | None:  # pragma: no cover - overridden
        raise NotImplementedError

    def _title(self, text: str) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def action_connect(self) -> None:
        """`x`: open a shell on the selected resource, if it can have one."""
        ref = self.selected_ref()
        if ref is None:
            self._shell_result("Connect", NOTHING)
            return
        handoff = self._shell_handoff(ref)
        if handoff is None:
            return  # _shell_handoff already said why
        self._shell_run(handoff)

    # Prefixed like `ViewEditHost`'s methods: a plain `_handoff` or `_run` would
    # collide with something on a screen that mixes in three of these.
    def _shell_handoff(self, ref: act.ResourceRef) -> ho.Handoff | None:
        opener = OPENERS.get(ref.type_name)
        if opener is None:
            body = EXPLAINED.get(ref.type_name, NOT_A_SHELL)
            self._shell_result(f"Connect  {ref.type_name}", body)
            return None
        try:
            handoff = opener(self.context, ref)
        except ValueError as exc:
            self._shell_result(f"Connect  {ref.identifier}", f"[red]{exc}[/red]")
            return None
        # Rule 1: a missing binary must be found before the app suspends.
        gone = handoff.unavailable()
        if gone:
            self._shell_result(handoff.label, f"[red]{gone}[/red]\n\n{handoff.command()}")
            return None
        return handoff

    def _shell_run(self, handoff: ho.Handoff) -> None:
        """Suspend, run, come back. Rule 2: only the child is inside the block."""
        self._title(f"{handoff.label} - handing over the terminal...")
        with self.app.suspend():  # type: ignore[attr-defined]
            print(f"\n--- clitka: {handoff.label} ---", flush=True)
            if handoff.note:
                print(f"    ({handoff.note})", flush=True)
            outcome = handoff.run()
            print(f"--- back to clitka ({outcome.summary()}) ---", flush=True)
        self._title(outcome.summary())
        # A non-zero exit is usually the remote shell's own doing, so it stays in
        # the title. Only "we never got to run at all" deserves a modal.
        if outcome.error:
            self._shell_result(handoff.label, f"[red]{outcome.error}[/red]")

    def _shell_result(self, title: str, body: str) -> None:
        from clitka.tui.resultview import ResultScreen

        self.app.push_screen(  # type: ignore[attr-defined]
            ResultScreen(self.context, act.ActionResult(title, body))
        )


def _self_check() -> None:
    ctx = Context(profile="sw-sandbox", region="eu-central-1")

    ec2 = _ec2(ctx, act.ResourceRef.from_row(EC2, {"identifier": "i-abc"}))
    assert ec2.argv[:3] == ["aws", "ssm", "start-session"], ec2.argv
    assert "i-abc" in ec2.argv
    # Cloud Control sometimes reports the id as a property instead.
    assert "i-xyz" in _ec2(ctx, act.ResourceRef(EC2, "", {"InstanceId": "i-xyz"})).argv
    # Anything that is not an instance id is refused with a sentence.
    try:
        _ec2(ctx, act.ResourceRef(EC2, "not-an-id", {}))
    except ValueError as exc:
        assert "instance id" in str(exc), exc
    else:
        raise AssertionError("a non-instance id should have been refused")

    # An ECS exec now goes through `ecsrun.shell_for`, which describes the task
    # first - so only the pure half is checkable without a network.
    arn = "arn:aws:ecs:eu-central-1:1:task/my-cluster/abc123"
    assert task_and_cluster(act.ResourceRef(ECS_TASK, arn, {})) == (arn, "my-cluster")
    # The row wins over the ARN, and a bare TaskArn property is accepted.
    assert task_and_cluster(act.ResourceRef(ECS_TASK, "abc", {"Cluster": "c"})) == ("abc", "c")
    assert task_and_cluster(act.ResourceRef(ECS_TASK, "", {"TaskArn": arn}))[0] == arn
    try:
        task_and_cluster(act.ResourceRef(ECS_TASK, "abc", {}))
    except ValueError as exc:
        assert "cluster" in str(exc), exc
    else:
        raise AssertionError("a task with no cluster should have been refused")

    assert callable(ShellHost.action_connect)
    assert ECS_SERVICE in EXPLAINED and "tasks" in EXPLAINED[ECS_SERVICE]
    assert EC2 in NOT_A_SHELL and ho.PLUGIN in NOT_A_SHELL
    print("[OK] shell host self-check passed")


if __name__ == "__main__":
    _self_check()
