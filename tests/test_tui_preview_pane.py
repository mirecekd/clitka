"""The preview pane and the clitka_previews hook, driven through the tree.

The owner's rule under test: the pane is filled by enter/click only, never by
moving the cursor, so holding the down arrow costs no API calls.
"""

from __future__ import annotations

import pytest

from clitka.core import cloudcontrol as cc
from clitka.core import preview as pv
from clitka.core.actions import ResourceRef
from clitka.core.context import Context, Identity
from clitka.tui.app import ClitkaApp
from clitka.tui.preview import BUILDING, PreviewPane, core_tabs, slug
from clitka.tui.restree import ResourceTree

TYPE = "AWS::S3::Bucket"
BUCKETS = [
    cc.Resource(TYPE, "bucket-one", {"Arn": "arn:aws:s3:::bucket-one"}),
    cc.Resource(TYPE, "bucket-two", {"Arn": "arn:aws:s3:::bucket-two"}),
]


@pytest.fixture
def offline(monkeypatch):
    ident = Identity(account="123456789012", arn="arn:aws:iam::1:user/mirek", user_id="A")
    monkeypatch.setattr(Context, "identity_or_none", lambda _self: ident)
    monkeypatch.setattr(cc, "iter_resources", lambda *_a, **_k: iter(BUCKETS))
    return Context(profile="demo", region="eu-central-1")


async def _open_first_bucket(pilot, app):
    """Expand the one branch and put the cursor on its first resource."""
    await pilot.pause()
    await pilot.press("enter")  # expand the type
    await app.workers.wait_for_complete()
    await pilot.pause()
    await pilot.press("down")  # onto bucket-one
    await pilot.pause()


# --- core tabs ------------------------------------------------------------


def test_core_tabs_are_overview_and_raw_and_need_no_api_call():
    tabs = core_tabs()
    assert [tab.id for tab in tabs] == [pv.OVERVIEW, pv.RAW]
    assert all(tab.lazy is False for tab in tabs), "core tabs must not need a worker"


def test_core_tabs_build_from_the_row_the_tree_already_has():
    ref = ResourceRef.from_row(TYPE, BUCKETS[0].row())
    overview, raw = core_tabs()
    assert "bucket-one" in overview.build(None, ref)
    assert "arn:aws:s3:::bucket-one" in overview.build(None, ref)
    assert raw.build(None, ref).startswith("TypeName: AWS::S3::Bucket")


# --- driven through the tree ---------------------------------------------


@pytest.mark.asyncio
async def test_the_pane_starts_empty(offline):
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        app.push_screen(ResourceTree(offline, [TYPE]))
        await pilot.pause()
        pane = app.screen.query_one(PreviewPane)
        assert pane.ref is None
        assert pane.query_one("#body-0-empty") is not None
        assert pane.body_text(pv.OVERVIEW) == ""


@pytest.mark.asyncio
async def test_moving_the_cursor_does_not_fill_the_pane(offline):
    """The owner's explicit call: no preview on cursor movement."""
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        app.push_screen(ResourceTree(offline, [TYPE]))
        await _open_first_bucket(pilot, app)
        pane = app.screen.query_one(PreviewPane)
        assert pane.ref is None, "the cursor moved but nothing was previewed"
        await pilot.press("down")
        await pilot.pause()
        assert pane.ref is None


@pytest.mark.asyncio
async def test_enter_on_a_resource_fills_the_pane(offline):
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        app.push_screen(ResourceTree(offline, [TYPE]))
        await _open_first_bucket(pilot, app)
        await pilot.press("enter")
        await pilot.pause()

        pane = app.screen.query_one(PreviewPane)
        assert pane.ref is not None
        assert pane.ref.identifier == "bucket-one"
        assert [tab.id for tab in pane.tabs] == [pv.OVERVIEW, pv.RAW]
        assert "bucket-one" in pane.body_text(pv.OVERVIEW)


@pytest.mark.asyncio
async def test_enter_on_a_type_still_folds_instead_of_previewing(offline):
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        app.push_screen(ResourceTree(offline, [TYPE]))
        await pilot.pause()
        screen = app.screen
        branch = screen.rtree.root.children[0]
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert branch.is_expanded
        await pilot.press("enter")
        await pilot.pause()
        assert not branch.is_expanded
        assert screen.query_one(PreviewPane).ref is None


@pytest.mark.asyncio
async def test_previewing_a_second_resource_rebuilds_the_tabs(offline):
    """The generation counter exists because clear_panes() removal is deferred."""
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        app.push_screen(ResourceTree(offline, [TYPE]))
        await _open_first_bucket(pilot, app)
        await pilot.press("enter")
        await pilot.pause()
        pane = app.screen.query_one(PreviewPane)
        first = pane.generation

        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        assert pane.ref.identifier == "bucket-two"
        assert pane.generation > first
        assert "bucket-two" in pane.body_text(pv.OVERVIEW)


@pytest.mark.asyncio
async def test_a_plugin_tab_is_offered_and_built_on_a_worker(offline, monkeypatch):
    calls: list[str] = []

    def build(_ctx, ref):
        calls.append(ref.identifier)
        return "the last events"

    # A dotted, namespaced id like a real plugin uses - Textual rejects the dot
    # in a widget id, which is exactly the bug this caught on a real log group.
    tab = pv.PreviewTab("logs.events", "Events", build, applies_to=pv.for_type(TYPE), lazy=True)
    monkeypatch.setattr(pv, "registered", lambda: [tab])

    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        app.push_screen(ResourceTree(offline, [TYPE]))
        await _open_first_bucket(pilot, app)
        await pilot.press("enter")
        await pilot.pause()
        pane = app.screen.query_one(PreviewPane)
        assert [t.id for t in pane.tabs] == [pv.OVERVIEW, pv.RAW, "logs.events"]
        # A lazy tab is not built until it is shown.
        assert calls == []

        pane.query_one("#preview-tabs").active = pane._tab_id("logs.events")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert calls == ["bucket-one"]
        assert pane.body_text("logs.events") == "the last events"


@pytest.mark.asyncio
async def test_a_failing_tab_shows_the_error_instead_of_crashing(offline, monkeypatch):
    def boom(_ctx, _ref):
        raise RuntimeError("AccessDenied: logs:FilterLogEvents")

    tab = pv.PreviewTab("bad", "Bad", boom, applies_to=pv.for_type(TYPE), lazy=False)
    monkeypatch.setattr(pv, "registered", lambda: [tab])

    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        app.push_screen(ResourceTree(offline, [TYPE]))
        await _open_first_bucket(pilot, app)
        await pilot.press("enter")
        await pilot.pause()
        pane = app.screen.query_one(PreviewPane)
        pane.query_one("#preview-tabs").active = pane._tab_id("bad")
        await pilot.pause()
        body = pane.body_text("bad")
        assert "[ERROR]" in body and "AccessDenied" in body


def test_a_lazy_tab_shows_a_placeholder_first():
    assert "loading" in BUILDING


def test_a_namespaced_tab_id_becomes_a_legal_widget_id():
    """Textual only allows letters, digits, '_' and '-' in an id.

    A plugin namespaces its tabs (`logs.events`), and the dot raised
    `BadIdentifier` the first time a real log group was previewed.
    """
    assert slug("logs.events") == "logs-events"
    assert slug("overview") == "overview"
    assert slug("a b.c:d") == "a-b-c-d"
    pane = PreviewPane(Context(region="eu-central-1"))
    for candidate in (pane._tab_id("logs.events"), pane._body_id("logs.events")):
        assert "." not in candidate
        assert all(char.isalnum() or char in "_-" for char in candidate), candidate


def test_pane_forgets_its_cache_when_the_context_changes():
    pane = PreviewPane(Context(region="eu-central-1"))
    pane.cache[("overview", TYPE, "b")] = "stale"
    pane.adopt_context(Context(region="eu-west-1"))
    assert pane.cache == {}
    assert pane.context.region == "eu-west-1"
