"""Where CLITKA remembers what it was doing: `~/.local/state/clitka/state.toml`.

Deliberately a *different file* from `config.toml`, and the distinction is the
whole point of this module:

- **`~/.config/clitka/config.toml`** holds what the user chose on purpose - the
  default profile, the explorer's branches, the default time window. CLITKA only
  writes it when asked (`clitka ctx use`, or `C` in the TUI).
- **`~/.local/state/clitka/state.toml`** holds what the app noticed by itself:
  the profile and region in force when it last exited, so the next run starts
  where the last one stopped. XDG calls this "state": data that persists between
  restarts but is neither a setting the user edits nor a cache that can be thrown
  away without being noticed.

Writing here therefore does **not** break the standing rule that `P` / `R` / `W`
are session-only - they still never touch `config.toml`. And it only happens at
all when `config.remember_last` is on, so the default behaviour is unchanged.

ponytail: it reuses `clitkaconfig`'s tiny TOML emitter instead of adding a
writer of its own. Ceiling: the same flat-table limitation. Upgrade path: the one
named in `clitkaconfig`.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from clitka.core.clitkaconfig import dumps
from clitka.core.errors import ConfigError

_FILENAME = "state.toml"


def state_dir() -> Path:
    """State directory, honouring CLITKA_STATE_DIR and XDG_STATE_HOME.

    `~/.local/state` is the XDG default and is what this is - not
    `~/.local/share` (that is user data worth backing up) and not the cache.
    """
    override = os.environ.get("CLITKA_STATE_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "state"
    return base / "clitka"


def state_path() -> Path:
    return state_dir() / _FILENAME


@dataclass(frozen=True)
class ClitkaState:
    """Where the last session left off. Every field is optional."""

    last_profile: str | None = None
    last_region: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    def with_values(self, **changes: Any) -> ClitkaState:
        return replace(self, **{k: v for k, v in changes.items() if v is not None})


_TYPES: dict[str, type] = {"last_profile": str, "last_region": str}


def load(path: Path | None = None) -> ClitkaState:
    """Read the state; a missing or unreadable file yields defaults.

    Unlike the config, a broken state file is **not** an error: it records
    something the user never typed, so refusing to start over it would be
    hostile. A bad value is simply forgotten.
    """
    target = path or state_path()
    if not target.is_file():
        return ClitkaState()
    try:
        raw = tomllib.loads(target.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return ClitkaState()
    clean = {k: v for k, v in raw.items() if k in _TYPES and isinstance(v, _TYPES[k])}
    return ClitkaState(**clean)


def save(state: ClitkaState, path: Path | None = None) -> Path:
    """Write the state atomically (temp file + replace), 0600."""
    target = path or state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_text(dumps(state.as_dict(), "CLITKA session state"), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise ConfigError(f"cannot write {target}: {exc}") from exc
    return target


def remember(profile: str | None, region: str | None, path: Path | None = None) -> ClitkaState:
    """Record where this session ended. Read-modify-write, so nothing is lost."""
    state = load(path).with_values(last_profile=profile, last_region=region)
    save(state, path)
    return state


def _self_check() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / _FILENAME
        assert load(target) == ClitkaState()

        remember("sw-sandbox", "eu-central-1", target)
        again = remember(None, "eu-west-1", target)
        # A None leaves the old value alone - that is what read-modify-write buys.
        assert again.last_profile == "sw-sandbox", again
        assert again.last_region == "eu-west-1", again
        assert load(target) == again
        assert oct(target.stat().st_mode)[-3:] == "600"

        # A corrupt state file is forgotten, not fatal: the user never typed it.
        target.write_text("this is not toml at all", encoding="utf-8")
        assert load(target) == ClitkaState()
        # ...and so is a value of the wrong type.
        target.write_text("last_profile = 7\n", encoding="utf-8")
        assert load(target) == ClitkaState()

    # The XDG default, and both overrides.
    old = {k: os.environ.get(k) for k in ("CLITKA_STATE_DIR", "XDG_STATE_HOME")}
    try:
        os.environ.pop("CLITKA_STATE_DIR", None)
        os.environ["XDG_STATE_HOME"] = "/tmp/xdg-state"
        assert state_dir() == Path("/tmp/xdg-state/clitka"), state_dir()
        os.environ.pop("XDG_STATE_HOME")
        assert state_dir() == Path.home() / ".local" / "state" / "clitka"
        os.environ["CLITKA_STATE_DIR"] = "/tmp/override"
        assert state_path() == Path("/tmp/override/state.toml")
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    print("[OK] clitkastate self-check passed")


if __name__ == "__main__":
    _self_check()
