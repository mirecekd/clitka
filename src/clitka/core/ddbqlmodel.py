# src/clitka/core/ddbqlmodel.py
"""The boto3-free half of PartiQL: what a row looks like, and what a statement is.

Split out of `core/ddbql.py` for the 8 kB rule, and it is the usual good seam - this
is the half that can be tested without a stub: what a statement is, and what a page
of rows looks like.

The value codec underneath it is `core/ddbvalue.py` (split out for the same rule when
the unwrapping became recursive), which is where the measured facts about numbers and
binary values live.

`core/ddbql.py` re-exports everything here, and that is what callers import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from clitka.core.ddbvalue import unwrap

__all__ = [
    "MAX_PAGES",
    "MAX_ROWS",
    "WRITE_VERBS",
    "Page",
    "columns_of",
    "flatten",
    "is_write",
    "unwrap",
]

MAX_ROWS = 500
"""How many rows one `run()` collects before it stops asking for more.

ponytail: a fixed cap rather than user-driven paging. Ceiling: a SELECT over a big
table is answered head-only, and `Page.capped` says so instead of pretending it is
the whole answer. Upgrade path: hand `Page.next_token` back to the caller - `run`
already accepts one, so the API is there and only the UI is missing.
"""

MAX_PAGES = 20
"""A second cap, on *requests* rather than rows - see `ddbql.run()` for why both."""

# The statement verbs that change data. PartiQL for DynamoDB has no DDL (no CREATE
# TABLE), so this list is closed rather than a prefix heuristic that might grow.
WRITE_VERBS = ("insert", "update", "delete")


def is_write(statement: str) -> bool:
    """Whether this statement would change data.

    Leading whitespace and case are ignored; anything that is not one of the three
    write verbs is treated as a read. That direction is the safe one: a misjudged
    *read* only means `require_write` is not consulted for something that cannot
    write anyway, whereas guessing "read" for a write would skip the check.
    """
    first = statement.strip().split(None, 1)
    return bool(first) and first[0].lower() in WRITE_VERBS


@dataclass(frozen=True)
class Page:
    """The outcome of one `run()`: rows, and whether that is all of them."""

    rows: list[dict[str, Any]]
    statement: str
    next_token: str | None = None
    """Where a later call would resume. `None` means the answer is complete."""
    requests: int = 1
    """How many `ExecuteStatement` calls this took - the cost, in one number."""
    capped: bool = False
    """True when a cap stopped the collection, so `rows` is a head, not the whole."""

    @property
    def count(self) -> int:
        return len(self.rows)

    def summary(self) -> str:
        """One line for a screen title or a CLI footer."""
        what = f"{self.count} row(s)"
        if self.capped:
            what += f" (capped at {MAX_ROWS} - the answer has more)"
        if self.requests > 1:
            what += f", {self.requests} requests"
        return what


def flatten(item: dict[str, Any]) -> dict[str, Any]:
    """One DynamoDB-JSON item as a flat, printable, JSON-safe row.

    `{"pk": {"S": "x"}, "n": {"N": "3"}}` becomes `{"pk": "x", "n": "3"}`, and the
    unwrapping goes all the way down - see `unwrap`, which is where the reasoning
    about precision and binary values lives.
    """
    return {name: unwrap(value) for name, value in item.items()}


def columns_of(rows: list[dict[str, Any]], first: tuple[str, ...] = ()) -> list[str]:
    """Every column present in `rows`, with `first` leading where it exists.

    DynamoDB is schemaless, so two rows in one answer need not have the same
    attributes - the columns are the *union*, in first-seen order, or a row further
    down the page would lose the attribute that only it has.
    """
    seen: list[str] = [name for name in first if any(name in row for row in rows)]
    for row in rows:
        for name in row:
            if name not in seen:
                seen.append(name)
    return seen


def _self_check() -> None:
    import json

    # --- is_write: the whole point is that a typed string can mutate ----------
    assert is_write("INSERT INTO t VALUE {'pk': 'a'}")
    assert is_write("  update t SET x=1 WHERE pk='a'")
    assert is_write("DELETE FROM t WHERE pk='a'")
    assert not is_write('SELECT * FROM "t"')
    assert not is_write("") and not is_write("   ")
    # A word that merely starts with a write verb is not one of them.
    assert not is_write("updated_at FROM t")

    # --- flatten: the row shape (the codec is `ddbvalue`'s own check) ----------
    assert flatten({"pk": {"S": "a"}, "n": {"N": "3"}, "ok": {"BOOL": True}}) == {
        "pk": "a",
        "n": "3",
        "ok": True,
    }
    # Unwrapping goes all the way down - this is what a live run showed as
    # `[{"S":"acme:aud:orders"}]` before it was recursive.
    assert flatten({"l": {"L": [{"S": "a"}]}})["l"] == ["a"]
    assert json.dumps(flatten({"b": {"B": b"hi"}}))
    assert flatten({}) == {}

    # --- columns_of: schemaless means the union, not the first row ------------
    rows = [{"pk": "a", "x": 1}, {"pk": "b", "y": 2}]
    assert columns_of(rows) == ["pk", "x", "y"]
    assert columns_of(rows, first=("y",)) == ["y", "pk", "x"]
    # `first` names a column nothing has: it must not be invented.
    assert columns_of([{"a": 1}], first=("pk",)) == ["a"]
    assert columns_of([]) == []

    # --- Page ----------------------------------------------------------------
    assert Page(rows=[{"a": 1}], statement="SELECT").summary() == "1 row(s)"
    assert Page([], "S").count == 0
    assert "capped" in Page([], "S", capped=True).summary()
    assert "3 requests" in Page([], "S", requests=3).summary()

    print("[OK] ddbql model self-check passed")


if __name__ == "__main__":
    _self_check()
