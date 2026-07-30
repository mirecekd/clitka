"""~/.aws/config parsing: profiles, sso-sessions, source_profile chains."""

from __future__ import annotations

from pathlib import Path

import pytest

from clitka.core.awsconfig import aws_config_path, load_aws_config
from clitka.core.errors import ConfigError


def _load(aws_files: tuple[Path, Path]):
    return load_aws_config(*aws_files)


def test_profiles_and_sessions_are_parsed(aws_files):
    cfg = _load(aws_files)
    assert set(cfg.sso_sessions) == {"corp", "other"}
    assert cfg.sso_sessions["corp"].start_url == "https://corp.awsapps.com/start/#"
    assert cfg.sso_sessions["corp"].registration_scopes == ("sso:account:access",)
    assert {"default", "root", "child", "legacy", "keys", "only-in-credentials"} <= set(
        cfg.profiles
    )
    assert "shared" not in cfg.profiles  # [services shared] is not a profile


def test_profile_fields(aws_files):
    root = _load(aws_files).profile("root")
    assert root.region == "eu-central-1"
    assert root.sso_account_id == "111122223333"
    assert root.sso_role_name == "AdminRole"
    assert root.kind == "sso"


def test_credentials_only_profile_is_included(aws_files):
    cfg = _load(aws_files)
    only = cfg.profile("only-in-credentials")
    assert only.origin == "credentials"
    assert only.kind == "static"
    # config wins over credentials for keys present in both
    assert cfg.profile("keys").region == "ap-south-1"
    assert cfg.profile("keys").get("aws_access_key_id") == "AKIAEXAMPLE"


def test_sso_for_follows_source_profile(aws_files):
    cfg = _load(aws_files)
    session, holder = cfg.sso_for("child")
    assert session is not None
    assert session.name == "corp"
    assert holder.name == "root"


def test_sso_for_legacy_inline_start_url(aws_files):
    session, holder = _load(aws_files).sso_for("legacy")
    assert session is not None
    assert session.start_url == "https://legacy.awsapps.com/start"
    assert session.region == "eu-north-1"
    assert holder.name == "legacy"


def test_sso_for_returns_none_without_sso(aws_files):
    session, _ = _load(aws_files).sso_for("keys")
    assert session is None


def test_unknown_profile_and_dangling_session_raise(aws_files):
    cfg = _load(aws_files)
    with pytest.raises(ConfigError):
        cfg.profile("nope")
    with pytest.raises(ConfigError):
        cfg.sso_for("orphan")


def test_summary_rows(aws_files):
    rows = {row["profile"]: row for row in _load(aws_files).summary()}
    assert rows["root"]["sso_session"] == "corp"
    assert rows["child"]["kind"] == "assume-role"
    assert rows["orphan"]["sso_session"] is None  # dangling session degrades, not raises


def test_missing_files_are_empty(tmp_path):
    cfg = load_aws_config(tmp_path / "nope", tmp_path / "nope2")
    assert cfg.profiles == {}
    assert cfg.sso_sessions == {}


def test_config_path_honours_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "custom"))
    assert aws_config_path() == tmp_path / "custom"
