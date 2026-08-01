"""Handing the whole terminal to another program, and getting it back.

This is what M4 needs for `ssm start-session` and `ecs execute-command`: an
interactive shell on a remote host cannot live in a Textual widget, so the app
steps aside for the duration.

What the throwaway PoC established (2026-08-01, `sw-sandbox`,
`i-0ccaf6ccac8cce88a`, textual 8.2.8, session-manager-plugin 1.2.835.0):

- `App.suspend()` around a plain `subprocess.run()` is enough. The child gets the
  real TTY - a full-screen `vim` drew correctly, and an SSM session accepted
  typing and returned `exit 0` on `exit`. No pty plumbing, no `os.execvp`.
- Textual repaints itself on the way back, the event loop and the timers survive,
  and the app still quits normally afterwards. Nothing had to be reset by hand.
- A missing binary raises `FileNotFoundError` **inside** the suspend block, which
  would otherwise leave the app suspended - hence `missing()`, checked *before*
  suspending, and the belt-and-braces `try` in `run()`.
- Printing around the child is worth it: without a marker line the user cannot
  tell whether the handoff happened at all.

ponytail: we shell out to the `aws` CLI rather than reimplementing the Session
Manager websocket protocol. Ceiling: the `aws` CLI (v2) and
`session-manager-plugin` must be on PATH, and the profile is passed by name so
the child resolves its own credentials - which also means an expired SSO login
surfaces as the child's own error message, not ours. Upgrade path: boto3
`start_session` plus feeding the response to the plugin directly, which is what
the CLI does internally.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field

from clitka.core.context import Context

AWS = "aws"
PLUGIN = "session-manager-plugin"


@dataclass(frozen=True)
class Outcome:
    """How a handoff ended. `ok` means the child ran and was happy."""

    label: str
    returncode: int | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and self.returncode == 0

    def summary(self) -> str:
        if self.error:
            return f"{self.label}: {self.error}"
        if self.returncode:
            return f"{self.label}: exited with {self.returncode}"
        return f"{self.label}: finished"


@dataclass(frozen=True)
class Handoff:
    """A command that wants the whole terminal.

    Textual-free on purpose: the caller wraps `run()` in `app.suspend()`, and the
    tests call it with `argv=["true"]`. `needs` is checked *before* the caller
    suspends, because a `FileNotFoundError` in there would strand the app.
    """

    label: str
    argv: list[str]
    needs: tuple[str, ...] = (AWS,)
    note: str = ""

    def missing(self) -> list[str]:
        """The required binaries that are not on PATH."""
        return [name for name in self.needs if shutil.which(name) is None]

    def unavailable(self) -> str:
        """A ready-to-print complaint, or "" when the handoff can go ahead."""
        gone = self.missing()
        if not gone:
            return ""
        return f"{', '.join(gone)} is not on PATH - {INSTALL.get(gone[0], 'install it')}"

    def run(self) -> Outcome:
        """Run the child with the terminal it was promised. Never raises."""
        gone = self.unavailable()
        if gone:
            return Outcome(self.label, error=gone)
        try:
            # argv is built in this module from a Context, never from a shell string.
            done = subprocess.run(self.argv, check=False)
        except FileNotFoundError as exc:  # a `needs` we failed to declare
            return Outcome(self.label, error=f"{exc.filename} not found")
        except KeyboardInterrupt:
            # ctrl-c reached us rather than the child - treat it as "user left".
            return Outcome(self.label, returncode=130)
        except OSError as exc:
            return Outcome(self.label, error=str(exc))
        return Outcome(self.label, returncode=done.returncode)

    def command(self) -> str:
        """The command as a user could type it - for the log and the help text."""
        return " ".join(self.argv)


INSTALL = {
    AWS: "install the AWS CLI v2",
    PLUGIN: (
        "install it from "
        "https://docs.aws.amazon.com/systems-manager/latest/userguide/"
        "session-manager-working-with-install-plugin.html"
    ),
}


def _where(context: Context) -> list[str]:
    """The `--profile`/`--region` pair every `aws` call in here shares."""
    argv: list[str] = []
    if context.profile:
        argv += ["--profile", context.profile]
    region = context.effective_region
    if region:
        argv += ["--region", region]
    return argv


def ssm_session(context: Context, target: str, document: str = "") -> Handoff:
    """An interactive shell on an SSM-managed instance. Verified in the PoC."""
    argv = [AWS, "ssm", "start-session", *_where(context), "--target", target]
    if document:
        argv += ["--document-name", document]
    return Handoff(
        label=f"SSM session on {target}",
        argv=argv,
        needs=(AWS, PLUGIN),
        note="type `exit` to come back to CLITKA",
    )


def ssm_port_forward(context: Context, target: str, remote: int, local: int) -> Handoff:
    """Forward a local port to a port on the instance. Blocks until ctrl-c."""
    return Handoff(
        label=f"port {local} -> {target}:{remote}",
        argv=[
            AWS,
            "ssm",
            "start-session",
            *_where(context),
            "--target",
            target,
            "--document-name",
            "AWS-StartPortForwardingSession",
            "--parameters",
            f"portNumber={remote},localPortNumber={local}",
        ],
        needs=(AWS, PLUGIN),
        note="press ctrl-c to stop forwarding",
    )


def ecs_exec(
    context: Context,
    cluster: str,
    task: str,
    container: str = "",
    command: str = "/bin/sh",
) -> Handoff:
    """A shell inside a running ECS task (needs `enableExecuteCommand`)."""
    argv = [
        AWS,
        "ecs",
        "execute-command",
        *_where(context),
        "--cluster",
        cluster,
        "--task",
        task,
        "--interactive",
        "--command",
        command,
    ]
    if container:
        argv += ["--container", container]
    return Handoff(
        label=f"exec in {task.rsplit('/', 1)[-1]}",
        argv=argv,
        needs=(AWS, PLUGIN),
        note="the task must have been started with --enable-execute-command",
    )


@dataclass
class Recorder:
    """Where a screen collects what it handed over - handy for the status line."""

    entries: list[Outcome] = field(default_factory=list)

    def run(self, handoff: Handoff) -> Outcome:
        outcome = handoff.run()
        self.entries.append(outcome)
        return outcome

    @property
    def last(self) -> Outcome | None:
        return self.entries[-1] if self.entries else None


def _self_check() -> None:
    ctx = Context(profile="sw-sandbox", region="eu-central-1")

    session = ssm_session(ctx, "i-0ccaf6ccac8cce88a")
    # The profile and region must reach the child - it resolves its own creds.
    assert "--profile" in session.argv and "sw-sandbox" in session.argv, session.argv
    assert session.argv[:3] == [AWS, "ssm", "start-session"], session.argv
    assert PLUGIN in session.needs, session.needs

    # A context with neither profile nor region must not emit empty flags.
    bare = ssm_session(Context(), "i-1")
    assert "--profile" not in bare.argv and "--region" not in bare.argv, bare.argv

    forward = ssm_port_forward(ctx, "i-1", 5432, 15432)
    assert "portNumber=5432,localPortNumber=15432" in forward.argv, forward.argv

    exec_ = ecs_exec(ctx, "c", "arn:aws:ecs:eu-central-1:1:task/c/abc123", "app")
    assert exec_.argv[-2:] == ["--container", "app"], exec_.argv
    assert "--interactive" in exec_.argv
    assert exec_.label.endswith("abc123"), exec_.label

    # A missing binary is reported *before* anything is suspended, and running it
    # anyway is still safe.
    nope = Handoff("nope", ["clitka-no-such-binary"], needs=("clitka-no-such-binary",))
    assert nope.missing() == ["clitka-no-such-binary"]
    assert "not on PATH" in nope.unavailable()
    assert not nope.run().ok

    # And the happy path really runs a child process.
    rec = Recorder()
    good = rec.run(Handoff("true", ["true"], needs=("true",)))
    assert good.ok and "finished" in good.summary(), good
    assert rec.last is good and len(rec.entries) == 1

    bad = Handoff("false", ["false"], needs=("false",)).run()
    assert not bad.ok and "exited with 1" in bad.summary(), bad
    print("[OK] handoff self-check passed")


if __name__ == "__main__":
    _self_check()
