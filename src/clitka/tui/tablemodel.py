"""Filtering and sorting for the resource table - pure logic, no Textual.

Kept separate from the widget so the behaviour that actually matters (what the
user sees after typing in the filter box) is testable without a screen.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

Row = dict[str, Any]


def cell_text(value: Any) -> str:
    """The string a cell shows - also what the filter matches against."""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list | tuple):
        return ", ".join(cell_text(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}={cell_text(v)}" for k, v in value.items())
    return str(value)


def matches(row: Row, needle: str, columns: Sequence[str] | None = None) -> bool:
    """Case-insensitive substring match across the visible columns.

    ponytail: plain substring matching, not fuzzy and not regex. Ceiling: no
    "foo AND bar"; upgrade path is to split the needle on spaces and require all
    terms, or accept `/re:` prefixed patterns.
    """
    if not needle:
        return True
    lowered = needle.lower()
    keys = columns if columns is not None else list(row)
    return any(lowered in cell_text(row.get(key)).lower() for key in keys)


def sort_key(value: Any) -> tuple[int, float, str]:
    """Order numbers numerically, everything else as text, None last."""
    if value is None or value == "":
        return (2, 0.0, "")
    if isinstance(value, bool):
        return (0, float(value), "")
    if isinstance(value, int | float):
        return (0, float(value), "")
    text = cell_text(value)
    try:
        return (0, float(text), "")
    except ValueError:
        return (1, 0.0, text.lower())


@dataclass
class TableModel:
    """All rows, the active filter and the active sort; yields the visible rows."""

    columns: list[str] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    filter_text: str = ""
    sort_column: str | None = None
    sort_descending: bool = False

    def set_rows(self, rows: Sequence[Row], columns: Sequence[str] | None = None) -> None:
        self.rows = list(rows)
        if columns is not None:
            self.columns = list(columns)
        elif not self.columns:
            seen: list[str] = []
            for row in self.rows:
                for key in row:
                    if key not in seen:
                        seen.append(key)
            self.columns = seen

    def extend(self, rows: Sequence[Row]) -> None:
        """Append a further page of results."""
        self.rows.extend(rows)

    def toggle_sort(self, column: str) -> None:
        """Sort by `column`; sorting by the same column again reverses it."""
        if self.sort_column == column:
            self.sort_descending = not self.sort_descending
        else:
            self.sort_column = column
            self.sort_descending = False

    def visible(self) -> list[Row]:
        out = [row for row in self.rows if matches(row, self.filter_text, self.columns)]
        if self.sort_column:
            column = self.sort_column
            out.sort(key=lambda row: sort_key(row.get(column)), reverse=self.sort_descending)
        return out

    def status(self) -> str:
        """A short "23/128 rows" style summary for the table header."""
        shown = len(self.visible())
        total = len(self.rows)
        text = f"{shown}/{total} rows" if shown != total else f"{total} rows"
        if self.sort_column:
            text += f" | sort: {self.sort_column}{' desc' if self.sort_descending else ''}"
        if self.filter_text:
            text += f" | filter: {self.filter_text}"
        return text


def _self_check() -> None:
    model = TableModel()
    model.set_rows(
        [
            {"name": "beta", "size": 10, "tags": ["a", "b"]},
            {"name": "alpha", "size": 2, "tags": None},
            {"name": "gamma", "size": 100, "tags": {"env": "prod"}},
        ]
    )
    assert model.columns == ["name", "size", "tags"], model.columns

    model.toggle_sort("size")
    assert [r["size"] for r in model.visible()] == [2, 10, 100]
    model.toggle_sort("size")
    assert [r["size"] for r in model.visible()] == [100, 10, 2]

    model.sort_column = None
    model.filter_text = "prod"  # matches inside the dict cell
    assert [r["name"] for r in model.visible()] == ["gamma"]
    model.filter_text = "AL"  # case-insensitive
    assert [r["name"] for r in model.visible()] == ["alpha"]
    assert "1/3 rows" in model.status()

    model.filter_text = ""
    model.toggle_sort("tags")  # None sorts last
    assert model.visible()[-1]["name"] == "alpha"
    print("[OK] table model self-check passed")


if __name__ == "__main__":
    _self_check()
