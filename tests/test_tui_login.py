"""The in-TUI SSO device login: F4 opens it, escape gets out at once, no network."""

from __future__ import annotations

import pytest

from clitka.core.context import Context, Identity
from clitka.core.errors import ClitkaError
from clitka.tui.app import ClitkaApp
from clitka.tui.logindrop import Cancelled, LoginDrop, target_for


@pytest.fixture
def offline_context(monkeypatch):
    ident = Identity(
        account="123456789012", arn="arn:aws:iam::123456789012:user/mirek", user_id="A"
    )
    monkeypatch.setattr(Context, "identity_or_none", lambda _self: ident)
    return Context(profile="demo", region="eu-central-1")


@pytest.fixture(autouse=True)
def no_real_login(monkeypatch):
    """Never touch sso-oidc: the panel is under test, not the flow."""

    def refuse(*_args, **_kwargs):
        raise ClitkaError("stubbed: no network in tests")

    monkeypatch.setattr("clitka.tui.logindrop.target_for", refuse)


@pytest.mark.asyncio
async def test_f4_opens_the_login_panel_and_escape_closes_it(offline_context):
    app = ClitkaApp(offline_context, open_tree=False)
    async with app.run_test() as pilot:
        await pilot.press("f4")
        await pilot.pause()
        assert isinstance(app.screen, LoginDrop)

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, LoginDrop)


@pytest.mark.asyncio
async def test_a_failed_login_is_shown_not_swallowed(offline_context):
    app = ClitkaApp(offline_context, open_tree=False)
    async with app.run_test() as pilot:
        await pilot.press("f4")
        await pilot.pause()
        panel = app.screen
        assert isinstance(panel, LoginDrop)
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert any("[ERROR]" in line for line in panel.lines), panel.lines


@pytest.mark.asyncio
async def test_offer_login_opens_only_one_panel(offline_context):
    app = ClitkaApp(offline_context, open_tree=False)
    async with app.run_test() as pilot:
        app.offer_login("AWS::S3::Bucket: the login has expired")
        await pilot.pause()
        panel = app.screen
        assert isinstance(panel, LoginDrop)
        assert "expired" in panel.lines[0]

        app.offer_login("again")
        await pilot.pause()
        assert app.screen is panel  # not stacked twice


def test_cancelled_sleep_returns_immediately():
    panel = LoginDrop(Context(profile="p"))
    panel._give_up = True
    with pytest.raises(Cancelled):
        panel._sleep(30.0)


def test_login_without_a_profile_is_refused(monkeypatch):
    monkeypatch.undo()  # target_for is stubbed by the autouse fixture
    with pytest.raises(ClitkaError, match="no profile"):
        target_for(Context())


def test_renewed_context_drops_the_cached_session_and_identity():
    # No profile: the default credential chain always builds a session, so this
    # runs anywhere, including CI without a ~/.aws/config.
    ctx = Context(region="eu-central-1", read_only=True)
    ctx.client("sts")  # populates the client cache and the session

    ctx._identity = Identity(account="1", arn="arn:aws:iam::1:user/x", user_id="A")

    fresh = ctx.renewed()
    assert fresh.profile == ctx.profile
    assert fresh.region == ctx.region
    assert fresh.read_only is ctx.read_only
    assert fresh._clients == {}
    assert getattr(fresh, "_identity", None) is None
