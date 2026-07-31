"""The resource tree: lazy expansion, labels, `:` adding a branch, F5, F9.

Cloud Control is stubbed - nothing here talks to AWS.
"""

from __future__ import annotations

import pytest
from textual.widgets import Tree

from clitka.core import cloudcontrol as cc
from clitka.core.context import Context, Identity
from clitka.tui.app import ClitkaApp
from clitka.tui.restree import ResourceTree
from clitka.tui.restypes import MAX_ROWS, PAGE_ROWS, TREE_TYPES
from clitka.tui.treemodel import ResourceNode, TypeNode, summarise

TYPES = ["AWS::S3::Bucket", "AWS::Lambda::Function"]


@pytest.fixture
def ctx(monkeypatch):
    ident = Identity(account="123456789012", arn="arn:aws:iam::1:user/m", user_id="A")
    monkeypatch.setattr(Context, "identity_or_none", lambda _self: ident)
    return Context(profile="demo", region="eu-central-1")


@pytest.fixture
def listed(monkeypatch):
    """Two resources per type, and a record of which types were asked for."""
    calls: list[str] = []

    def fake(_ctx, type_name, *_a, **_kw):
        calls.append(type_name)
        for name in ("one", "two"):
            yield cc.Resource(type_name, name, {"Arn": f"arn:{name}"})

    monkeypatch.setattr(cc, "iter_resources", fake)
    return calls


def many(count: int):
    def fake(_ctx, type_name, *_a, **_kw):
        for index in range(count):
            yield cc.Resource(type_name, f"r{index:05d}", {})

    return fake


async def _tree(app, pilot):
    """Wait until the landing tree is up, and hand it over."""
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()
    assert isinstance(app.screen, ResourceTree)
    return app.screen


def _branch(screen, index: int = 0):
    return list(screen.rtree.root.children)[index]


# --- the model ------------------------------------------------------------


def test_summarise_skips_the_identifier_and_empty_values():
    res = cc.Resource("T", "me", {"Name": "me", "Empty": "", "Blank": "{}", "Arn": "a"})
    assert summarise(res) == "Arn=a"


def test_a_leaf_without_an_identifier_still_has_a_label():
    assert ResourceNode("T", cc.Resource("T", "", {})).label() == "(no identifier)"


# --- the landing screen ---------------------------------------------------


@pytest.mark.asyncio
async def test_the_app_opens_on_the_tree_with_everything_folded(ctx, listed):
    """The landing screen is the type tree, and it fetches nothing by itself."""
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = await _tree(app, pilot)
        assert len(screen.rtree.root.children) == len(TREE_TYPES)
        assert all(not branch.is_expanded for branch in screen.rtree.root.children)
        assert listed == [], "nothing may be listed until a branch is opened"


@pytest.mark.asyncio
async def test_the_cursor_starts_on_the_first_branch_not_the_hidden_root(ctx, listed):
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = await _tree(app, pilot)
        assert screen.rtree.cursor_node is _branch(screen)


@pytest.mark.asyncio
async def test_enter_opens_a_type_and_loads_it(ctx, listed):
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = await _tree(app, pilot)
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        branch = _branch(screen)
        assert branch.is_expanded
        assert listed == [TREE_TYPES[0]]
        assert [str(child.label) for child in branch.children] == ["one", "two"] or len(
            branch.children
        ) == 2
        assert "(2)" in str(branch.label)


@pytest.mark.asyncio
async def test_enter_again_folds_it_and_keeps_what_was_loaded(ctx, listed):
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = await _tree(app, pilot)
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        branch = _branch(screen)

        await pilot.press("enter")
        await pilot.pause()
        assert not branch.is_expanded
        assert len(branch.children) == 2, "folding must not throw the resources away"

        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert branch.is_expanded
        assert listed == [TREE_TYPES[0]], "reopening must not refetch"


@pytest.mark.asyncio
async def test_space_toggles_too(ctx, listed):
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = await _tree(app, pilot)
        await pilot.press("space")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert _branch(screen).is_expanded


@pytest.mark.asyncio
async def test_each_branch_loads_on_its_own(ctx, listed):
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = await _tree(app, pilot)
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        # shift+down goes to the next sibling, skipping the resources.
        await pilot.press("shift+down", "enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert listed == [TREE_TYPES[0], TREE_TYPES[1]]
        assert _branch(screen, 1).is_expanded


@pytest.mark.asyncio
async def test_an_empty_type_says_none(ctx, monkeypatch):
    monkeypatch.setattr(cc, "iter_resources", many(0))
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = await _tree(app, pilot)
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "(none)" in str(_branch(screen).label)


@pytest.mark.asyncio
async def test_a_denied_type_keeps_the_error_on_the_branch(ctx, monkeypatch):
    def denied(*_a, **_kw):
        raise cc.ClitkaError("AccessDenied: cloudcontrol:ListResources")

    monkeypatch.setattr(cc, "iter_resources", denied)
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = await _tree(app, pilot)
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        branch = _branch(screen)
        assert "[ERROR]" in str(branch.label)
        assert "AccessDenied" in str(branch.label)
        assert "[ERROR]" in screen.title_text


@pytest.mark.asyncio
async def test_a_big_type_stops_at_the_display_limit(ctx, monkeypatch):
    monkeypatch.setattr(cc, "iter_resources", many(MAX_ROWS + PAGE_ROWS * 2))
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = await _tree(app, pilot)
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        data = _branch(screen).data
        assert isinstance(data, TypeNode)
        assert data.count == MAX_ROWS
        assert f"({MAX_ROWS}+)" in str(_branch(screen).label)


# --- `:` adds a branch ----------------------------------------------------


@pytest.mark.asyncio
async def test_the_palette_adds_a_new_branch_rather_than_leaving_the_tree(ctx, listed):
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = await _tree(app, pilot)
        before = len(screen.rtree.root.children)

        screen.add_type("AWS::Custom::Thing")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert isinstance(app.screen, ResourceTree), "it must stay on the tree"
        assert len(screen.rtree.root.children) == before + 1
        added = _branch(screen, before)
        assert "AWS::Custom::Thing" in str(added.label)
        assert added.is_expanded, "an added type is opened straight away"
        assert listed == ["AWS::Custom::Thing"]


@pytest.mark.asyncio
async def test_adding_a_type_that_is_already_there_just_jumps_to_it(ctx, listed):
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = await _tree(app, pilot)
        before = len(screen.rtree.root.children)
        screen.add_type(TREE_TYPES[1])
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert len(screen.rtree.root.children) == before
        assert _branch(screen, 1).is_expanded


# --- F5 and F9 ------------------------------------------------------------


@pytest.mark.asyncio
async def test_f5_forgets_everything_and_the_next_open_refetches(ctx, listed):
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = await _tree(app, pilot)
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        await pilot.press("f5")
        await pilot.pause()
        branch = _branch(screen)
        assert not branch.is_expanded
        assert str(branch.label) == TREE_TYPES[0], "the count is dropped too"

        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert listed == [TREE_TYPES[0], TREE_TYPES[0]]


@pytest.mark.asyncio
async def test_f9_has_nothing_to_offer_on_a_type_but_does_on_a_resource(ctx, listed):
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = await _tree(app, pilot)
        assert screen.selected_ref() is None, "a type branch is not a resource"

        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()

        ref = screen.selected_ref()
        assert ref is not None
        assert ref.type_name == TREE_TYPES[0]
        assert ref.identifier == "one"


@pytest.mark.asyncio
async def test_switching_the_profile_drops_what_was_loaded(ctx, listed, monkeypatch):
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = await _tree(app, pilot)
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        screen.adopt_context(ctx.with_profile("other"))
        await pilot.pause()
        branch = _branch(screen)
        assert not branch.is_expanded
        assert screen.context.profile == "other"
        assert str(branch.label) == TREE_TYPES[0]


def test_the_widget_is_reached_as_rtree_not_tree(ctx):
    """`Screen.tree` is Textual's own - shadowing it breaks the app at runtime."""
    assert "rtree" in vars(ResourceTree)
    assert "tree" not in vars(ResourceTree), "do not add a `tree` attribute here"
    # And enter really is claimed by us, because Textual's own enter does not
    # expand anything (it only posts NodeSelected).
    keys = [binding.key for binding in ResourceTree.BINDINGS]
    assert "enter" in keys
    assert "enter" in [binding.key for binding in Tree.BINDINGS]
