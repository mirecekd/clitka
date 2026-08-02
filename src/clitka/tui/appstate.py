"""`SessionMemory`: where a session starts, and where it is remembered.

Mixed into `ClitkaApp`. Split out of `tui/app.py` for the 8 kB rule, and the seam
is the same one `appswitch.py` sits on: `app.py` is the shell, this is one policy.

The policy, in one sentence: **`state.toml` is the weakest voice in the room.** It
may fill a gap the resolver had nothing for, and it may never beat a `--profile`
flag, `AWS_PROFILE`, or `config.toml`. Anything else would make "where I happened
to be last time" outrank something the user typed on purpose.

It is off unless `config.remember_last` is on, so a fresh install behaves exactly
as it did before this existed.
"""

from __future__ import annotations

from contextlib import suppress

from clitka.core import clitkaconfig, clitkastate
from clitka.core.context import Context

# What `Context.source` records for a value that came out of `state.toml`. The
# other four are botocore's: "flag" / "env" / "config" / "aws".
STATE = "state"


class SessionMemory:
    """Mixed into `ClitkaApp`. Needs `config` and `context`."""

    config: clitkaconfig.ClitkaConfig
    context: Context

    def opening_context(self) -> Context:
        """The context to start in: the resolved one, or where the last run ended."""
        context = Context.from_env()
        if not self.config.remember_last:
            return context
        state = clitkastate.load()
        # "aws" means nothing above botocore's own defaults spoke - the only gap
        # the state file is allowed to fill.
        if context.source.get("profile") == "aws" and state.last_profile:
            context = context.with_profile(state.last_profile)
            context.source["profile"] = STATE
        if context.source.get("region") == "aws" and state.last_region:
            context = context.with_region(state.last_region)
            context.source["region"] = STATE
        return context

    def remember_session(self) -> None:
        """On the way out, record the profile and region in force - if asked to.

        The config is re-read rather than trusted from memory: the `C` panel may
        have switched `remember_last` on or off while the app was running.
        """
        if not clitkaconfig.load().remember_last:
            return
        try:
            region = self.context.effective_region
        except Exception:
            # `effective_region` builds a boto3 session, which a broken profile
            # cannot do - and quitting must not fail over it.
            region = self.context.region
        # Nor may it fail because the state file could not be written.
        with suppress(Exception):
            clitkastate.remember(self.context.profile, region)


def _self_check() -> None:
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        old = {k: os.environ.get(k) for k in ("CLITKA_CONFIG_DIR", "CLITKA_STATE_DIR")}
        os.environ["CLITKA_CONFIG_DIR"] = tmp + "/config"
        os.environ["CLITKA_STATE_DIR"] = tmp + "/state"
        env_profile = os.environ.pop("AWS_PROFILE", None)
        try:
            memory = SessionMemory()

            # Off by default: nothing is read, and nothing is written.
            memory.config = clitkaconfig.ClitkaConfig()
            memory.context = Context(profile="live", region="eu-central-1")
            clitkastate.save(clitkastate.ClitkaState(last_profile="remembered"))
            assert memory.opening_context().profile != "remembered"
            memory.remember_session()
            assert clitkastate.load().last_profile == "remembered", "must not overwrite"

            # On, with nothing else to say: the state file fills the gap.
            memory.config = clitkaconfig.ClitkaConfig(remember_last=True)
            started = memory.opening_context()
            assert started.profile == "remembered", started
            assert started.source["profile"] == STATE, started.source

            # On, but the config names a profile: the config wins.
            clitkaconfig.save(clitkaconfig.ClitkaConfig(remember_last=True, profile="chosen"))
            memory.config = clitkaconfig.load()
            assert memory.opening_context().profile == "chosen"

            # And it writes on the way out.
            memory.context = Context(profile="last-one", region="eu-west-1")
            memory.remember_session()
            assert clitkastate.load().last_profile == "last-one"

            # A profile that cannot build a session must not stop the app quitting.
            memory.context = Context(profile="no-such-profile-exists")
            memory.remember_session()
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            if env_profile is not None:
                os.environ["AWS_PROFILE"] = env_profile

    print("[OK] session memory self-check passed")


if __name__ == "__main__":
    _self_check()
