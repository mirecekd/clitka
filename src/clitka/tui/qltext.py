# src/clitka/tui/qltext.py
"""How a PartiQL answer is turned into text - the Textual-free half of `Q`.

Split out of `tui/qlconsole.py` for the 8 kB rule, and it is the half worth testing
on its own: no screen, no app, no worker - rows in, a block of markup out.

The one rule encoded here is the escaping, and it is measured rather than assumed
(see `_self_check`): a value containing an unmatched **closing** tag like `[/close]`
raises `MarkupError` inside Rich and takes the result screen down. A stray `[red]`
merely disappears. Item data is exactly where such a string turns up, so every cell
goes through `escape` - the same lesson the `logs` and `s3` plugins already learned.
"""

from __future__ import annotations

import json

from clitka.core.ddbqlmodel import Page, columns_of

__all__ = ["as_text", "examples"]


def examples(table: str) -> list[str]:
    """The candidate statements the prompt opens with, for a named table.

    Quoted, always: PartiQL parses `FROM my-table` as a syntax error rather than a
    missing table, and every real table name on the owner's sandbox has a hyphen in
    it. Offering the quotes is cheaper than explaining them.
    """
    quoted = f'"{table}"'
    return [
        f"SELECT * FROM {quoted}",
        f"SELECT * FROM {quoted} WHERE ",
        f'SELECT * FROM {quoted}."index-name" WHERE ',
    ]


def _cell(value: object) -> str:
    """One value as one line. A dict or list is JSON, so a map stays readable."""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, dict | list):
        return json.dumps(value, separators=(",", ":"), default=str)
    return str(value)


def as_text(page: Page) -> str:
    """The rows as an aligned block, the shape every result body in CLITKA uses."""
    from rich.markup import escape

    if not page.rows:
        return f"[dim](no rows)[/dim]\n\n{escape(page.statement)}"

    columns = columns_of(page.rows)
    cells = [[escape(_cell(row.get(name))) for name in columns] for row in page.rows]
    heads = [escape(name) for name in columns]
    widths = [
        max(len(heads[col]), *(len(row[col]) for row in cells)) for col in range(len(columns))
    ]
    head = "  ".join(f"[b]{name:<{widths[col]}}[/b]" for col, name in enumerate(heads))
    body = ["  ".join(f"{cell:<{widths[col]}}" for col, cell in enumerate(row)) for row in cells]
    return "\n".join([head, *body])


def _self_check() -> None:
    from rich.markup import MarkupError, escape
    from rich.text import Text

    # --- the examples are quoted, or PartiQL rejects a hyphenated name --------
    for line in examples("my-table"):
        assert '"my-table"' in line, line
    assert examples("t")[0] == 'SELECT * FROM "t"'

    # --- the row block -------------------------------------------------------
    page = Page(rows=[{"pk": "a", "n": "3"}, {"pk": "bb"}], statement="SELECT")
    text = as_text(page)
    assert "pk" in text and "bb" in text
    # A row missing an attribute the other row has must show a placeholder, not
    # shift its columns along - DynamoDB is schemaless and that WILL happen.
    assert "-" in text.splitlines()[-1], text
    assert "(no rows)" in as_text(Page(rows=[], statement="SELECT"))

    # The escaping, measured rather than assumed: an unmatched CLOSING tag is what
    # actually raises, and item data is exactly where such a string turns up.
    try:
        Text.from_markup("value: [/close]")
        raise AssertionError("expected Rich to reject an unmatched closing tag")
    except MarkupError:
        pass
    nasty = as_text(Page(rows=[{"pk": "[/close]"}], statement="S"))
    Text.from_markup(nasty)  # must not raise - that is the whole point
    assert escape("[/close]") in nasty
    # The statement is echoed on the empty path, so it needs escaping too.
    Text.from_markup(as_text(Page(rows=[], statement="SELECT [/x]")))

    # A dict value stays readable rather than printing as a Python repr.
    assert '{"inner":"x"}' in as_text(Page(rows=[{"m": {"inner": "x"}}], statement="S"))
    assert "yes" in as_text(Page(rows=[{"ok": True}], statement="S"))
    # Alignment must survive a value that is wider than its own column header.
    wide = as_text(Page(rows=[{"a": "x" * 12}], statement="S"))
    assert "x" * 12 in wide

    print("[OK] ql text self-check passed")


if __name__ == "__main__":
    _self_check()
