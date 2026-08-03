"""The S3 plugin: the F9 keys, the CLI, and the recursion in the real tree.

The test that matters most is the last one: open a bucket leaf, open its `Objects`
sub-branch, open a *prefix* underneath that, and assert the cursor lands on a real
`ResourceRef`. That is the whole point of M5 - a bucket you can walk into - and it
is the claim the ECS milestone taught not to trust without driving the app
("it is in the app" and "the user can get to it" are different claims).
"""

from __future__ import annotations

import pytest

from clitka.core import actions as act
from clitka.core import lister as ls
from clitka.core import s3
from clitka.core.actions import ResourceRef
from clitka.core.context import Context, Identity
from clitka.services.s3 import actions as s3actions
from clitka.services.s3 import listers as s3listers
from clitka.tui.app import ClitkaApp
from clitka.tui.childmodel import ChildNode
from clitka.tui.restree import ResourceTree
from clitka.tui.treemodel import ResourceNode

BUCKET = s3.BUCKET
PREFIX = s3.PREFIX
OBJECT = s3.OBJECT


@pytest.fixture
def ctx(monkeypatch):
    ident = Identity(account="123456789012", arn="arn:aws:iam::1:user/m", user_id="A")
    monkeypatch.setattr(Context, "identity_or_none", lambda _self: ident)
    return Context(profile="demo", region="eu-central-1")


# --- the plugin seam ------------------------------------------------------


def test_the_plugin_is_registered_and_brings_all_four_hooks():
    from clitka.core import plugins

    assert "clitka.services.s3" in plugins.BUILTIN_SERVICES
    assert "s3" in [name for name, _ in plugins.service_apps()]
    ids = {one.id for one in act.registered()}
    assert {"s3.contents", "s3.details", "s3.uri"} <= ids
    assert "s3.objects" in {one.id for one in ls.registered()}


def test_no_f9_key_collides_with_anything_that_applies_to_the_same_type():
    """The `ec2.details` lesson: keys are global per resource and nobody owns them.

    `resources.*` claims y/j/i/d on every type with an identifier and
    `ActionMenu.on_key` runs the FIRST match - which is how `d` for "Details" once
    meant "delete the instance".
    """
    for type_name in (BUCKET, PREFIX, OBJECT):
        ref = ResourceRef.from_row(type_name, {"identifier": "b/x"})
        keys = [one.key for one in act.available(act.registered(), ref)]
        assert len(keys) == len(set(keys)), f"{type_name} has a duplicate key: {keys}"


def test_contents_is_offered_on_things_that_hold_things_and_not_on_a_file():
    holds = ResourceRef.from_row(PREFIX, {"identifier": "b/logs/"})
    leaf = ResourceRef.from_row(OBJECT, {"identifier": "b/logs/a.txt"})
    assert "s3.contents" in [one.id for one in act.available(act.registered(), holds)]
    assert "s3.contents" not in [one.id for one in act.available(act.registered(), leaf)]


def test_nothing_this_plugin_offers_mutates_anything():
    """No download, no delete, no presign yet - and none of it behind one keystroke."""
    ours = [one for one in act.registered() if one.id.startswith("s3.")]
    assert ours and not any(one.destructive for one in ours)


# --- the rows -------------------------------------------------------------


def test_a_prefix_row_is_its_own_browse_argument():
    made = s3listers.prefix_resource(s3.Location("b", "logs/2026/"))
    assert made.identifier == "b/logs/2026/"
    # And the leaf leads with the last segment, not the whole path.
    assert made.name() == "2026/"


def test_an_object_row_carries_the_bucket_and_no_duplicate_identifier():
    obj = s3.S3Object(s3.Location("b", "logs/a.txt"), size=2048)
    made = s3listers.object_resource(obj)
    assert made.properties["Bucket"] == "b"
    assert "identifier" not in made.properties and "name" not in made.properties
    assert made.name() == "a.txt"


def test_a_capped_level_says_so_in_the_tree_rather_than_lying(ctx, monkeypatch):
    """A browser that shows 2000 of 50 000 keys without saying so is lying."""
    full = s3.Listing(s3.Location("big"))
    full.files = [s3.S3Object(s3.Location("big", f"k{n}")) for n in range(3)]
    full.capped = True
    monkeypatch.setattr(s3, "browse", lambda *_a, **_kw: full)

    rows = s3listers.list_children(ctx, ResourceRef.from_row(BUCKET, {"identifier": "big"}))
    assert len(rows) == 4, "the extra row is the 'there is more' notice"
    assert "more than" in rows[-1].properties["Name"]


# --- the CLI --------------------------------------------------------------


def test_ls_exits_1_when_the_listing_was_cut_short(ctx, monkeypatch):
    from typer.testing import CliRunner

    from clitka.services.s3.cli import app

    cut = s3.Listing(s3.Location("big"))
    cut.files = [s3.S3Object(s3.Location("big", "k1"))]
    cut.capped = True
    monkeypatch.setattr("clitka.services.s3.cli.s3.browse", lambda *_a, **_kw: cut)

    result = CliRunner().invoke(app, ["ls", "big"], obj={"context": ctx})
    assert result.exit_code == 1, result.output


def test_ls_is_happy_with_an_empty_level(ctx, monkeypatch):
    from typer.testing import CliRunner

    from clitka.services.s3.cli import app

    monkeypatch.setattr(
        "clitka.services.s3.cli.s3.browse", lambda *_a, **_kw: s3.Listing(s3.Location("empty"))
    )
    result = CliRunner().invoke(app, ["ls", "empty"], obj={"context": ctx})
    assert result.exit_code == 0, result.output


def test_get_prints_the_values_not_just_the_keys(ctx, monkeypatch):
    """The live bug: `render` iterates a dict as its keys - `render_one` does not."""
    from typer.testing import CliRunner

    from clitka.services.s3.cli import app

    monkeypatch.setattr(
        "clitka.services.s3.cli.s3.get_bucket",
        lambda *_a, **_kw: s3.Bucket("far", region="eu-north-1"),
    )
    result = CliRunner().invoke(app, ["get", "far", "-o", "json"], obj={"context": ctx})
    assert result.exit_code == 0, result.output
    assert "eu-north-1" in result.output, result.output


# --- the tree, end to end -------------------------------------------------


@pytest.fixture
def fake_bucket(monkeypatch):
    """One bucket holding `logs/`, which holds `2026/` and `a.txt`."""
    from clitka.core import cloudcontrol as cc

    def listed(_ctx, type_name, *_a, **_kw):
        if type_name == BUCKET:
            yield cc.Resource(BUCKET, "demo-bucket", {"BucketName": "demo-bucket"})

    monkeypatch.setattr(cc, "iter_resources", listed)

    tree = {
        "demo-bucket": (["demo-bucket/logs/"], []),
        "demo-bucket/logs/": (["demo-bucket/logs/2026/"], ["demo-bucket/logs/a.txt"]),
        "demo-bucket/logs/2026/": ([], ["demo-bucket/logs/2026/08.log"]),
    }

    def fake_browse(_ctx, identifier, limit=None):
        folders, files = tree.get(identifier, ([], []))
        where = s3.Location.parse(identifier)
        found = s3.Listing(where)
        found.folders = [s3.Location.parse(one) for one in folders]
        found.files = [s3.S3Object(s3.Location.parse(one), size=12) for one in files]
        return found

    monkeypatch.setattr(s3, "browse", fake_browse)
    return tree


async def _settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


def _node(screen, wanted: str):
    for index, line in enumerate(screen.rtree._tree_lines):
        data = line.path[-1].data
        if isinstance(data, ResourceNode) and data.resource.identifier == wanted:
            return index, line.path[-1]
    raise AssertionError(f"{wanted} is not in the tree")


async def _open(app, pilot, screen, identifier: str):
    """Open a resource leaf, then the `Objects` node it grows."""
    index, node = _node(screen, identifier)
    screen.rtree.cursor_line = index
    await pilot.pause()
    await pilot.press("enter")
    await _settle(app, pilot)
    for line_index, line in enumerate(screen.rtree._tree_lines):
        if isinstance(line.path[-1].data, ChildNode) and line.path[-1] in node.children:
            screen.rtree.cursor_line = line_index
            await pilot.pause()
            await pilot.press("enter")
            await _settle(app, pilot)
            return node
    raise AssertionError(f"no Objects sub-branch under {identifier}")


@pytest.mark.asyncio
async def test_a_bucket_can_be_walked_into_two_levels_deep(ctx, fake_bucket):
    """M5's whole claim, driven in the real app rather than asserted about."""
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        screen = app.screen
        assert isinstance(screen, ResourceTree)
        screen.add_type(BUCKET)
        await _settle(app, pilot)

        await _open(app, pilot, screen, "demo-bucket")
        # Level one: the folder is there and it can be opened.
        _, folder = _node(screen, "demo-bucket/logs/")
        assert folder.allow_expand is True, "a prefix must open, or there is no browser"

        await _open(app, pilot, screen, "demo-bucket/logs/")
        # Level two, and a file that must NOT offer to open.
        _, deeper = _node(screen, "demo-bucket/logs/2026/")
        assert deeper.allow_expand is True
        index, file_node = _node(screen, "demo-bucket/logs/a.txt")
        assert file_node.allow_expand is False, "an object has nothing under it"

        # And the cursor on a file yields the ref F9 and the preview consume.
        screen.rtree.cursor_line = index
        await pilot.pause()
        ref = screen.selected_ref()
        assert ref is not None and ref.type_name == OBJECT
        assert ref.identifier == "demo-bucket/logs/a.txt"
        assert s3actions.location_of(ref).bucket == "demo-bucket"


@pytest.mark.asyncio
async def test_folders_sort_above_files_in_the_tree(ctx, fake_bucket):
    """`a.txt` would sort above `2026/` alphabetically - `sort_key` prevents it."""
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        screen = app.screen
        screen.add_type(BUCKET)
        await _settle(app, pilot)
        await _open(app, pilot, screen, "demo-bucket")
        await _open(app, pilot, screen, "demo-bucket/logs/")

        seen = [
            line.path[-1].data.resource.identifier
            for line in screen.rtree._tree_lines
            if isinstance(line.path[-1].data, ResourceNode)
            and line.path[-1].data.resource.identifier.startswith("demo-bucket/logs/")
        ]
        assert seen.index("demo-bucket/logs/2026/") < seen.index("demo-bucket/logs/a.txt")


def test_the_self_checks_pass():
    s3actions._self_check()
    s3listers._self_check()
