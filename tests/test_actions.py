"""The action model and the F9 menu. AWS is stubbed, never called."""

from __future__ import annotations

import pytest

from clitka.core import cloudcontrol as cc
from clitka.core.actions import Action, ActionResult, ResourceRef, available, registered
from clitka.core.context import Context, Identity
from clitka.services.resources import actions as ra
from clitka.tui.actionmenu import ActionMenu, ConfirmModal
from clitka.tui.app import ClitkaApp
from clitka.tui.explorer import ExplorerScreen
from clitka.tui.resultview import ResultScreen

RESOURCES = [
    cc.Resource("AWS::S3::Bucket", "one", {"BucketName": "one"}),
    cc.Resource("AWS::S3::Bucket", "two", {"BucketName": "two"}),
]


@pytest.fixture
def ctx(monkeypatch):
    ident = Identity(account="123456789012", arn="arn:aws:iam::1:user/m", user_id="A")
    monkeypatch.setattr(Context, "identity_or_none", lambda _self: ident)
    return Context(profile="demo", region="eu-central-1")


@pytest.fixture
def listed(monkeypatch):
    monkeypatch.setattr(cc, "iter_resources", lambda *_a, **_kw: iter(RESOURCES))


async def _explorer(app, ctx, pilot):
    await app.push_screen(ExplorerScreen(ctx, "AWS::S3::Bucket"))
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()
    return app.screen


# --- the model -------------------------------------------------------------


def test_ref_takes_the_identifier_from_the_row():
    ref = ResourceRef.from_row("AWS::S3::Bucket", {"identifier": "one", "BucketName": "one"})
    assert (ref.type_name, ref.identifier) == ("AWS::S3::Bucket", "one")
    assert ref.row["BucketName"] == "one"


def test_available_filters_and_survives_a_broken_predicate():
    ref = ResourceRef("AWS::S3::Bucket", "one")

    def explode(_ref):
        raise RuntimeError("boom")

    keep = Action("keep", "Keep", lambda _c, _r: ActionResult("ok"))
    drop = Action("drop", "Drop", lambda _c, _r: ActionResult("ok"), applies_to=lambda _r: False)
    bad = Action("bad", "Bad", lambda _c, _r: ActionResult("ok"), applies_to=explode)
    assert [a.id for a in available([keep, drop, bad], ref)] == ["keep"]
    assert available([keep], None) == []


def test_menu_label_marks_destructive_actions_and_shows_the_key():
    action = Action("d", "Delete", lambda _c, _r: ActionResult("x"), key="d", destructive=True)
    assert action.menu_label().startswith("d  Delete")
    assert "destructive" in action.menu_label()


def test_the_resources_plugin_publishes_its_actions_through_the_hook():
    ids = [action.id for action in registered()]
    assert "resources.view_yaml" in ids
    assert "resources.delete" in ids
    delete = next(a for a in registered() if a.id == "resources.delete")
    assert delete.destructive


def test_view_yaml_uses_get_resource(monkeypatch, ctx):
    monkeypatch.setattr(
        cc,
        "get_resource",
        lambda _c, t, i: cc.Resource(t, i, {"BucketName": "one", "Tags": [{"Key": "a"}]}),
    )
    result = ra.view_yaml(ctx, ResourceRef("AWS::S3::Bucket", "one"))
    assert "BucketName: one" in result.body
    assert result.reload is False


def test_delete_action_asks_cloudcontrol_and_requests_a_reload(monkeypatch, ctx):
    seen = {}

    def fake(_c, type_name, identifier):
        seen["target"] = (type_name, identifier)
        return {"status": "IN_PROGRESS"}

    monkeypatch.setattr(cc, "delete_resource", fake)
    result = ra.delete(ctx, ResourceRef("AWS::S3::Bucket", "one"))
    assert seen["target"] == ("AWS::S3::Bucket", "one")
    assert result.reload is True


# --- the F9 menu -----------------------------------------------------------


@pytest.mark.asyncio
async def test_f9_opens_the_menu_for_the_selected_row(ctx, listed):
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = await _explorer(app, ctx, pilot)
        assert screen.selected_ref().identifier == "one"
        await pilot.press("f9")
        await pilot.pause()
        assert isinstance(app.screen, ActionMenu)
        assert "AWS::S3::Bucket one" in app.screen.heading()
        assert "resources.view_yaml" in [a.id for a in app.screen.actions]


@pytest.mark.asyncio
async def test_a_non_destructive_action_runs_and_shows_its_result(ctx, listed, monkeypatch):
    monkeypatch.setattr(cc, "get_resource", lambda _c, t, i: cc.Resource(t, i, {"BucketName": i}))
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        await _explorer(app, ctx, pilot)
        await pilot.press("f9")
        await pilot.pause()
        await pilot.press("y")  # the key of "View as YAML"
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, ResultScreen)
        assert "BucketName: one" in app.screen.result.body


@pytest.mark.asyncio
async def test_a_destructive_action_confirms_first_and_no_is_the_default(ctx, listed, monkeypatch):
    calls = []
    monkeypatch.setattr(cc, "delete_resource", lambda *a: calls.append(a) or {"status": "X"})
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        await _explorer(app, ctx, pilot)
        await pilot.press("f9")
        await pilot.pause()
        await pilot.press("d")  # the key of "Delete resource"
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press("enter")  # enter must NOT delete
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert calls == []


@pytest.mark.asyncio
async def test_a_confirmed_destructive_action_runs(ctx, listed, monkeypatch):
    calls = []

    def fake(_c, type_name, identifier):
        calls.append((type_name, identifier))
        return {"status": "IN_PROGRESS"}

    monkeypatch.setattr(cc, "delete_resource", fake)
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        await _explorer(app, ctx, pilot)
        await pilot.press("f9")
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert calls == [("AWS::S3::Bucket", "one")]


@pytest.mark.asyncio
async def test_a_failing_action_is_reported_in_the_heading(ctx, listed, monkeypatch):
    def boom(*_a, **_kw):
        raise cc.ClitkaError("AccessDenied: no cloudcontrol:GetResource")

    monkeypatch.setattr(cc, "get_resource", boom)
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = await _explorer(app, ctx, pilot)
        await pilot.press("f9")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "[ERROR]" in screen.title_text
        assert "AccessDenied" in screen.title_text
        assert not isinstance(app.screen, ResultScreen)


@pytest.mark.asyncio
async def test_escape_closes_the_menu_without_running_anything(ctx, listed, monkeypatch):
    monkeypatch.setattr(cc, "get_resource", lambda *_a, **_kw: pytest.fail("must not run"))
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        await _explorer(app, ctx, pilot)
        await pilot.press("f9")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, ExplorerScreen)


@pytest.mark.asyncio
async def test_result_screen_escape_returns_to_the_explorer(ctx, listed, monkeypatch):
    monkeypatch.setattr(cc, "get_resource", lambda _c, t, i: cc.Resource(t, i, {"BucketName": i}))
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        await _explorer(app, ctx, pilot)
        await pilot.press("f9")
        await pilot.pause()
        await pilot.press("i")  # show identifier
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.screen.result.body == "one"
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, ExplorerScreen)
