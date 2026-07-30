"""Resource table: filter/sort logic and the widget behaviour under run_test()."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Input

from clitka.tui.table import ResourceTable
from clitka.tui.tablemodel import TableModel, cell_text, matches, sort_key

ROWS = [
    {"name": "beta", "size": 10, "state": "running"},
    {"name": "alpha", "size": 2, "state": "stopped"},
    {"name": "gamma", "size": 100, "state": "running"},
]


class TableApp(App[None]):
    def compose(self) -> ComposeResult:
        yield ResourceTable()


# --- pure logic -----------------------------------------------------------


def test_cell_text_renders_aws_shapes():
    assert cell_text(None) == "-"
    assert cell_text(True) == "yes"
    assert cell_text(["a", "b"]) == "a, b"
    assert cell_text({"env": "prod"}) == "env=prod"


def test_matches_is_case_insensitive_and_covers_all_columns():
    row = {"name": "MyBucket", "tags": {"env": "prod"}}
    assert matches(row, "mybu")
    assert matches(row, "PROD")
    assert matches(row, "") is True
    assert not matches(row, "nope")


def test_matches_only_looks_at_given_columns():
    row = {"name": "a", "hidden": "secret"}
    assert not matches(row, "secret", columns=["name"])


def test_sort_key_orders_numbers_then_text_then_none():
    values = [None, "banana", 5, "10", ""]
    ordered = sorted(values, key=sort_key)
    assert ordered[:3] == [5, "10", "banana"]
    assert ordered[-2:] == [None, ""] or ordered[-2:] == ["", None]


def test_model_sort_toggles_direction():
    model = TableModel()
    model.set_rows(ROWS)
    model.toggle_sort("name")
    assert [r["name"] for r in model.visible()] == ["alpha", "beta", "gamma"]
    model.toggle_sort("name")
    assert [r["name"] for r in model.visible()] == ["gamma", "beta", "alpha"]


def test_model_status_line():
    model = TableModel()
    model.set_rows(ROWS)
    assert model.status() == "3 rows"
    model.filter_text = "running"
    model.toggle_sort("size")
    status = model.status()
    assert "2/3 rows" in status and "sort: size" in status and "filter: running" in status


def test_model_extend_appends():
    model = TableModel()
    model.set_rows(ROWS)
    model.extend([{"name": "delta", "size": 1, "state": "running"}])
    assert len(model.rows) == 4


# --- widget ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_table_renders_rows_and_columns():
    app = TableApp()
    async with app.run_test() as pilot:
        table = app.query_one(ResourceTable)
        table.set_rows(ROWS)
        await pilot.pause()
        data = app.query_one(DataTable)
        assert data.row_count == 3
        assert [str(c.label) for c in data.columns.values()] == ["name", "size", "state"]
        assert "3 rows" in table.model.status()


@pytest.mark.asyncio
async def test_slash_opens_filter_and_filters_rows():
    app = TableApp()
    async with app.run_test() as pilot:
        table = app.query_one(ResourceTable)
        table.set_rows(ROWS)
        await pilot.pause()
        app.query_one(DataTable).focus()
        await pilot.press("/")
        await pilot.pause()
        assert app.query_one(Input).has_class("visible")
        await pilot.press("g", "a", "m")
        await pilot.pause()
        assert app.query_one(DataTable).row_count == 1
        assert table.selected_row()["name"] == "gamma"

        await pilot.press("escape")
        await pilot.pause()
        assert app.query_one(DataTable).row_count == 3
        assert not app.query_one(Input).has_class("visible")


@pytest.mark.asyncio
async def test_sorting_marks_the_column_and_reverses():
    app = TableApp()
    async with app.run_test() as pilot:
        table = app.query_one(ResourceTable)
        table.set_rows(ROWS)
        await pilot.pause()
        table.sort_by("size")
        await pilot.pause()
        assert [r["size"] for r in table.model.visible()] == [2, 10, 100]
        labels = [str(c.label) for c in app.query_one(DataTable).columns.values()]
        assert "size ^" in labels
        table.sort_by("size")
        await pilot.pause()
        assert [r["size"] for r in table.model.visible()] == [100, 10, 2]
        labels = [str(c.label) for c in app.query_one(DataTable).columns.values()]
        assert "size v" in labels


@pytest.mark.asyncio
async def test_selected_row_returns_the_original_dict():
    app = TableApp()
    async with app.run_test() as pilot:
        table = app.query_one(ResourceTable)
        table.set_rows(ROWS)
        await pilot.pause()
        selected = table.selected_row()
        assert selected is not None
        assert selected is ROWS[0] or selected == ROWS[0]


@pytest.mark.asyncio
async def test_empty_table_has_no_selection():
    app = TableApp()
    async with app.run_test() as pilot:
        table = app.query_one(ResourceTable)
        table.set_rows([], columns=["name"])
        await pilot.pause()
        assert table.selected_row() is None


@pytest.mark.asyncio
async def test_add_rows_appends_a_page():
    app = TableApp()
    async with app.run_test() as pilot:
        table = app.query_one(ResourceTable)
        table.set_rows(ROWS)
        await pilot.pause()
        table.add_rows([{"name": "delta", "size": 1, "state": "running"}])
        await pilot.pause()
        assert app.query_one(DataTable).row_count == 4
