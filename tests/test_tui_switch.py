"""F2/F3 profile and region switching. No AWS calls - the config is a fixture."""

from __future__ import annotations

import pytest

from clitka.core.awsconfig import AwsConfig, Profile, SsoSession
from clitka.core.context import Context, Identity
from clitka.tui import app as app_module
from clitka.tui.app import ClitkaApp
from clitka.tui.dropdown import TextDrop
from clitka.tui.dropmenu import DropMenu
from clitka.tui.keybar import KeyBar
from clitka.tui.status import StatusBar
from clitka.tui.switch import profile_items, region_items

CONFIG = AwsConfig(
    profiles={
        "sw-sandbox": Profile(
            "sw-sandbox", settings={"sso_session": "sw", "region": "eu-central-1"}
        ),
        "k-d-mirdvorak": Profile(
            "k-d-mirdvorak", settings={"aws_access_key_id": "AK", "region": "eu-central-1"}
        ),
        "trask": Profile("trask", settings={"sso_session": "sw", "region": "eu-west-1"}),
    },
    sso_sessions={"sw": SsoSession("sw", start_url="https://x.awsapps.com/start")},
)


@pytest.fixture
def offline(monkeypatch):
    """An app whose identity, config and region list never touch the network."""
    ident = Identity(account="123456789012", arn="arn:aws:iam::1:user/mirek", user_id="A")
    monkeypatch.setattr(Context, "identity_or_none", lambda _self: ident)
    monkeypatch.setattr(app_module, "load_aws_config", lambda: CONFIG)
    monkeypatch.setattr(
        ClitkaApp, "_regions", lambda _self: ["eu-central-1", "eu-west-1", "us-east-1"]
    )
    return Context(profile="sw-sandbox", region="eu-central-1")


# --- list building --------------------------------------------------------


def test_profile_items_marks_the_active_profile():
    items = profile_items(CONFIG, current="trask")
    assert [i.value for i in items] == ["k-d-mirdvorak", "sw-sandbox", "trask"]
    assert [i.current for i in items] == [False, False, True]


def test_profile_items_detail_shows_kind_region_and_session():
    detail = {i.value: i.detail for i in profile_items(CONFIG, None)}
    assert "sso" in detail["sw-sandbox"]
    assert "eu-central-1" in detail["sw-sandbox"]
    assert "sw" in detail["sw-sandbox"]
    assert "static" in detail["k-d-mirdvorak"]


def test_profile_items_with_no_profiles_is_empty():
    assert profile_items(AwsConfig(), None) == []


def test_region_items_are_sorted_with_the_active_one_marked():
    items = region_items(["us-east-1", "eu-central-1", "ap-south-1"], "eu-central-1")
    assert [i.value for i in items] == ["ap-south-1", "eu-central-1", "us-east-1"]
    assert items[1].current is True


# --- driven through the app ----------------------------------------------


@pytest.mark.asyncio
async def test_f2_drops_the_profile_menu_and_switches_the_context(offline):
    app = ClitkaApp(offline)
    async with app.run_test() as pilot:
        await pilot.press("f2")
        await pilot.pause()
        menu = app.screen
        assert isinstance(menu, DropMenu)
        assert "[reverse]F2 Profile[/reverse]" in app.query_one(KeyBar).line()
        # The cursor starts on the active profile; move down to "trask".
        assert [i.value for i in menu.matches][1] == "sw-sandbox"
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert app.context.profile == "trask"
        assert app.query_one(StatusBar).profile == "trask"
        assert "[b]F2[/b] Profile" in app.query_one(KeyBar).line()


@pytest.mark.asyncio
async def test_f3_drops_the_region_menu_and_switches_the_context(offline):
    app = ClitkaApp(offline)
    async with app.run_test() as pilot:
        await pilot.press("f3")
        await pilot.pause()
        assert isinstance(app.screen, DropMenu)
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert app.context.region == "eu-west-1"
        assert app.query_one(StatusBar).aws_region == "eu-west-1"


@pytest.mark.asyncio
async def test_escaping_the_profile_menu_changes_nothing(offline):
    app = ClitkaApp(offline)
    async with app.run_test() as pilot:
        await pilot.press("f2")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.context.profile == "sw-sandbox"


@pytest.mark.asyncio
async def test_picking_the_active_profile_again_is_a_no_op(offline):
    app = ClitkaApp(offline)
    async with app.run_test() as pilot:
        before = app.context
        await pilot.press("f2")
        await pilot.pause()
        await pilot.press("enter")  # cursor sits on the active one
        await pilot.pause()
        assert app.context is before


@pytest.mark.asyncio
async def test_an_empty_profile_list_explains_itself_instead_of_crashing(offline, monkeypatch):
    monkeypatch.setattr(app_module, "load_aws_config", AwsConfig)
    app = ClitkaApp(offline)
    async with app.run_test() as pilot:
        await pilot.press("f2")
        await pilot.pause()
        panel = app.screen
        assert isinstance(panel, TextDrop)
        assert "Nothing to choose from" in panel.body


@pytest.mark.asyncio
async def test_switching_the_profile_does_not_touch_the_saved_config(offline, monkeypatch):
    """F2 is session-only by the owner's explicit call - nothing is persisted."""
    from clitka.core import clitkaconfig

    def boom(*_args, **_kwargs):
        raise AssertionError("F2 must not write the CLITKA config")

    monkeypatch.setattr(clitkaconfig, "save", boom, raising=False)
    app = ClitkaApp(offline)
    async with app.run_test() as pilot:
        await pilot.press("f2")
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert app.context.profile == "trask"
