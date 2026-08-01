"""The 2026-07-31 view rework: letter switches, F3 view / F4 edit, the name column.

The owner's four requests, one test group each:

1. F1 stays help; profile / region move to P / R (either case). Signing in was
   later dropped from the TUI altogether - it is `clitka auth login`.

2. F3 views the selected resource, F4 is the edit slot.
3. A resource is recognisable by its *name* - the `Name` tag on EC2 - not only by
   its identifier.
4. Inside the preview the tabs are reachable from the keyboard, and the focused
   side is highlighted.

No AWS calls anywhere - Cloud Control is stubbed.
"""

from __future__ import annotations

import pytest

from clitka.core import cloudcontrol as cc
from clitka.core.actions import ResourceRef
from clitka.core.context import Context, Identity
from clitka.tui import viewedit as ve
from clitka.tui.app import ClitkaApp
from clitka.tui.dropmenu import DropMenu
from clitka.tui.keybar import ALL_KEYS, KeyBar
from clitka.tui.preview import PreviewPane
from clitka.tui.restree import ResourceTree
from clitka.tui.resultview import ResultScreen

EC2 = "AWS::EC2::Instance"
INSTANCES = [
    cc.Resource(
        EC2,
        "i-0abc",
        {"Tags": [{"Key": "Name", "Value": "web-01"}], "InstanceType": "t3.micro"},
    ),
    cc.Resource(EC2, "i-0def", {"InstanceType": "t3.small"}),
]


@pytest.fixture
def offline(monkeypatch):
    ident = Identity(account="123456789012", arn="arn:aws:iam::1:user/mirek", user_id="A")
    monkeypatch.setattr(Context, "identity_or_none", lambda _self: ident)
    monkeypatch.setattr(cc, "iter_resources", lambda *_a, **_k: iter(INSTANCES))
    return Context(profile="demo", region="eu-central-1")


async def _first_instance(pilot, app, identifier: str = "i-0abc"):
    """Open the one branch and put the cursor on `identifier`.

    It seeks rather than pressing `down` once: the branch is sorted by what the
    leaf leads with, so `web-01` (i-0abc) comes *after* the unnamed i-0def, and
    "the first leaf" is not the one these tests are about.
    """
    await pilot.pause()
    await pilot.press("enter")
    await app.workers.wait_for_complete()
    await pilot.pause()

    tree = app.screen.rtree
    for index, line in enumerate(tree._tree_lines):
        data = line.path[-1].data
        if getattr(getattr(data, "resource", None), "identifier", None) == identifier:
            tree.cursor_line = index
            await pilot.pause()
            return
    raise AssertionError(f"{identifier} is not in the tree")


# --- 1. the letter switches -----------------------------------------------


@pytest.mark.parametrize("key", ["p", "P"])
@pytest.mark.asyncio
async def test_p_opens_the_profile_menu_in_either_case(offline, key, monkeypatch):
    from clitka.core.awsconfig import AwsConfig, Profile
    from clitka.tui import appswitch

    config = AwsConfig(
        profiles={"demo": Profile("demo", settings={"region": "eu-central-1"})},
        sso_sessions={},
    )
    monkeypatch.setattr(appswitch, "load_aws_config", lambda: config)

    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        await pilot.press(key)
        await pilot.pause()
        assert isinstance(app.screen, DropMenu)
        assert "Switch profile" in app.screen.title_text


@pytest.mark.parametrize("key", ["r", "R"])
@pytest.mark.asyncio
async def test_r_opens_the_region_menu_in_either_case(offline, key, monkeypatch):
    monkeypatch.setattr(ClitkaApp, "_regions", lambda _self: ["eu-central-1", "eu-west-1"])
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        await pilot.press(key)
        await pilot.pause()
        assert isinstance(app.screen, DropMenu)
        assert "Switch region" in app.screen.title_text


@pytest.mark.parametrize("key", ["l", "L"])
@pytest.mark.asyncio
async def test_l_does_nothing_the_tui_has_no_login(offline, key):
    """Login was removed from the app (owner's call) - L must not open anything."""
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        opened = app.screen
        await pilot.press(key)
        await pilot.pause()
        assert app.screen is opened
    assert not hasattr(ClitkaApp, "action_login")
    assert not hasattr(ClitkaApp, "offer_login")


def test_the_key_bar_offers_the_new_slots():
    assert {"F1", "P", "R", "W", "F3", "F4", "F9", "F10"} <= ALL_KEYS
    assert "F2" not in ALL_KEYS, "profile is P now"
    assert "L" not in ALL_KEYS, "signing in is `clitka auth login`, not a key"

    line = KeyBar().line()
    assert "[b]P[/b] Profile" in line
    assert "[b]F3[/b] View" in line
    assert "[b]F4[/b] Edit" in line


# --- 2. F3 view / F4 edit -------------------------------------------------


@pytest.mark.asyncio
async def test_f3_views_the_selected_resource_in_full(offline, monkeypatch):
    asked: list[str] = []

    def fake_get(_ctx, type_name, identifier):
        asked.append(identifier)
        return cc.Resource(type_name, identifier, {"InstanceType": "t3.micro", "State": "running"})

    monkeypatch.setattr(cc, "get_resource", fake_get)
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        app.push_screen(ResourceTree(offline, [EC2]))
        await _first_instance(pilot, app)
        await pilot.press("f3")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert asked == ["i-0abc"], "F3 must fetch the full resource, not reuse the row"
        assert isinstance(app.screen, ResultScreen)
        assert "State: running" in app.screen.result.body


@pytest.mark.asyncio
async def test_f4_explains_itself_rather_than_pretending_to_edit(offline):
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        app.push_screen(ResourceTree(offline, [EC2]))
        await _first_instance(pilot, app)
        await pilot.press("f4")
        await pilot.pause()
        assert isinstance(app.screen, ResultScreen)
        assert "not wired up" in app.screen.result.body
        assert "i-0abc" in app.screen.result.title


@pytest.mark.asyncio
async def test_f3_on_a_type_branch_says_nothing_is_selected(offline):
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        app.push_screen(ResourceTree(offline, [EC2]))
        await pilot.pause()
        await pilot.press("f3")
        await pilot.pause()
        assert isinstance(app.screen, ResultScreen)
        assert app.screen.result.body == ve.NOTHING


def test_view_falls_back_to_the_row_when_get_resource_fails(monkeypatch):
    def denied(*_a, **_kw):
        raise cc.ClitkaError("AccessDenied: cloudcontrol:GetResource")

    monkeypatch.setattr(cc, "get_resource", denied)
    ref = ResourceRef.from_row(EC2, INSTANCES[0].row())
    text = ve.view_yaml(Context(region="eu-central-1"), ref)
    assert "AccessDenied" in text, "the reason must be visible, not swallowed"
    assert "InstanceType: t3.micro" in text, "the listing row is better than nothing"


# --- 3. the name ----------------------------------------------------------


def test_an_ec2_instance_is_named_by_its_name_tag():
    assert INSTANCES[0].name() == "web-01"
    assert INSTANCES[1].name() == "", "no Name tag, no name - not a made-up one"
    assert INSTANCES[0].row()["name"] == "web-01"
    assert "name" not in INSTANCES[1].row()


def test_the_name_column_comes_second_when_any_row_has_one():
    assert cc.columns_for(INSTANCES, limit=4)[:2] == ["identifier", "name"]
    assert cc.columns_for([INSTANCES[1]])[0] == "identifier"
    assert "name" not in cc.columns_for([INSTANCES[1]])


def test_a_bucket_is_named_by_its_name_property():
    bucket = cc.Resource("AWS::S3::Bucket", "arn:aws:s3:::b1", {"BucketName": "b1"})
    assert bucket.name() == "b1"
    # A property that only repeats the identifier is not a name.
    same = cc.Resource("AWS::S3::Bucket", "b1", {"BucketName": "b1"})
    assert same.name() == ""


@pytest.mark.asyncio
async def test_the_tree_leaf_leads_with_the_name(offline):
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        screen = ResourceTree(offline, [EC2])
        app.push_screen(screen)
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        labels = [str(child.label) for child in screen.rtree.root.children[0].children]
        assert any("web-01" in label and "i-0abc" in label for label in labels), labels


@pytest.mark.asyncio
async def test_the_overview_shows_the_name_but_the_raw_tab_does_not(offline):
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        app.push_screen(ResourceTree(offline, [EC2]))
        await _first_instance(pilot, app)
        await pilot.press("enter")
        await pilot.pause()
        pane = app.screen.query_one(PreviewPane)
        assert "web-01" in pane.body_text("overview")
        raw = pane.body_text("raw")
        # `name` is derived by the table, not returned by the API.
        assert "\nname:" not in raw and "  name:" not in raw, raw


# --- 4. the preview keyboard and focus ------------------------------------


@pytest.mark.asyncio
async def test_tab_lands_on_the_tab_strip_so_the_arrows_can_walk_the_tabs(offline):
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        app.push_screen(ResourceTree(offline, [EC2]))
        await _first_instance(pilot, app)
        await pilot.press("enter")
        await pilot.pause()
        screen = app.screen
        pane = screen.query_one(PreviewPane)
        tabs = pane.query_one("#preview-tabs")

        await pilot.press("tab")
        await pilot.pause()
        assert pane.focused_here(screen.focused), "the preview must take the focus"
        strip = pane._strip()
        assert strip is not None and strip.has_focus, "the strip is what the arrows drive"
        assert tabs.active.endswith("overview"), tabs.active

        await pilot.press("right")
        await pilot.pause()
        assert tabs.active.endswith("raw"), "right must move to the next tab"

        await pilot.press("left")
        await pilot.pause()
        assert tabs.active.endswith("overview")

        # ...and tab goes back to the tree.
        await pilot.press("tab")
        await pilot.pause()
        assert not pane.focused_here(screen.focused)
        assert screen.rtree.has_focus


@pytest.mark.asyncio
async def test_the_focused_side_is_highlighted(offline):
    """The explorer showed its focus and the viewer did not - the owner's report."""
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        app.push_screen(ResourceTree(offline, [EC2]))
        await _first_instance(pilot, app)
        await pilot.press("enter")
        await pilot.pause()
        screen = app.screen
        pane = screen.query_one(PreviewPane)

        assert "focus-within" not in pane.pseudo_classes
        await pilot.press("tab")
        await pilot.pause()
        assert "focus-within" in pane.pseudo_classes, "the preview must look focused"
