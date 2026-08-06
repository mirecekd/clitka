# src/clitka/core/ddbql.py
"""PartiQL against DynamoDB: run a statement, get rows back.

The read half of M5's DynamoDB work, and deliberately the *first* half: the owner's
call was "jen partiql, builder prozatim odsuneme". A key-condition builder is a
screen with operators and GSI selection; PartiQL is a string the user already knows
how to write, and `execute_statement` does the parsing. So this is the whole engine
for `clitka dynamodb ql` and for the console screen.

The row shape and the statement arithmetic live in `core/ddbqlmodel.py` (the 8 kB
rule, and it is the boto3-free half); this file is the call itself.

Measured against `sw-sandbox`/`eu-central-1` before this was written
(`/tmp/clitka-ddb-poc/`, Q8 and Q3), because two things here are not what the API
reference implies:

1. **Paging is `NextToken`, a string** - *not* the `LastEvaluatedKey` dict that
   `scan` and `query` use. Same service, same page-through idea, different mechanism.
2. **An empty page does not mean the end** - see `run()`, which is where that
   finding turns into a loop that would otherwise have been wrong.

A write statement (INSERT / UPDATE / DELETE) goes through `require_write`, because
PartiQL is the one place in CLITKA where a *typed string* can mutate data.
"""

from __future__ import annotations

from typing import Any

from clitka.core.context import Context
from clitka.core.ddbqlmodel import (
    MAX_PAGES,
    MAX_ROWS,
    WRITE_VERBS,
    Page,
    columns_of,
    flatten,
    is_write,
)
from clitka.core.errors import wrap_aws_errors

__all__ = [
    "MAX_PAGES",
    "MAX_ROWS",
    "WRITE_VERBS",
    "Page",
    "columns_of",
    "flatten",
    "is_write",
    "run",
]


@wrap_aws_errors
def _execute(ctx: Context, statement: str, token: str | None, limit: int) -> dict[str, Any]:
    """One `ExecuteStatement` call. Every AWS error is translated by the decorator."""
    kwargs: dict[str, Any] = {"Statement": statement}
    if token:
        kwargs["NextToken"] = token
    if limit > 0:
        # `Limit` caps what is *evaluated*, not what is returned (PoC Q3), so it is
        # a cost control and never the reason a page looks short.
        kwargs["Limit"] = limit
    return ctx.client("dynamodb").execute_statement(**kwargs)


def run(
    ctx: Context,
    statement: str,
    max_rows: int = MAX_ROWS,
    start_token: str | None = None,
) -> Page:
    """Run a PartiQL statement, following `NextToken` until the answer or a cap.

    **An empty page does not mean the end.** A statement whose filter matches
    nothing on the current page still comes back with a `NextToken`, and the same is
    true of `scan` (PoC Q3, where it was measured). So the loop is driven by the
    token alone - `if not items: break` would report "no rows" for a table whose
    matches all sit further along, and that is exactly the bug this note prevents.

    Hence two caps. `max_rows` bounds what is *collected*; `MAX_PAGES` bounds the
    *requests*, because a highly selective statement can return empty page after
    empty page and would otherwise walk an entire table one page at a time.
    """
    text = statement.strip()
    if not text:
        raise ValueError("no statement given")
    if is_write(text):
        ctx.require_write(f"run a PartiQL {text.split(None, 1)[0].lower()} statement")

    rows: list[dict[str, Any]] = []
    token = start_token
    requests = 0
    capped = False

    while True:
        raw = _execute(ctx, text, token, max_rows)
        requests += 1
        rows.extend(flatten(item) for item in raw.get("Items", []))
        token = raw.get("NextToken")
        if not token:
            break
        if len(rows) >= max_rows:
            capped = True
            break
        if requests >= MAX_PAGES:
            # Out of requests but not out of answer: still capped, and the token is
            # kept so the caller could resume.
            capped = True
            break

    return Page(
        rows=rows[:max_rows],
        statement=text,
        next_token=token,
        requests=requests,
        capped=capped or len(rows) > max_rows,
    )


def _self_check() -> None:
    """The call and its loop. The row shape is `ddbqlmodel`'s own check."""

    class FakeClient:
        """Two pages where the FIRST is empty - the shape that broke the naive loop."""

        def __init__(self) -> None:
            self.calls: list[str | None] = []
            self.limits: list[int | None] = []

        def execute_statement(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs.get("NextToken"))
            self.limits.append(kwargs.get("Limit"))
            if kwargs.get("NextToken") is None:
                return {"Items": [], "NextToken": "page2"}
            return {"Items": [{"pk": {"S": "found"}}]}

    class FakeCtx:
        read_only = False

        def __init__(self) -> None:
            self.fake = FakeClient()

        def client(self, _service: str) -> Any:
            return self.fake

        def require_write(self, _what: str) -> None:
            raise AssertionError("a SELECT must not consult require_write")

    ctx = FakeCtx()
    found = run(ctx, '  SELECT * FROM "t"  ')  # type: ignore[arg-type]
    assert found.count == 1, "an empty first page must not end the walk"
    assert found.rows[0] == {"pk": "found"}
    assert ctx.fake.calls == [None, "page2"], ctx.fake.calls
    assert found.requests == 2 and not found.capped
    assert found.next_token is None
    # The statement is stored stripped - a screen echoes it back.
    assert found.statement == 'SELECT * FROM "t"'
    assert ctx.fake.limits == [MAX_ROWS, MAX_ROWS]

    # A write statement must reach require_write, with the verb in the message.
    class RefusingCtx(FakeCtx):
        def require_write(self, what: str) -> None:
            raise PermissionError(what)

    try:
        run(RefusingCtx(), "DELETE FROM t WHERE pk='a'")  # type: ignore[arg-type]
        raise AssertionError("a DELETE must be refused in read-only mode")
    except PermissionError as exc:
        assert "delete" in str(exc), exc

    for bad in ("", "   ", "\n\t "):
        try:
            run(FakeCtx(), bad)  # type: ignore[arg-type]
            raise AssertionError("an empty statement must be refused")
        except ValueError:
            pass

    # The request cap must stop a statement that pages for ever.
    class Endless(FakeCtx):
        def client(self, _service: str) -> Any:
            class Never:
                def execute_statement(self, **_kw: Any) -> dict[str, Any]:
                    return {"Items": [], "NextToken": "more"}

            return Never()

    forever = run(Endless(), 'SELECT * FROM "t"')  # type: ignore[arg-type]
    assert forever.requests == MAX_PAGES and forever.capped
    assert forever.next_token == "more", "a resumable token must be kept"

    # The row cap: one page that overshoots must be trimmed AND marked.
    class Flood(FakeCtx):
        def client(self, _service: str) -> Any:
            class Many:
                def execute_statement(self, **_kw: Any) -> dict[str, Any]:
                    return {"Items": [{"pk": {"S": str(i)}} for i in range(10)]}

            return Many()

    small = run(Flood(), 'SELECT * FROM "t"', max_rows=4)  # type: ignore[arg-type]
    assert small.count == 4 and small.capped, small
    # No token came back, so it is complete-but-trimmed rather than resumable.
    assert small.next_token is None

    print("[OK] ddbql self-check passed")


if __name__ == "__main__":
    _self_check()
