"""CLITKA's own settings: ~/.config/clitka/config.toml.

Deliberately separate from `~/.aws/*`, which CLITKA only ever reads. Reading
uses stdlib `tomllib`; writing is a hand-rolled emitter because the document is
a single flat table.

ponytail: the writer only supports str / bool / int / float / list-of-str at the
top level and one `[table]` per nested dict. Ceiling: no arrays of tables, no
deep nesting. Upgrade path: depend on `tomli-w` and delete `dumps`.

`dumps` is public because `core/clitkastate.py` writes a second, sibling file
(`~/.local/state/clitka/state.toml`) and one 20-line emitter is enough for both.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from clitka.core.errors import ConfigError

_FILENAME = "config.toml"


def config_dir() -> Path:
    """Config directory, honouring CLITKA_CONFIG_DIR and XDG_CONFIG_HOME."""
    override = os.environ.get("CLITKA_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "clitka"


def config_path() -> Path:
    return config_dir() / _FILENAME


@dataclass(frozen=True)
class ClitkaConfig:
    """Persisted user preferences. Every field is optional.

    Everything here is a *deliberate* choice - nothing writes this file unless the
    user asked (`clitka ctx use`, or `C` in the TUI). Where the last session
    happened to be is a different question and lives in `core/clitkastate.py`.
    """

    profile: str | None = None
    region: str | None = None
    read_only: bool = False
    theme: str = "default"
    # The branches the explorer opens with. Empty means "use the built-in list" -
    # an empty tree is never what anyone wanted, so it is not a reachable state.
    tree_types: list[str] = field(default_factory=list)
    # The time window a session starts on, as a `core.timerange` label ("1h").
    default_window: str | None = None
    # Start where the last session stopped, by reading `state.toml`. Off by
    # default: a fresh install must behave exactly as it did before this existed.
    remember_last: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    def with_values(self, **changes: Any) -> ClitkaConfig:
        return replace(self, **{k: v for k, v in changes.items() if v is not None})


_TYPES: dict[str, type] = {
    "profile": str,
    "region": str,
    "read_only": bool,
    "theme": str,
    "tree_types": list,
    "default_window": str,
    "remember_last": bool,
}


def _coerce(raw: dict[str, Any]) -> ClitkaConfig:
    # Forward compatibility: ignore keys a newer CLITKA wrote.
    raw = {k: v for k, v in raw.items() if k in _TYPES}
    for key, value in raw.items():
        expected = _TYPES[key]
        if value is not None and not isinstance(value, expected):
            raise ConfigError(f"{config_path()}: '{key}' must be {expected.__name__}")
    return ClitkaConfig(**raw)


def load(path: Path | None = None) -> ClitkaConfig:
    """Read the config; a missing file yields defaults."""
    target = path or config_path()
    if not target.is_file():
        return ClitkaConfig()
    try:
        raw = tomllib.loads(target.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise ConfigError(f"cannot read {target}: {exc}") from exc
    return _coerce(raw)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def dumps(data: dict[str, Any], header: str = "") -> str:
    """Emit a flat TOML document. Shared with `core/clitkastate.py`."""
    scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
    tables = {k: v for k, v in data.items() if isinstance(v, dict)}
    title = header or "CLITKA configuration - managed by `clitka ctx use` and the C panel"
    lines = [f"# {title}", ""]

    lines += [f"{key} = {_toml_value(value)}" for key, value in scalars.items()]
    for name, table in tables.items():
        lines += ["", f"[{name}]"]
        lines += [f"{key} = {_toml_value(value)}" for key, value in table.items()]
    return "\n".join(lines) + "\n"


def save(cfg: ClitkaConfig, path: Path | None = None) -> Path:
    """Write the config atomically (temp file + replace), 0600."""
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_text(dumps(cfg.as_dict()), encoding="utf-8")

        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise ConfigError(f"cannot write {target}: {exc}") from exc
    return target


def update(path: Path | None = None, **changes: Any) -> ClitkaConfig:
    """Read-modify-write: only the given keys change, the rest is preserved."""
    cfg = load(path).with_values(**changes)
    save(cfg, path)
    return cfg


def _self_check() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "config.toml"
        assert load(target) == ClitkaConfig()
        update(target, profile="demo", region="eu-central-1")
        again = update(target, read_only=True)
        assert again.profile == "demo", again
        assert again.region == "eu-central-1", again
        assert again.read_only is True, again
        assert load(target) == again
        assert oct(target.stat().st_mode)[-3:] == "600"

        # The C-panel settings survive a round trip, list included.
        types = ["AWS::S3::Bucket", "AWS::Lambda::Function"]
        update(target, tree_types=types, default_window="3h", remember_last=True)
        back = load(target)
        assert back.tree_types == types, back
        assert back.default_window == "3h", back
        assert back.remember_last is True, back
        # ...and the older keys were not lost by the read-modify-write.
        assert back.profile == "demo", back

        # A key a newer CLITKA wrote is ignored, not fatal.
        target.write_text('profile = "demo"\nfrom_the_future = 1\n', encoding="utf-8")
        assert load(target).profile == "demo"
        # A key of the wrong type IS fatal - the user typed it, so say so.
        target.write_text("tree_types = 7\n", encoding="utf-8")
        try:
            load(target)
        except ConfigError:
            pass
        else:
            raise AssertionError("a wrongly-typed key must be reported")

    print("[OK] clitkaconfig self-check passed")


if __name__ == "__main__":
    _self_check()
