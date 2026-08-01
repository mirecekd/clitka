"""Tests for `core.handoff` - the terminal handoff M4 is built on.

Nothing in here talks to AWS or suspends anything: the argv is built from a
Context and the only child processes are `true` and `false`.
"""

from __future__ import annotations

from clitka.core import handoff
from clitka.core.context import Context
from clitka.core.handoff import (
    AWS,
    PLUGIN,
    Handoff,
    Outcome,
    Recorder,
    ecs_exec,
    ssm_port_forward,
    ssm_session,
)


def ctx() -> Context:
    return Context(profile="sw-sandbox", region="eu-central-1")


def test_the_self_check_passes() -> None:
    handoff._self_check()


def test_the_ssm_session_carries_the_profile_and_the_region() -> None:
    made = ssm_session(ctx(), "i-abc")
    assert made.argv[:3] == [AWS, "ssm", "start-session"]
    assert made.argv[3:7] == ["--profile", "sw-sandbox", "--region", "eu-central-1"]
    assert made.argv[-2:] == ["--target", "i-abc"]
    # The child resolves its own credentials, so the plugin is required too.
    assert made.needs == (AWS, PLUGIN)
    assert "exit" in made.note


def test_a_bare_context_emits_no_empty_flags() -> None:
    # `--profile ''` would make the aws CLI fail with a confusing message.
    made = ssm_session(Context(), "i-abc")
    assert made.argv == [AWS, "ssm", "start-session", "--target", "i-abc"]


def test_the_port_forward_names_both_ports() -> None:
    made = ssm_port_forward(ctx(), "i-abc", remote=5432, local=15432)
    assert "AWS-StartPortForwardingSession" in made.argv
    assert "portNumber=5432,localPortNumber=15432" in made.argv
    assert "15432" in made.label and "5432" in made.label


def test_ecs_exec_is_interactive_and_names_the_container() -> None:
    task = "arn:aws:ecs:eu-central-1:1:task/cluster/abc123"
    made = ecs_exec(ctx(), "cluster", task, container="app", command="/bin/bash")
    assert made.argv[:3] == [AWS, "ecs", "execute-command"]
    assert "--interactive" in made.argv
    assert made.argv[-2:] == ["--container", "app"]
    assert "/bin/bash" in made.argv
    # The label shows the task id, not the whole ARN.
    assert made.label.endswith("abc123")
    assert "arn:" not in made.label


def test_ecs_exec_omits_the_container_when_there_is_only_one() -> None:
    made = ecs_exec(ctx(), "cluster", "task/abc", command="/bin/sh")
    assert "--container" not in made.argv


def test_a_missing_binary_is_reported_before_anything_is_suspended() -> None:
    # This is the important one: the PoC showed a FileNotFoundError raised inside
    # the suspend block would strand the app, so the check happens up front.
    made = Handoff("nope", ["clitka-no-such-binary"], needs=("clitka-no-such-binary",))
    assert made.missing() == ["clitka-no-such-binary"]
    complaint = made.unavailable()
    assert "not on PATH" in complaint
    outcome = made.run()
    assert not outcome.ok
    assert outcome.error == complaint


def test_an_undeclared_missing_binary_is_still_survivable() -> None:
    # `needs` says nothing is required, so run() has to catch the error itself.
    made = Handoff("nope", ["clitka-no-such-binary"], needs=())
    assert made.missing() == []
    outcome = made.run()
    assert not outcome.ok
    assert "not found" in outcome.error


def test_a_child_that_succeeds_and_a_child_that_fails() -> None:
    good = Handoff("true", ["true"], needs=("true",)).run()
    assert good.ok
    assert good.summary() == "true: finished"

    bad = Handoff("false", ["false"], needs=("false",)).run()
    assert not bad.ok
    assert bad.returncode == 1
    assert "exited with 1" in bad.summary()


def test_the_command_is_printable() -> None:
    made = ssm_session(ctx(), "i-abc")
    assert made.command().startswith("aws ssm start-session ")
    assert "i-abc" in made.command()


def test_the_recorder_keeps_the_last_outcome() -> None:
    rec = Recorder()
    assert rec.last is None
    first = rec.run(Handoff("true", ["true"], needs=("true",)))
    second = rec.run(Handoff("false", ["false"], needs=("false",)))
    assert rec.entries == [first, second]
    assert rec.last is second


def test_an_outcome_with_an_error_is_never_ok() -> None:
    assert not Outcome("x", returncode=0, error="boom").ok
    assert Outcome("x", returncode=0).ok
    assert not Outcome("x", returncode=None).ok
