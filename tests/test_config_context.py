"""CLITKA config persistence and the context resolution priority order."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from clitka.cli.main import app
from clitka.core import clitkaconfig
from clitka.core.clitkaconfig import ClitkaConfig
from clitka.core.context import Context
from clitka.core.errors import ConfigError

runner = CliRunner()


def test_missing_config_is_defaults(tmp_path):
    assert clitkaconfig.load(tmp_path / "nope.toml") == ClitkaConfig()


def test_update_is_read_modify_write(tmp_path):
    target = tmp_path / "config.toml"
    clitkaconfig.update(target, profile="root", region="eu-central-1")
    after = clitkaconfig.update(target, read_only=True)
    assert (after.profile, after.region, after.read_only) == ("root", "eu-central-1", True)
    assert clitkaconfig.load(target) == after


def test_unknown_keys_are_ignored_and_bad_types_rejected(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text('profile = "root"\nfuture_key = 42\n', encoding="utf-8")
    assert clitkaconfig.load(target).profile == "root"
    target.write_text("profile = 42\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        clitkaconfig.load(target)


def test_config_dir_honours_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CLITKA_CONFIG_DIR", str(tmp_path / "c"))
    assert clitkaconfig.config_path() == tmp_path / "c" / "config.toml"


def test_resolver_priority(isolated_home, monkeypatch):
    clitkaconfig.update(profile="from-config", region="eu-west-3")

    ctx = Context.from_env()
    assert (ctx.profile, ctx.region) == ("from-config", "eu-west-3")
    assert ctx.source["profile"] == "config"

    monkeypatch.setenv("AWS_PROFILE", "from-env")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-2")
    ctx = Context.from_env()
    assert (ctx.profile, ctx.region) == ("from-env", "us-east-2")
    assert ctx.source["region"] == "env"

    ctx = Context.from_env(profile="from-flag", region="ap-south-1")
    assert (ctx.profile, ctx.region) == ("from-flag", "ap-south-1")
    assert ctx.source == {"profile": "flag", "region": "flag", "read_only": "aws"}


def test_resolver_falls_through_to_aws_default(isolated_home):
    ctx = Context.from_env()
    assert ctx.profile is None
    assert ctx.region is None
    assert ctx.source["profile"] == "aws"


def test_read_only_from_config_and_env(isolated_home, monkeypatch):
    clitkaconfig.update(read_only=True)
    assert Context.from_env().read_only is True
    clitkaconfig.update(read_only=False)
    assert Context.from_env().read_only is False
    monkeypatch.setenv("CLITKA_READ_ONLY", "yes")
    assert Context.from_env().read_only is True


def test_ctx_use_persists_and_validates(isolated_home, sample_aws_config):
    (isolated_home / ".aws" / "config").write_text(sample_aws_config, encoding="utf-8")

    bad = runner.invoke(app, ["ctx", "use", "does-not-exist"])
    assert bad.exit_code == 1
    assert clitkaconfig.load().profile is None

    ok = runner.invoke(app, ["ctx", "use", "root", "-o", "json"])
    assert ok.exit_code == 0, ok.output
    saved = clitkaconfig.load()
    assert saved.profile == "root"
    assert saved.region == "eu-central-1"  # inherited from the profile

    cleared = runner.invoke(app, ["ctx", "use", "--clear"])
    assert cleared.exit_code == 0
    assert clitkaconfig.load().profile is None


def test_ctx_use_without_arguments_is_an_error(isolated_home):
    result = runner.invoke(app, ["ctx", "use"])
    assert result.exit_code == 2


def test_ctx_profiles_lists_sso_session(isolated_home, sample_aws_config):
    (isolated_home / ".aws" / "config").write_text(sample_aws_config, encoding="utf-8")
    result = runner.invoke(app, ["ctx", "profiles", "-o", "json"])
    assert result.exit_code == 0, result.output
    assert "corp" in result.stdout
    assert "assume-role" in result.stdout
