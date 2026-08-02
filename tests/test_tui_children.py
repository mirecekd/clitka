"""Sub-branches under a resource: the fix for "I cannot click on any of the tasks".

The owner's report (2026-08-01). Cloud Control has no `AWS::ECS::Task`, so the tree
could never fill such a branch and the F9 action only *printed* the tasks - printed
text has no cursor, so `x` and the task's own F9 entries were unreachable from the
TUI. `core/lister.ChildLister` plus `tui/childload.ChildLoader` are the answer.

The end-to-end test is the one that matters: open a cluster leaf, open its `Tasks`
sub-branch, put the cursor on a task and assert `selected_ref()` really is that task
- because that ref is what `x` and F9 consume.
"""

from __future__ import annotations

import pytest

from clitka.core import cloudcontrol as cc
from clitka.core import lister as ls
from clitka.core.actions import ResourceRef
from clitka.core.context import Context, Identity
from clitka.tui.app import ClitkaApp
from clitka.tui.childload import ChildLoader
from clitka.tui.childmodel import ChildNode
from clitka.tui.restree import ResourceTree
from clitka.tui.treemodel import ResourceNode

CLUSTER = "AWS::ECS::Cluster"
TASK = "AWS::ECS::Task"
TASK_ARN = "arn:aws:ecs:eu-central-1:1:task/prod/abc123def456"


@pytest.fixture
def ctx(monkeypatch):
    ident = Identity(account="123456789012", arn="arn:aws:iam::1:user/m", user_id="A")
    monkeypatch.setattr(Context, "identity_or_none", lambda _self: ident)
    return Context(profile="demo", region="eu-central-1")


@pytest.fixture
def listed(monkeypatch):
    """One ECS cluster on the `AWS::ECS::Cluster` branch, nothing anywhere else."""

    def fake(_ctx, type_name, *_a, **_kw):
        if type_name == CLUSTER:
            yield cc.Resource(CLUSTER, "prod", {"ClusterName": "prod"})

    monkeypatch.setattr(cc, "iter_resources", fake)


def task(identifier: str = TASK_ARN, name: str = "api  abc123def456") -> cc.Resource:
    return cc.Resource(TASK, identifier, {"Name": name, "Cluster": "prod", "exec": "ready"})


@pytest.fixture
def tasks_lister(monkeypatch):
    """A `Tasks` sub-branch on a cluster, and a record of every call."""
    calls: list[str] = []

    def fake_list(_ctx, ref):
        calls.append(ref.identifier)
        return [task()]

    lister = ls.ChildLister(
        id="test.tasks",
        label="Tasks",
        child_type=TASK,
        list=fake_list,
        applies_to=lambda ref: ref.type_name == CLUSTER,
    )
    monkeypatch.setattr(ls, "registered", lambda: [lister])
    return calls


async def _tree(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()
    assert isinstance(app.screen, ResourceTree)
    return app.screen


async def _settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


def _seek(screen, wanted: str) -> int:
    """The cursor line whose node carries `wanted` as its identifier."""
    for index, line in enumerate(screen.rtree._tree_lines):
        data = line.path[-1].data
        if isinstance(data, ResourceNode) and data.resource.identifier == wanted:
            return index
    raise AssertionError(f"{wanted} is not in the tree")


def _sub_branch(screen):
    """The first `ChildNode` node in the tree - the `Tasks` sub-branch."""
    for line in screen.rtree._tree_lines:
        if isinstance(line.path[-1].data, ChildNode):
            return line.path[-1]
    raise AssertionError("no sub-branch in the tree")


async def _open_cluster(app, pilot, screen):
    """Open the ECS cluster branch, then the `prod` leaf underneath it."""
    screen.add_type(CLUSTER)
    await _settle(app, pilot)
    screen.rtree.cursor_line = _seek(screen, "prod")
    await pilot.pause()
    await pilot.press("enter")
    await _settle(app, pilot)


# --- the model ------------------------------------------------------------


def test_a_resource_that_nothing_lists_children_of_stays_a_plain_leaf(ctx):
    """No fold arrow where there is nothing to fold out - an empty one is a lie."""
    bucket = ResourceNode("AWS::S3::Bucket", cc.Resource("AWS::S3::Bucket", "b", {}))

    class Host(ChildLoader):
        def registered_listers(self):
            return []

    assert Host().has_child_listers(bucket) is False


def test_the_fold_arrow_is_decided_per_type_and_cached(tasks_lister):
    """`BranchLoader._page` asks for every one of up to 2000 leaves."""
    calls = 0

    class Host(ChildLoader):
        def registered_listers(self):
            nonlocal calls
            calls += 1
            return ls.registered()

    host = Host()
    node = ResourceNode(CLUSTER, cc.Resource(CLUSTER, "prod", {}))
    assert host.has_child_listers(node) is True
    assert host.has_child_listers(node) is True
    assert calls == 1, "the second ask must come from the cache"


# --- the tree -------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_cluster_leaf_can_be_opened_and_grows_a_tasks_sub_branch(ctx, listed, tasks_lister):
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = await _tree(app, pilot)
        screen.add_type(CLUSTER)
        await _settle(app, pilot)

        line = screen.rtree._tree_lines[_seek(screen, "prod")]
        leaf = line.path[-1]
        # The whole bug in one assertion: without this the leaf could not be opened.
        assert leaf.allow_expand is True

        screen.rtree.cursor_line = _seek(screen, "prod")
        await pilot.pause()
        await pilot.press("enter")
        await _settle(app, pilot)

        assert leaf.is_expanded
        kids = [child.data for child in leaf.children]
        assert [one.lister.id for one in kids if isinstance(one, ChildNode)] == ["test.tasks"]
        # Nothing was listed yet - opening the leaf costs no API call.
        assert tasks_lister == []


@pytest.mark.asyncio
async def test_opening_the_sub_branch_lists_the_tasks(ctx, listed, tasks_lister):
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = await _tree(app, pilot)
        await _open_cluster(app, pilot, screen)

        await pilot.press("down", "enter")  # onto `Tasks`, then open it
        await _settle(app, pilot)

        assert tasks_lister == ["prod"], "the lister is called with the parent ref"
        branch = _sub_branch(screen)
        assert "(1)" in str(branch.label)
        assert isinstance(branch.data, ChildNode) and branch.data.count == 1


@pytest.mark.asyncio
async def test_a_task_under_the_cluster_is_a_real_selectable_resource(ctx, listed, tasks_lister):
    """The point of the whole change: `x` and F9 need a ref, and now they get one."""
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = await _tree(app, pilot)
        await _open_cluster(app, pilot, screen)
        await pilot.press("down", "enter")
        await _settle(app, pilot)

        screen.rtree.cursor_line = _seek(screen, TASK_ARN)
        await pilot.pause()

        ref = screen.selected_ref()
        assert ref is not None
        assert ref.type_name == TASK
        assert ref.identifier == TASK_ARN
        # And the row carries what the shell handoff reads off it.
        assert ref.row["Cluster"] == "prod"
        from clitka.tui.shellhost import task_and_cluster

        assert task_and_cluster(ref) == (TASK_ARN, "prod")


@pytest.mark.asyncio
async def test_a_task_leaf_leads_with_its_name_not_the_arn(ctx, listed, tasks_lister):
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = await _tree(app, pilot)
        await _open_cluster(app, pilot, screen)
        await pilot.press("down", "enter")
        await _settle(app, pilot)
        label = str(screen.rtree._tree_lines[_seek(screen, TASK_ARN)].path[-1].label)
        assert label.startswith("api  abc123def456"), label


@pytest.mark.asyncio
async def test_an_empty_sub_branch_says_none_rather_than_looking_broken(ctx, listed, monkeypatch):
    lister = ls.ChildLister(
        "test.tasks", "Tasks", TASK, lambda _c, _r: [], applies_to=lambda r: r.type_name == CLUSTER
    )
    monkeypatch.setattr(ls, "registered", lambda: [lister])

    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = await _tree(app, pilot)
        await _open_cluster(app, pilot, screen)
        await pilot.press("down", "enter")
        await _settle(app, pilot)
        assert "(none)" in str(_sub_branch(screen).label)


@pytest.mark.asyncio
async def test_a_failing_lister_keeps_the_error_on_the_sub_branch(ctx, listed, monkeypatch):
    """A denial must reach the user, exactly as on a type branch. F5 is the retry."""

    def denied(_ctx, _ref):
        raise cc.ClitkaError("AccessDenied: ecs:ListTasks")

    lister = ls.ChildLister(
        "test.tasks", "Tasks", TASK, denied, applies_to=lambda r: r.type_name == CLUSTER
    )
    monkeypatch.setattr(ls, "registered", lambda: [lister])

    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = await _tree(app, pilot)
        await _open_cluster(app, pilot, screen)
        await pilot.press("down", "enter")
        await _settle(app, pilot)
        branch = _sub_branch(screen)
        assert "[ERROR]" in str(branch.label) and "AccessDenied" in str(branch.label)
        assert "[ERROR]" in screen.title_text


@pytest.mark.asyncio
async def test_reopening_a_sub_branch_does_not_refetch(ctx, listed, tasks_lister):
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = await _tree(app, pilot)
        await _open_cluster(app, pilot, screen)
        await pilot.press("down", "enter")
        await _settle(app, pilot)
        await pilot.press("enter", "enter")  # fold, unfold
        await _settle(app, pilot)
        assert tasks_lister == ["prod"], tasks_lister


@pytest.mark.asyncio
async def test_a_sub_branch_is_not_a_resource_so_f9_has_nothing_to_act_on(
    ctx, listed, tasks_lister
):
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = await _tree(app, pilot)
        await _open_cluster(app, pilot, screen)
        await pilot.press("down")  # onto the `Tasks` node itself
        await pilot.pause()
        assert screen.selected_ref() is None
        # But its heading still says what it is about, for ActionHost's sake.
        assert screen.type_name == TASK


@pytest.mark.asyncio
async def test_f5_forgets_the_sub_branches_too(ctx, listed, tasks_lister):
    """F5 throws the nodes away, so `expanded_children` must go with them."""
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        screen = await _tree(app, pilot)
        await _open_cluster(app, pilot, screen)
        await pilot.press("f5")
        await _settle(app, pilot)
        for branch in screen.rtree.root.children:
            assert not branch.is_expanded
            assert not any(isinstance(child.data, ChildNode) for child in branch.children)


def test_the_self_checks_pass():
    from clitka.tui import childload, childmodel, treekeys

    ls._self_check()
    childmodel._self_check()
    childload._self_check()
    treekeys._self_check()


def test_the_bindings_still_reach_the_screen_after_the_split():
    """`treekeys.py` was carved out of `restree.py` for the 8 kB rule."""
    from clitka.tui import treekeys

    assert ResourceTree.BINDINGS is treekeys.TREE_BINDINGS
    assert "Tree:focus" in ResourceTree.DEFAULT_CSS


def test_a_broken_applies_to_never_empties_the_list():
    def explode(_ref):
        raise RuntimeError("broken")

    good = ls.ChildLister("a", "A", TASK, lambda _c, _r: [])
    bad = ls.ChildLister("b", "B", TASK, lambda _c, _r: [], applies_to=explode)
    ref = ResourceRef.from_row(CLUSTER, {"identifier": "prod"})
    assert [one.id for one in ls.available([bad, good], ref)] == ["a"]
