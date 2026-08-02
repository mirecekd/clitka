"""`C`: the config panel, and the persistence around it. No AWS calls.

Two things these tests exist to protect, both of them promises rather than code:

1. **`P` / `R` / `W` still never write.** That was the owner's rule from M2 and it
   is the reason `C` exists at all - one deliberate place that saves.
2. **The explorer is never empty.** A configured branch list that is empty or
   nonsense falls back to the built-in one.
"""

from __future__ import annotations

import time

import pytest

from clitka.core import clitkaconfig, clitkastate
from clitka.core import timerange as tr
from clitka.core.context import Context, Identity
from clitka.tui import configmodel as cm
from clitka.tui import configpanel
from clitka.tui.app import ClitkaApp
from clitka.tui.configpanel import BranchPicker
from clitka.tui.dropdown import TextDrop
from clitka.tui.dropmenu import DropMenu
from clitka.tui.keybar import KeyBar
from clitka.tui.restree import ResourceTree
from clitka.tui.restypes import TREE_TYPES


@pytest.fixture(autouse=True)
def _fresh_window():
    tr.reset()
    yield
    tr.reset()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """CLITKA's config and state files, both in a temp dir. Never the real home."""
    monkeypatch.setenv("CLITKA_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CLITKA_STATE_DIR", str(tmp_path / "state"))
    for var in ("AWS_PROFILE", "AWS_REGION", "AWS_DEFAULT_REGION", "CLITKA_READ_ONLY"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


@pytest.fixture
def offline(monkeypatch):
    ident = Identity(account="123456789012", arn="arn:aws:iam::1:user/mirek", user_id="A")
    monkeypatch.setattr(Context, "identity_or_none", lambda _self: ident)
    # The branch picker refills itself from `ListTypes` on a worker, exactly as
    # the `:` palette does. Stub it, or the test reaches for a real SSO token.
    monkeypatch.setattr(configpanel, "type_names", lambda _ctx, fallback: list(fallback))
    return Context(profile="sw-sandbox", region="eu-central-1")


# --- the panel, driven through the app -----------------------------------


@pytest.mark.asyncio
async def test_c_drops_the_config_panel_and_marks_its_slot(home, offline):
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, DropMenu)
        assert "[reverse]C Config[/reverse]" in app.query_one(KeyBar).line()
        # Every row must name what it would save, not just what it is about.
        labels = " ".join(item.label for item in app.screen.items)
        assert "sw-sandbox" in labels and "eu-central-1" in labels


@pytest.mark.asyncio
async def test_c_closes_the_panel_again(home, offline):
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, DropMenu)
        await pilot.press("c")
        await pilot.pause()
        assert not isinstance(app.screen, DropMenu)


@pytest.mark.asyncio
async def test_p_saves_the_session_profile_as_the_default(home, offline):
    """The whole point of `C`: promoting a session choice to a saved one."""
    assert clitkaconfig.load().profile is None
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        await pilot.press("c")
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        # It says what it wrote, and where.
        assert isinstance(app.screen, TextDrop)
        assert str(clitkaconfig.config_path()) in app.screen.body
    assert clitkaconfig.load().profile == "sw-sandbox"


@pytest.mark.asyncio
async def test_w_saves_the_session_window_as_the_default(home, offline):
    tr.select(tr.parse("6h"))
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        await pilot.press("c")
        await pilot.pause()
        await pilot.press("w")
        await pilot.pause()
    assert clitkaconfig.load().default_window == "6h"


@pytest.mark.asyncio
async def test_the_read_only_toggle_switches_both_ways(home, offline):
    """A toggle written through `update()` could only ever be switched ON.

    `clitkaconfig.update` drops None *and* False, so turning the flag off has to
    go through a full replace. This is the test that caught it.
    """
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        for expected in (True, False):
            await pilot.press("c")
            await pilot.pause()
            await pilot.press("o")
            await pilot.pause()
            assert clitkaconfig.load().read_only is expected
            await pilot.press("escape")
            await pilot.pause()


@pytest.mark.asyncio
async def test_the_remember_last_toggle_switches_both_ways(home, offline):
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        for expected in (True, False):
            await pilot.press("c")
            await pilot.pause()
            await pilot.press("l")
            await pilot.pause()
            assert clitkaconfig.load().remember_last is expected
            await pilot.press("escape")
            await pilot.pause()


@pytest.mark.asyncio
async def test_reset_clears_the_configured_branches(home, offline):
    clitkaconfig.update(tree_types=["AWS::SQS::Queue"])
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        await pilot.press("c")
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
    assert clitkaconfig.load().tree_types == []


# --- the branch picker ---------------------------------------------------


@pytest.mark.asyncio
async def test_the_branch_picker_saves_the_whole_set_not_one_type(home, offline):
    """`DropMenu` dismisses on the first pick; a *set* must not work that way."""
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        await pilot.press("c")
        await pilot.pause()
        await pilot.press("b")
        await pilot.pause()
        picker = app.screen
        assert isinstance(picker, BranchPicker)
        # It opens on the defaults, all of them marked.
        assert picker.chosen == list(TREE_TYPES)
        # Space on the first row removes it - and the panel stays open.
        await pilot.press("space")
        await pilot.pause()
        assert isinstance(app.screen, BranchPicker)
        assert len(picker.chosen) == len(TREE_TYPES) - 1
        await pilot.press("escape")
        await pilot.pause()
    saved = clitkaconfig.load().tree_types
    assert len(saved) == len(TREE_TYPES) - 1
    assert TREE_TYPES[0] not in saved


@pytest.mark.asyncio
async def test_the_branch_picker_never_mounts_a_whole_catalogue(home, offline, monkeypatch):
    """The owner's report: `C` then `b` froze. **1831 types, 92 seconds.**

    `ListTypes` returns 1831 types on a real account and every one of them became
    a mounted Textual `ListItem` - on open, and again on every `space`. This test
    hands the picker a realistic catalogue and asserts it builds a *window*, with
    a wall-clock bound that a rebuilt-everything regression cannot pass.
    """
    catalogue = [f"AWS::Svc{n // 60}::Thing{n % 60}" for n in range(1831)]
    monkeypatch.setattr(configpanel, "type_names", lambda _ctx, _fb: catalogue)

    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        started = time.perf_counter()
        await pilot.press("c")
        await pilot.pause()
        await pilot.press("b")
        await pilot.pause()
        opened = time.perf_counter() - started

        picker = app.screen
        assert isinstance(picker, BranchPicker)
        assert len(picker.matches) <= cm.BRANCH_ROWS, len(picker.matches)
        # 1831 rows measured at 92 s; a generous bound still catches that.
        assert opened < 20, f"opening the picker took {opened:.1f}s"

        # The worker's fuller list must not undo the cap either.
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert len(picker.matches) <= cm.BRANCH_ROWS, len(picker.matches)
        assert len(picker.candidates) > cm.BRANCH_ROWS, "the catalogue is still all there"


@pytest.mark.asyncio
async def test_the_tree_rebuilds_its_branches_when_they_change(home, offline):
    """A branch that was taken away must not linger on screen until a restart."""
    app = ClitkaApp(offline, open_tree=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.screen
        assert isinstance(tree, ResourceTree)
        assert tree.types == list(TREE_TYPES)

        tree.adopt_types(["AWS::SQS::Queue", "AWS::SNS::Topic"])
        await pilot.pause()
        assert tree.types == ["AWS::SQS::Queue", "AWS::SNS::Topic"]
        assert len(tree.rtree.root.children) == 2


# --- what starts a session ------------------------------------------------


def test_the_explorer_is_never_empty(home):
    """An empty or unusable `tree_types` falls back to the built-in list."""
    from clitka.tui.restypes import tree_types

    clitkaconfig.update(profile="p")  # a config file that exists but says nothing
    assert tree_types() == list(TREE_TYPES)
    clitkaconfig.save(clitkaconfig.ClitkaConfig(tree_types=["not a type"]))
    assert tree_types() == list(TREE_TYPES)


@pytest.mark.asyncio
async def test_the_configured_branches_are_what_the_tree_opens_with(home, offline):
    clitkaconfig.update(tree_types=["AWS::SQS::Queue", "AWS::SNS::Topic"])
    app = ClitkaApp(offline, open_tree=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.screen
        assert isinstance(tree, ResourceTree)
        assert tree.types == ["AWS::SQS::Queue", "AWS::SNS::Topic"]


@pytest.mark.asyncio
async def test_the_configured_window_is_what_a_session_starts_on(home, offline):
    clitkaconfig.update(default_window="3h")
    ClitkaApp(offline, open_tree=False)
    assert tr.current().label == "3h"


@pytest.mark.asyncio
async def test_an_unparseable_default_window_does_not_stop_the_app(home, offline):
    clitkaconfig.save(clitkaconfig.ClitkaConfig(default_window="tomorrow"))
    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        await pilot.pause()
    assert tr.current().label == tr.DEFAULT.label


# --- "start where I stopped" ---------------------------------------------


def test_nothing_is_remembered_unless_asked(home, offline):
    """Off by default: a fresh install must behave as it did before this existed."""
    app = ClitkaApp(offline, open_tree=False)
    app.remember_session()
    assert clitkastate.load() == clitkastate.ClitkaState()
    assert not clitkastate.state_path().exists()


def test_the_session_is_remembered_when_asked(home, offline):
    clitkaconfig.update(remember_last=True)
    app = ClitkaApp(offline, open_tree=False)
    app.remember_session()
    state = clitkastate.load()
    assert state.last_profile == "sw-sandbox"
    assert state.last_region == "eu-central-1"


def test_the_remembered_session_is_the_weakest_voice(home, monkeypatch):
    """`state.toml` may only fill a gap - it must never beat an explicit choice."""
    clitkaconfig.update(remember_last=True, profile="from-config")
    clitkastate.save(clitkastate.ClitkaState(last_profile="from-state"))

    app = ClitkaApp.__new__(ClitkaApp)
    app.config = clitkaconfig.load()
    # The config file wins, and the source says so rather than lying about it.
    assert app.opening_context().profile == "from-config"

    # With nothing in the config, the state file is what is left.
    clitkaconfig.save(clitkaconfig.ClitkaConfig(remember_last=True))
    app.config = clitkaconfig.load()
    started = app.opening_context()
    assert started.profile == "from-state"
    assert started.source["profile"] == "state"

    # And an env var still beats it.
    monkeypatch.setenv("AWS_PROFILE", "from-env")
    assert app.opening_context().profile == "from-env"


def test_a_broken_state_file_is_forgotten_not_fatal(home, offline):
    clitkaconfig.update(remember_last=True)
    clitkastate.state_path().parent.mkdir(parents=True, exist_ok=True)
    clitkastate.state_path().write_text("}{ not toml", encoding="utf-8")
    app = ClitkaApp.__new__(ClitkaApp)
    app.config = clitkaconfig.load()
    assert app.opening_context() is not None


# --- the rule this whole feature had to not break ------------------------


@pytest.mark.asyncio
async def test_p_r_and_w_still_never_write_to_disk(home, offline, monkeypatch):
    """The owner's standing rule. `C` is the only screen allowed to save."""
    calls: list[object] = []
    monkeypatch.setattr(clitkaconfig, "save", lambda *a, **k: calls.append(a))

    app = ClitkaApp(offline, open_tree=False)
    async with app.run_test() as pilot:
        for key, then in (("p", "escape"), ("r", "escape"), ("w", "3")):
            await pilot.press(key)
            await pilot.pause()
            await pilot.press(then)
            await pilot.pause()
    assert calls == [], calls
    # The window really did change - the test would be vacuous otherwise.
    assert tr.current().label == "1h"


def test_config_and_state_are_different_files(home):
    """The XDG distinction: a chosen setting is not the same as a noticed one."""
    assert clitkaconfig.config_path() != clitkastate.state_path()
    assert clitkaconfig.config_path().name == "config.toml"
    assert clitkastate.state_path().name == "state.toml"


def test_every_panel_key_is_unique():
    """`ActionMenu`-style collision check: the first match wins, so one key each."""
    keys = [row.key for row in cm.settings_items(clitkaconfig.ClitkaConfig())]
    assert len(set(keys)) == len(keys), keys
    # `c` would close the panel instead of running the row.
    assert "c" not in keys
