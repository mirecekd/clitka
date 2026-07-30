"""The ~/.aws/sso/cache token store, in the exact aws CLI v2 layout.

Sharing this directory is what makes `aws sso login` and `clitka auth login`
interchangeable in both directions, so the file name and the JSON keys are
dictated by aws CLI v2 and botocore, not by CLITKA:

- file name: `sha1(<sso-session name>).json` for `[sso-session x]` profiles,
  `sha1(<sso_start_url>).json` for the legacy inline layout
- keys: startUrl, region, accessToken, expiresAt, refreshToken, clientId,
  clientSecret, registrationExpiresAt, scopes
- timestamps: UTC, ISO 8601, `Z` suffix
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clitka.core.errors import ConfigError

# Refresh a little early; a token that dies mid-call is worse than one login.
_SKEW = dt.timedelta(seconds=60)


def sso_cache_dir() -> Path:
    """Path of ~/.aws/sso/cache, honouring AWS_SSO_CACHE_DIR (CLITKA extension)."""
    override = os.environ.get("AWS_SSO_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".aws" / "sso" / "cache"


def cache_key(value: str) -> str:
    """aws CLI v2 cache key: sha1 of the session name or the start URL.

    sha1 is not a security choice here - the file name layout is dictated by
    aws CLI v2 and must match byte for byte.
    """
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def cache_file(value: str) -> Path:
    return sso_cache_dir() / f"{cache_key(value)}.json"


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def to_aws_time(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_aws_time(value: str | None) -> dt.datetime | None:
    """Parse the timestamps aws CLI v2 and the AWS Toolkit write."""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


@dataclass(frozen=True)
class SsoToken:
    """One cache entry. `raw` keeps unknown keys so we never lose foreign data."""

    key: str
    path: Path
    raw: dict[str, Any]

    @property
    def access_token(self) -> str | None:
        return self.raw.get("accessToken")

    @property
    def start_url(self) -> str | None:
        return self.raw.get("startUrl")

    @property
    def region(self) -> str | None:
        return self.raw.get("region")

    @property
    def expires_at(self) -> dt.datetime | None:
        return parse_aws_time(self.raw.get("expiresAt"))

    @property
    def registration(self) -> tuple[str | None, str | None]:
        """(clientId, clientSecret) if the registration is still valid."""
        expires = parse_aws_time(self.raw.get("registrationExpiresAt"))
        if expires is not None and expires <= now():
            return None, None
        return self.raw.get("clientId"), self.raw.get("clientSecret")

    def is_valid(self, at: dt.datetime | None = None) -> bool:
        expires = self.expires_at
        if not self.access_token or expires is None:
            return False
        return expires - _SKEW > (at or now())

    def expires_in(self, at: dt.datetime | None = None) -> dt.timedelta | None:
        expires = self.expires_at
        return None if expires is None else expires - (at or now())


def read(key_value: str) -> SsoToken | None:
    """Load the cache entry for a session name / start URL, if any."""
    path = cache_file(key_value)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A corrupt or foreign file must not break `auth status`.
        return None
    if not isinstance(raw, dict):
        return None
    return SsoToken(key=cache_key(key_value), path=path, raw=raw)


def write(key_value: str, values: dict[str, Any]) -> SsoToken:
    """Merge `values` into the cache entry and write it back atomically, 0600."""
    path = cache_file(key_value)
    existing = read(key_value)
    merged = {**(existing.raw if existing else {}), **values}
    merged = {k: v for k, v in merged.items() if v is not None}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise ConfigError(f"cannot write {path}: {exc}") from exc
    return SsoToken(key=cache_key(key_value), path=path, raw=merged)


def forget(key_value: str, keep_registration: bool = True) -> bool:
    """Drop the access token. Returns True if something was removed.

    The client registration is kept by default (as aws CLI v2 does), so the next
    login does not have to register again.
    """
    token = read(key_value)
    if token is None:
        return False
    if not keep_registration:
        token.path.unlink(missing_ok=True)
        return True
    if not token.access_token:
        return False
    remaining = {
        k: v for k, v in token.raw.items() if k not in ("accessToken", "refreshToken", "expiresAt")
    }
    path = token.path
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(remaining, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return True


def _self_check() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["AWS_SSO_CACHE_DIR"] = tmp
        assert cache_key("trask") == "0213c69ee8fe69a1fbe5387186b7ccf390e031f6"
        assert read("demo") is None
        soon = now() + dt.timedelta(hours=1)
        token = write(
            "demo",
            {
                "startUrl": "https://demo.awsapps.com/start",
                "region": "eu-central-1",
                "accessToken": "secret",
                "expiresAt": to_aws_time(soon),
                "clientId": "cid",
                "clientSecret": "csecret",
            },
        )
        assert token.is_valid(), token.raw
        back = read("demo")
        assert back is not None and back.registration == ("cid", "csecret")
        assert forget("demo") is True
        left = read("demo")
        assert left is not None and left.access_token is None
        assert left.registration == ("cid", "csecret")
        assert forget("demo", keep_registration=False) is True
        assert read("demo") is None
    print("[OK] ssocache self-check passed")


if __name__ == "__main__":
    _self_check()
