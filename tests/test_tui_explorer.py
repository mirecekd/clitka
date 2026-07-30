"""Explorer screen and `:` palette. Cloud Control is stubbed, never called."""

from __future__ import annotations

import pytest

from clitka.core import cloudcontrol as cc
from clitka.core.context import Context, Identity
from clitka.tui.app import ClitkaApp
from clitka.tui.explorer import COMMON_TYPES, MAX_ROWS, PAGE_ROWS, ExplorerScreen
from clitka.tui.picker import CommandPalette, rank
from clitka.tui.table import ResourceTable

RESOURCES = [
    cc.Resource("AWS::S3::Bucket", "one", {"BucketName": "one", "Arn": "arn:1"}),
    cc.Resource("AWS::S3::Bucket", "two", {"BucketName": "two", "Arn": "arn:2"}),
]


@pytest.fixture
def ctx(monkeypatch):
    ident = Identity(account="123456789012", arn="arn:aws:iam::1:user/m", user_id="A")
    monkeypatch.setattr(Context, "identity_or_none", lambda _self: ident)
    return Context(profile="demo", region="eu-central-1")


@pytest.fixture
def listed(monkeypatch):
    """Make cloudcontrol.iter_resources yield canned data."""
    calls = []

    def fake(_ctx, type_name, *_a, **_kw):
        calls.append(type_name)
        yield from RESOURCES

    monkeypatch.setattr(cc, "iter_resources", fake)
    return calls


def many(count: int):
    """A generator factory for `count` synthetic buckets."""

    def fake(_ctx, type_name, *_a, **_kw):
        for index in range(count):
            yield cc.Resource(type_name, f"b{index:05d}", {"BucketName": f"b{index:05d}"})

    return fake


# --- palette ranking ------------------------------------------------------


def test_rank_prefers_prefix_matches():
    types = ["AWS::S3::Bucket", "AWS::S3::AccessPoint", "AWS::Lambda::Function"]
    assert rank(types, "aws::s3::a") == ["AWS::S3::AccessPoint"]
    assert rank(types, "bucket") == ["AWS::S3::Bucket"]
    assert rank(types, "") == types
    assert rank(types, "nope") == []


def test_rank_respects_the_limit():
    assert len(rank([f"AWS::X::T{i}" for i in range(50)], "aws", limit=7)) == 7


def test_common_types_are_sane():
    assert "AWS::S3::Bucket" in COMMON_TYPES
    assert all(name.startswith("AWS::") for name in COMMON_TYPES)


# --- explorer screen ------------------------------------------------------


@pytest.mark.asyncio
async def test_explorer_loads_resources_into_the_table(ctx, listed):
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        await app.push_screen(ExplorerScreen(ctx, "AWS::S3::Bucket"))
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        screen = app.screen
        table = screen.query_one(ResourceTable)
        assert table.model.columns[0] == "identifier"
        assert [row["identifier"] for row in table.model.visible()] == ["one", "two"]
        assert screen.selected()["identifier"] == "one"
        assert listed == ["AWS::S3::Bucket"]


@pytest.mark.asyncio
async def test_explorer_shows_the_error_instead_of_swallowing_it(ctx, monkeypatch):
    def boom(*_a, **_kw):
        raise cc.AdditionalInputsError("AWS::EC2::Subnet", "Missing property: VpcId")

    monkeypatch.setattr(cc, "iter_resources", boom)
    app = ClitkaApp(ctx)

    async with app.run_test() as pilot:
        await app.push_screen(ExplorerScreen(ctx, "AWS::EC2::Subnet"))
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        title = app.screen.title_text
        assert "[ERROR]" in title
        assert "VpcId" in title
        assert app.screen.query_one(ResourceTable).model.rows == []


@pytest.mark.asyncio
async def test_explorer_reload_refetches(ctx, listed):
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        await app.push_screen(ExplorerScreen(ctx, "AWS::S3::Bucket"))
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.press("f5")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert listed == ["AWS::S3::Bucket", "AWS::S3::Bucket"]


@pytest.mark.asyncio
async def test_escape_leaves_the_explorer(ctx, listed):
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        await app.push_screen(ExplorerScreen(ctx, "AWS::S3::Bucket"))
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert isinstance(app.screen, ExplorerScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ExplorerScreen)


# --- palette wiring -------------------------------------------------------


@pytest.mark.asyncio
async def test_colon_opens_the_palette_and_enter_opens_the_explorer(ctx, listed):
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press(":")
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)
        await pilot.press(*"lambda")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, ExplorerScreen)
        assert app.screen.type_name == "AWS::Lambda::Function"
        assert listed == ["AWS::Lambda::Function"]


@pytest.mark.asyncio
async def test_palette_escape_opens_nothing(ctx, listed):
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press(":")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ExplorerScreen)
        assert listed == []


@pytest.mark.asyncio
async def test_palette_accepts_a_type_that_is_not_in_the_list(ctx, listed):
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press(":")
        await pilot.pause()
        for char in "AWS::Custom::Thing":
            await pilot.press(char if char != ":" else "colon")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert listed == ["AWS::Custom::Thing"]


# --- lazy paging ----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_page_is_shown_before_the_listing_finishes(ctx, monkeypatch):
    """The table must fill as pages arrive, not only once everything is in."""
    monkeypatch.setattr(cc, "iter_resources", many(PAGE_ROWS * 2 + 5))
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = ExplorerScreen(ctx, "AWS::S3::Bucket")
        await app.push_screen(screen)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert len(screen.resources) == PAGE_ROWS * 2 + 5
        assert "205 resources" in screen.title_text
        assert "loading" not in screen.title_text
        table = screen.query_one(ResourceTable)
        assert len(table.model.rows) == PAGE_ROWS * 2 + 5


@pytest.mark.asyncio
async def test_the_first_page_sets_the_columns(ctx, monkeypatch):
    monkeypatch.setattr(cc, "iter_resources", many(PAGE_ROWS + 1))
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = ExplorerScreen(ctx, "AWS::S3::Bucket")
        await app.push_screen(screen)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        columns = screen.query_one(ResourceTable).model.columns
        assert columns[0] == "identifier"
        assert "BucketName" in columns


@pytest.mark.asyncio
async def test_listing_stops_at_the_display_limit(ctx, monkeypatch):
    monkeypatch.setattr(cc, "iter_resources", many(MAX_ROWS + PAGE_ROWS * 3))
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = ExplorerScreen(ctx, "AWS::S3::Bucket")
        await app.push_screen(screen)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert len(screen.resources) == MAX_ROWS
        assert "display limit" in screen.title_text


@pytest.mark.asyncio
async def test_an_empty_type_is_reported_as_zero_not_as_loading(ctx, monkeypatch):
    monkeypatch.setattr(cc, "iter_resources", many(0))
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = ExplorerScreen(ctx, "AWS::S3::Bucket")
        await app.push_screen(screen)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert screen.resources == []
        assert "0 resources" in screen.title_text
        assert "loading" not in screen.title_text


@pytest.mark.asyncio
async def test_a_failure_midway_through_still_reaches_the_heading(ctx, monkeypatch):
    def half_then_boom(_ctx, type_name, *_a, **_kw):
        for index in range(PAGE_ROWS + 3):
            yield cc.Resource(type_name, f"b{index}", {})
        raise RuntimeError("Throttling")

    monkeypatch.setattr(cc, "iter_resources", half_then_boom)
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = ExplorerScreen(ctx, "AWS::S3::Bucket")
        await app.push_screen(screen)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "[ERROR]" in screen.title_text
        assert "Throttling" in screen.title_text
