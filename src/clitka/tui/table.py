"""The generic resource table: one widget every service screen reuses.

Wraps Textual's `DataTable` and adds what CLITKA needs everywhere: a `/` filter
box, click/keyboard column sorting, a row count line, and a `selected_row()`
accessor that hands back the original dict (not the rendered strings) so actions
get real data.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Input, Static

from clitka.tui.tablemodel import Row, TableModel, cell_text


class ResourceTable(Vertical):
    """A filterable, sortable table of AWS resources."""

    DEFAULT_CSS = """
    ResourceTable Input.filter {
        display: none;
        border: none;
        height: 1;
        padding: 0 1;
    }
    ResourceTable Input.filter.visible {
        display: block;
    }
    ResourceTable .count {
        height: 1;
        color: $text-muted;
        padding: 0 1;
    }
    ResourceTable DataTable {
        height: 1fr;
    }
    """
    BINDINGS = [
        Binding("slash", "start_filter", "Filter", show=False),
        Binding("s", "sort_current_column", "Sort", show=False),
        Binding("escape", "clear_filter", "Clear filter", show=False),
    ]

    def __init__(self, columns: Sequence[str] | None = None) -> None:
        super().__init__()
        self.model = TableModel(columns=list(columns or []))

    def compose(self) -> ComposeResult:
        yield Input(placeholder="filter...", classes="filter")
        yield Static("", classes="count")
        table = DataTable(cursor_type="row", zebra_stripes=True)
        yield table

    def on_mount(self) -> None:
        self._sync()

    # --- data -------------------------------------------------------------

    def set_rows(self, rows: Sequence[Row], columns: Sequence[str] | None = None) -> None:
        self.model.set_rows(rows, columns)
        self._sync(rebuild_columns=True)

    def add_rows(self, rows: Sequence[Row]) -> None:
        """Append a further page without losing the cursor position."""
        self.model.extend(rows)
        self._sync()

    def selected_row(self) -> Row | None:
        """The original dict for the highlighted row, or None if empty."""
        visible = self.model.visible()
        table = self.query_one(DataTable)
        index = table.cursor_row
        if not visible or index is None or index < 0 or index >= len(visible):
            return None
        return visible[index]

    # --- rendering --------------------------------------------------------

    def _sync(self, rebuild_columns: bool = False) -> None:
        table = self.query_one(DataTable)
        if rebuild_columns or not table.columns:
            table.clear(columns=True)
            for column in self.model.columns:
                table.add_column(self._column_label(column), key=column)
        else:
            table.clear()
        for row in self.model.visible():
            table.add_row(*(cell_text(row.get(column)) for column in self.model.columns))
        self.query_one(".count", Static).update(self.model.status())

    def _column_label(self, column: str) -> str:
        if column != self.model.sort_column:
            return column
        return f"{column} {'v' if self.model.sort_descending else '^'}"

    def _relabel_columns(self) -> None:
        """Show the sort marker in the header without rebuilding the table."""
        table = self.query_one(DataTable)
        for key, column in table.columns.items():
            column.label = Text(self._column_label(str(key.value)))
        table.refresh()

    # --- actions ----------------------------------------------------------

    def sort_by(self, column: str) -> None:
        self.model.toggle_sort(column)
        self._sync()
        self._relabel_columns()

    def action_sort_current_column(self) -> None:
        table = self.query_one(DataTable)
        index = table.cursor_column
        if 0 <= index < len(self.model.columns):
            self.sort_by(self.model.columns[index])

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        self.sort_by(str(event.column_key.value))

    def action_start_filter(self) -> None:
        box = self.query_one(Input)
        box.add_class("visible")
        box.focus()

    def action_clear_filter(self) -> None:
        box = self.query_one(Input)
        box.value = ""
        box.remove_class("visible")
        self.model.filter_text = ""
        self._sync()
        self.query_one(DataTable).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.model.filter_text = event.value
        self._sync()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self.query_one(DataTable).focus()
