"""SSO device flow and token cache. Stubbed sso-oidc, no network, no real ~/.aws."""

from __future__ import annotations

import datetime as dt
import json

import boto3
import pytest
from botocore.stub import Stubber
from typer.testing import CliRunner

from clitka.cli.main import app
from clitka.core import sso, ssocache, ssotargets
from clitka.core.awsconfig import load_aws_config
from clitka.core.errors import AuthError, ConfigError

runner = CliRunner()


@pytest.fixture
def sso_home(isolated_home, sample_aws_config, monkeypatch):
    """isolated_home plus a sample AWS config and an empty SSO cache."""
    (isolated_home / ".aws" / "config").write_text(sample_aws_config, encoding="utf-8")
    cache = isolated_home / ".aws" / "sso" / "cache"
    cache.mkdir(parents=True)
    monkeypatch.setenv("AWS_SSO_CACHE_DIR", str(cache))
    return isolated_home


def _oidc():
    return boto3.Session(region_name="eu-central-1").client("sso-oidc")


def _seed_token(key: str, hours: float) -> None:
    ssocache.write(
        key,
        {
            "startUrl": "https://corp.awsapps.com/start/#",
            "region": "eu-central-1",
            "accessToken": "cached-token",
            "expiresAt": ssocache.to_aws_time(ssocache.now() + dt.timedelta(hours=hours)),
        },
    )


def test_cache_key_matches_aws_cli_layout():
    # aws CLI v2 keys sso-session tokens by sha1 of the session name.
    assert ssocache.cache_key("trask") == "0213c69ee8fe69a1fbe5387186b7ccf390e031f6"
    assert ssocache.cache_file("trask").name.endswith(".json")


def test_target_resolution(sso_home):
    cfg = load_aws_config()
    assert [t.key for t in ssotargets.targets(cfg)] == ["corp", "other"]

    corp = ssotargets.target_for_profile(cfg, "child")  # via source_profile
    assert corp.key == "corp"
    assert corp.scopes == ("sso:account:access",)

    legacy = ssotargets.target_for_profile(cfg, "legacy")
    assert legacy.key == "https://legacy.awsapps.com/start"  # keyed by start URL

    with pytest.raises(ConfigError):
        ssotargets.target_for_profile(cfg, "keys")


def test_login_reuses_a_valid_cached_token(sso_home):
    _seed_token("corp", hours=4)
    target = ssotargets.target_for_profile(load_aws_config(), "root")
    messages: list[str] = []
    # No client is passed: if the flow tried to call AWS it would fail here.
    token = sso.login(target, report=messages.append)
    assert token.access_token == "cached-token"
    assert any("already signed in" in m for m in messages)


def test_login_runs_the_device_flow_and_writes_the_cache(sso_home):
    target = ssotargets.target_for_profile(load_aws_config(), "root")
    client = _oidc()
    with Stubber(client) as stub:
        stub.add_response(
            "register_client",
            {
                "clientId": "cid",
                "clientSecret": "csecret",
                "clientSecretExpiresAt": int((ssocache.now() + dt.timedelta(days=90)).timestamp()),
            },
            {"clientName": "clitka", "clientType": "public", "scopes": ["sso:account:access"]},
        )
        stub.add_response(
            "start_device_authorization",
            {
                "deviceCode": "dc",
                "userCode": "ABCD-EFGH",
                "verificationUri": "https://device.sso/",
                "verificationUriComplete": "https://device.sso/?user_code=ABCD-EFGH",
                "interval": 1,
                "expiresIn": 600,
            },
            {
                "clientId": "cid",
                "clientSecret": "csecret",
                "startUrl": "https://corp.awsapps.com/start/#",
            },
        )
        stub.add_client_error("create_token", service_error_code="AuthorizationPendingException")
        stub.add_response(
            "create_token",
            {"accessToken": "fresh-token", "expiresIn": 28800, "tokenType": "Bearer"},
        )
        messages: list[str] = []
        slept: list[float] = []
        token = sso.login(
            target,
            report=messages.append,
            client=client,
            sleep=slept.append,
        )
        stub.assert_no_pending_responses()

    assert token.access_token == "fresh-token"
    assert token.is_valid()
    assert slept == [1.0]  # polled once while pending
    assert any("ABCD-EFGH" in m for m in messages)

    # The file must be readable by aws CLI v2: sha1(session) name, its keys.
    path = ssocache.cache_file("corp")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert path.name == f"{ssocache.cache_key('corp')}.json"
    assert raw["startUrl"] == "https://corp.awsapps.com/start/#"
    assert raw["region"] == "eu-central-1"
    assert raw["clientId"] == "cid"
    assert raw["expiresAt"].endswith("Z")
    assert oct(path.stat().st_mode)[-3:] == "600"


def test_login_reuses_the_client_registration(sso_home):
    ssocache.write(
        "corp",
        {
            "clientId": "cid",
            "clientSecret": "csecret",
            "registrationExpiresAt": ssocache.to_aws_time(ssocache.now() + dt.timedelta(days=30)),
        },
    )
    target = ssotargets.target_for_profile(load_aws_config(), "root")
    client = _oidc()
    with Stubber(client) as stub:
        # No register_client response is queued: registering again would fail.
        stub.add_response(
            "start_device_authorization",
            {"deviceCode": "dc", "userCode": "U", "verificationUri": "https://d/", "interval": 1},
            {
                "clientId": "cid",
                "clientSecret": "csecret",
                "startUrl": "https://corp.awsapps.com/start/#",
            },
        )
        stub.add_response("create_token", {"accessToken": "t2", "expiresIn": 3600})
        sso.login(target, client=client, sleep=lambda _s: None)
        stub.assert_no_pending_responses()
    assert ssocache.read("corp").access_token == "t2"


def test_expired_device_code_raises_auth_error(sso_home):
    target = ssotargets.target_for_profile(load_aws_config(), "root")
    client = _oidc()
    with Stubber(client) as stub:
        stub.add_response("register_client", {"clientId": "cid", "clientSecret": "csecret"}, None)
        stub.add_response(
            "start_device_authorization",
            {"deviceCode": "dc", "userCode": "U", "verificationUri": "https://d/", "interval": 1},
            None,
        )
        stub.add_client_error("create_token", service_error_code="ExpiredTokenException")
        with pytest.raises(AuthError):
            sso.login(target, client=client, sleep=lambda _s: None)


def test_logout_keeps_the_registration_by_default(sso_home):
    _seed_token("corp", hours=4)
    ssocache.write("corp", {"clientId": "cid", "clientSecret": "csecret"})
    target = ssotargets.target_for_profile(load_aws_config(), "root")

    assert sso.logout(target) is True
    left = ssocache.read("corp")
    assert left is not None and left.access_token is None
    assert left.registration == ("cid", "csecret")
    assert sso.logout(target) is False  # nothing left to do

    assert sso.logout(target, forget_registration=True) is True
    assert ssocache.read("corp") is None


def test_status_reports_validity(sso_home):
    _seed_token("corp", hours=2)
    rows = {row["sso_session"]: row for row in ssotargets.status(load_aws_config())}
    assert rows["corp"]["valid"] == "yes"
    assert rows["corp"]["expires_in"].startswith("1h5")
    assert rows["other"]["valid"] == "no"
    assert rows["other"]["expires_at"] is None


def test_expired_token_is_not_valid(sso_home):
    _seed_token("corp", hours=-1)
    rows = {row["sso_session"]: row for row in ssotargets.status(load_aws_config())}
    assert rows["corp"]["valid"] == "no"
    assert rows["corp"]["expires_in"] == "expired"


def test_cli_auth_status_and_logout(sso_home):
    _seed_token("corp", hours=3)
    result = runner.invoke(app, ["auth", "status", "-o", "json"])
    assert result.exit_code == 0, result.output
    assert "corp" in result.stdout

    out = runner.invoke(app, ["auth", "logout", "--sso-session", "corp"])
    assert out.exit_code == 0, out.output
    assert "[OK]" in out.stdout
    assert ssocache.read("corp").access_token is None


def test_cli_auth_login_requires_a_target_when_ambiguous(sso_home):
    result = runner.invoke(app, ["auth", "login"])
    assert result.exit_code == 1
    assert "corp" in result.output


def test_cli_auth_login_reuses_cached_token(sso_home):
    _seed_token("corp", hours=5)
    result = runner.invoke(app, ["auth", "login", "--sso-session", "corp"])
    assert result.exit_code == 0, result.output
    assert "already signed in" in result.stdout
