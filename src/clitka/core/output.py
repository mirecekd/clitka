"""One output contract for the whole CLI: table on a TTY, JSON when piped."""

from __future__ import annotations

import datetime as _dt
import json
import sys
from collections.abc import Sequence
from enum import StrEnum
from typing import Any

import yaml
from rich.console import Console
from rich.table import Table

console = Console()
err_console = Console(stderr=True)


class OutputFormat(StrEnum):
    AUTO = "auto"

    TABLE = "table"
    JSON = "json"
    YAML = "yaml"

    def resolve(self) -> OutputFormat:
        if self is not OutputFormat.AUTO:
            return self
        return OutputFormat.TABLE if sys.stdout.isatty() else OutputFormat.JSON


def _jsonable(value: Any) -> Any:
    """Make AWS responses serialisable: datetimes, Decimals, bytes, sets."""
    if isinstance(value, _dt.datetime | _dt.date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, set | frozenset):
        return sorted(_jsonable(v) for v in value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if hasattr(value, "__float__") and type(value).__name__ == "Decimal":
        return float(value)
    return value


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, _dt.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, dict | list | tuple):
        return json.dumps(_jsonable(value), separators=(",", ":"))
    return str(value)


def render(
    rows: Sequence[dict[str, Any]],
    fmt: OutputFormat = OutputFormat.AUTO,
    columns: Sequence[str] | None = None,
    title: str | None = None,
) -> None:
    """Print a list of records in the requested format."""
    resolved = fmt.resolve()
    if resolved is OutputFormat.JSON:
        console.print_json(json.dumps(_jsonable(list(rows))))
        return
    if resolved is OutputFormat.YAML:
        console.print(yaml.safe_dump(_jsonable(list(rows)), sort_keys=False).rstrip())
        return

    cols = list(columns) if columns else sorted({k for row in rows for k in row})
    table = Table(title=title, header_style="bold", show_lines=False)
    for col in cols:
        table.add_column(col, overflow="fold")
    for row in rows:
        table.add_row(*(_cell(row.get(col)) for col in cols))
    if not rows:
        console.print("(no results)")
        return
    console.print(table)


def render_one(
    record: dict[str, Any],
    fmt: OutputFormat = OutputFormat.AUTO,
    title: str | None = None,
) -> None:
    """Print a single record: key/value table, or the raw document."""
    resolved = fmt.resolve()
    if resolved is OutputFormat.JSON:
        console.print_json(json.dumps(_jsonable(record)))
        return
    if resolved is OutputFormat.YAML:
        console.print(yaml.safe_dump(_jsonable(record), sort_keys=False).rstrip())
        return
    table = Table(title=title, show_header=False, box=None)
    table.add_column("key", style="bold")
    table.add_column("value", overflow="fold")
    for key, value in record.items():
        table.add_row(key, _cell(value))
    console.print(table)


def _self_check() -> None:
    from decimal import Decimal

    payload = {
        "when": _dt.datetime(2026, 1, 2, 3, 4, 5),
        "num": Decimal("1.5"),
        "tags": {"b", "a"},
        "blob": b"hi",
    }
    out = _jsonable(payload)
    assert out["when"] == "2026-01-02T03:04:05", out
    assert out["num"] == 1.5, out
    assert out["tags"] == ["a", "b"], out
    assert out["blob"] == "hi", out
    assert json.dumps(out)
    assert _cell(None) == "-"
    assert _cell(True) == "yes"
    print("[OK] output self-check passed")


if __name__ == "__main__":
    _self_check()
