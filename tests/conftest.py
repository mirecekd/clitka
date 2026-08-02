"""Shared fixtures. No test is allowed to touch the real ~/.aws or the network."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _own_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point CLITKA's **own** two files at a temp dir, for every single test.

    Autouse, because this is not a convenience - it is a correctness rule. Once
    `config.toml` grew `tree_types` and `default_window`, a test that merely
    *started the app* began reading the developer's real file: two
    `test_tui_window` tests failed because the owner's saved `default_window`
    replaced `timerange.DEFAULT`, and any test that saved would have rewritten a
    real config. A test must never see, or touch, either file.

    `CLITKA_CONFIG_DIR` and `CLITKA_STATE_DIR` exist for exactly this.
    """
    monkeypatch.setenv("CLITKA_CONFIG_DIR", str(tmp_path / "clitka-config"))
    monkeypatch.setenv("CLITKA_STATE_DIR", str(tmp_path / "clitka-state"))


SAMPLE_CONFIG = """\
[default]
region = eu-west-1

[sso-session corp]
sso_start_url = https://corp.awsapps.com/start/#
sso_region = eu-central-1
sso_registration_scopes = sso:account:access

[sso-session other]
sso_start_url = https://other.awsapps.com/start
sso_region = us-east-1

[profile root]
sso_session = corp
sso_account_id = 111122223333
sso_role_name = AdminRole
region = eu-central-1
output = json

[profile child]
source_profile = root
role_arn = arn:aws:iam::444455556666:role/DevRole
role_session_name = child-session

[profile legacy]
sso_start_url = https://legacy.awsapps.com/start
sso_region = eu-north-1
sso_account_id = 777788889999
sso_role_name = ReadOnly

[profile orphan]
sso_session = missing

[profile keys]
region = ap-south-1

[services shared]
dynamodb =
  endpoint_url = http://localhost:8000
"""

SAMPLE_CREDENTIALS = """\
[keys]
aws_access_key_id = AKIAEXAMPLE
aws_secret_access_key = secret

[only-in-credentials]
aws_access_key_id = AKIAOTHER
aws_secret_access_key = secret2
"""


@pytest.fixture
def aws_files(tmp_path: Path) -> tuple[Path, Path]:
    """A sample ~/.aws/config + ~/.aws/credentials pair."""
    config = tmp_path / "config"
    credentials = tmp_path / "credentials"
    config.write_text(SAMPLE_CONFIG, encoding="utf-8")
    credentials.write_text(SAMPLE_CREDENTIALS, encoding="utf-8")
    return config, credentials


@pytest.fixture
def sample_aws_config() -> str:
    """The raw text of the sample ~/.aws/config."""
    return SAMPLE_CONFIG


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point HOME, XDG_CONFIG_HOME and the AWS file env vars at a temp dir."""
    home = tmp_path / "home"
    (home / ".aws").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(home / ".aws" / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(home / ".aws" / "credentials"))
    for var in ("AWS_PROFILE", "AWS_REGION", "AWS_DEFAULT_REGION", "CLITKA_READ_ONLY"):
        monkeypatch.delenv(var, raising=False)
    return home
