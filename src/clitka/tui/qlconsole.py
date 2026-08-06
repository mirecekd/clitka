# src/clitka/tui/qlconsole.py
"""`Q`: type a PartiQL statement, see the rows. The DynamoDB console.

**Why this is a screen key and not an F9 action.** An `Action` is a plain callable
that runs on a worker and returns a finished `ActionResult` - it has no way to ask
the user anything, and nothing in CLITKA's F9 menu ever has. A console is *entirely*
about typed input, so it belongs where `t` (live tail) and `x` (shell) already live:
a key on the resource screen. `t` set the precedent for the same reason - a live
tail never finishes, so it could not be an action either.

The input is `CommandPalette`, **unchanged**. It already dismisses with whatever was
typed when nothing in its candidate list matches - that is how `:` opens a type it
has never heard of, and how `W`'s `c` takes a duration - so a free-text prompt with
a few examples on offer needed no new widget. The rows land on `ResultScreen`, where
every other full-text answer in the app already goes.

The formatting is `tui/qltext.py` (the 8 kB rule, and it is the Textual-free half).

Mixed into a screen that has `context` and `selected_ref()` - the `ShellHost`
contract, so one mixin line buys the key.
"""

from __future__ import annotations

from clitka.core import actions as act
from clitka.core import ddbql
from clitka.core.context import Context
from clitka.tui.qltext import as_text, examples

TABLE_TYPE = "AWS::DynamoDB::Table"

PROMPT = "PartiQL - enter runs it, escape cancels"

# A statement left at one of these is the offered example, not a query. Running it
# would only be a ValidationException the user can learn nothing from.
UNFINISHED = ("where", "and", "or", "=", ",")

NOT_A_TABLE = """\
`Q` runs a PartiQL statement against a DynamoDB table.

Move the cursor onto a table first (the AWS::DynamoDB::Table branch), or press `:`
and open that type. The statement is then offered with the table already quoted in
it, which is what PartiQL needs for any name with a hyphen.

The same thing from a shell: `clitka dynamodb ql 'SELECT * FROM "my-table"'`.
"""


class QlConsole:
    """Mixed into a `Screen`. Expects `context`, `selected_ref()` and `_title()`."""

    context: Context

    def selected_ref(self) -> act.ResourceRef | None:  # pragma: no cover - overridden
        raise NotImplementedError

    def _title(self, text: str) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def action_query(self) -> None:
        """`Q`: ask for a statement, then run it off the UI thread."""
        from clitka.tui.picker import CommandPalette

        table = table_of(self.selected_ref())
        if table is None:
            self._ql_result("Q  PartiQL", NOT_A_TABLE)
            return

        self.app.push_screen(  # type: ignore[attr-defined]
            CommandPalette(examples(table), PROMPT), self._ql_typed
        )

    def _ql_typed(self, typed: object) -> None:
        """What the prompt came back with. A cancel is silent, a stub explains."""
        if not isinstance(typed, str) or not typed.strip():
            return
        statement = typed.strip()
        if statement.lower().rstrip().endswith(UNFINISHED):
            self._ql_result(
                "Q  PartiQL",
                f"[red]unfinished statement[/red]\n\n{statement}\n\n"
                "Add a condition after WHERE, or run the plain SELECT.",
            )
            return
        self._title("PartiQL - running...")
        self.run_worker(  # type: ignore[attr-defined]
            lambda: self._ql_run(statement), thread=True, exclusive=False, group="ql"
        )

    # Prefixed like `ViewEditHost`'s and `ShellHost`'s methods: a bare `_run` would
    # collide on a screen that mixes in four of these. That trap cost 21 tests once.
    def _ql_run(self, statement: str) -> None:
        try:
            page = ddbql.run(self.context, statement)
            title = f"PartiQL  {page.summary()}"
            body = as_text(page)
        except Exception as exc:
            from rich.markup import escape

            title = "PartiQL  failed"
            # AWS says "Statement wasn't well formed" and similar, which is already
            # written for a human - so it is shown verbatim rather than reworded.
            body = f"[red]{escape(str(exc))}[/red]\n\n{escape(statement)}"
        self.app.call_from_thread(self._ql_result, title, body)  # type: ignore[attr-defined]

    def _ql_result(self, title: str, body: str) -> None:
        from clitka.tui.resultview import ResultScreen

        self.app.push_screen(  # type: ignore[attr-defined]
            ResultScreen(self.context, act.ActionResult(title, body))
        )


def table_of(ref: act.ResourceRef | None) -> str | None:
    """The table `Q` would run against, or None when the cursor is not on one."""
    if ref is None or ref.type_name != TABLE_TYPE:
        return None
    # Cloud Control identifies a table by its name, so the identifier IS the table.
    return ref.identifier or str(ref.row.get("TableName", "")) or None


def _self_check() -> None:
    # --- which selection `Q` applies to --------------------------------------
    table = act.ResourceRef.from_row(TABLE_TYPE, {"identifier": "audience-resolution"})
    assert table_of(table) == "audience-resolution"
    assert table_of(None) is None
    assert table_of(act.ResourceRef.from_row("AWS::S3::Bucket", {"identifier": "b"})) is None
    # A table with no identifier but a TableName property still resolves.
    assert table_of(act.ResourceRef.from_row(TABLE_TYPE, {"TableName": "t"})) == "t"
    assert table_of(act.ResourceRef.from_row(TABLE_TYPE, {})) is None

    # --- the unfinished-example guard ----------------------------------------
    for stub in ('SELECT * FROM "t" WHERE ', 'SELECT * FROM "t" WHERE pk =', "a AND"):
        assert stub.lower().rstrip().endswith(UNFINISHED), stub
    # A real statement must NOT be caught by it.
    for good in ('SELECT * FROM "t"', "SELECT * FROM \"t\" WHERE pk='a'"):
        assert not good.lower().rstrip().endswith(UNFINISHED), good

    for name in ("action_query", "_ql_typed", "_ql_run"):
        assert callable(getattr(QlConsole, name)), name
    # Every mixin method is prefixed - the MRO collision that cost 21 tests once.
    plain = [n for n in vars(QlConsole) if not n.startswith(("_ql", "action_", "__"))]
    assert plain == ["selected_ref", "_title"] or all(
        n in ("selected_ref", "_title") for n in plain
    ), plain
    print("[OK] ql console self-check passed")


if __name__ == "__main__":
    _self_check()
