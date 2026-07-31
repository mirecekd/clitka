"""The single source of truth for which profile / region / account we act on.

Nothing in CLITKA creates a boto3 client on its own; everything goes through a
Context, which makes the header bar truthful and the tests stubbable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from functools import cached_property
from typing import Any

import boto3
from botocore.config import Config

from clitka.core import clitkaconfig
from clitka.core.errors import ConfigError, ReadOnlyError, wrap_aws_errors

_USER_AGENT_SUFFIX = "clitka"
_TRUTHY = ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Identity:
    """Result of sts:GetCallerIdentity."""

    account: str
    arn: str
    user_id: str

    @property
    def display(self) -> str:
        return self.arn.rsplit("/", 1)[-1] or self.arn


@dataclass
class Context:
    """Immutable-ish operating context. Use `with_` helpers to derive variants."""

    profile: str | None = None
    region: str | None = None
    read_only: bool = False
    source: dict[str, str] = field(default_factory=dict, repr=False, compare=False)
    _clients: dict[tuple[str, str | None], Any] = field(
        default_factory=dict, repr=False, compare=False
    )

    @classmethod
    def from_env(cls, profile: str | None = None, region: str | None = None) -> Context:
        """Resolve the context. Priority: CLI flag > env var > CLITKA config > AWS default.

        "AWS default" means: leave the value unset and let botocore resolve it,
        so `~/.aws/config` stays the single source of truth for defaults.
        """
        saved = clitkaconfig.load()
        source: dict[str, str] = {}

        env_profile = os.environ.get("AWS_PROFILE")
        env_region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        env_read_only = os.environ.get("CLITKA_READ_ONLY", "").lower() in _TRUTHY

        for key, values in (
            ("profile", (("flag", profile), ("env", env_profile), ("config", saved.profile))),
            ("region", (("flag", region), ("env", env_region), ("config", saved.region))),
        ):
            source[key] = "aws"
            for origin, value in values:
                if value:
                    source[key] = origin
                    break

        resolved_profile = profile or env_profile or saved.profile
        resolved_region = region or env_region or saved.region
        source["read_only"] = "env" if env_read_only else ("config" if saved.read_only else "aws")
        return cls(
            profile=resolved_profile,
            region=resolved_region,
            read_only=env_read_only or saved.read_only,
            source=source,
        )

    def with_profile(self, profile: str | None) -> Context:
        return replace(self, profile=profile, _clients={})

    def with_region(self, region: str | None) -> Context:
        return replace(self, region=region, _clients={})

    def renewed(self) -> Context:
        """A twin of this context with every cached thing thrown away.

        Needed after a fresh SSO login: the boto3 session and its clients are
        holding on to the credential resolver that already failed, and the
        identity cache still says "unauthenticated". Building a new Context is
        cheaper and more honest than trying to invalidate them one by one.
        """
        return Context(
            profile=self.profile,
            region=self.region,
            read_only=self.read_only,
            source=dict(self.source),
        )

    @cached_property
    def session(self) -> boto3.Session:
        try:
            return boto3.Session(profile_name=self.profile, region_name=self.region)
        except Exception as exc:  # botocore raises several unrelated types here
            raise ConfigError(f"cannot create AWS session (profile={self.profile}): {exc}") from exc

    @property
    def effective_region(self) -> str | None:
        return self.region or self.session.region_name

    def client(self, service: str, region: str | None = None) -> Any:
        """Return a cached boto3 client for `service`.

        Clients are created lazily; creating them is the expensive part of
        boto3 startup, so we never build one we do not use.
        """
        key = (service, region)
        cached = self._clients.get(key)
        if cached is not None:
            return cached
        cfg = Config(
            retries={"mode": "adaptive", "max_attempts": 5},
            user_agent_extra=_USER_AGENT_SUFFIX,
        )
        client = self.session.client(service, region_name=region or self.region, config=cfg)
        self._clients[key] = client
        return client

    def require_write(self, what: str) -> None:
        if self.read_only:
            raise ReadOnlyError(f"refusing to {what}: CLITKA is running in read-only mode")

    @wrap_aws_errors
    def _call_identity(self) -> dict[str, Any]:
        return self.client("sts").get_caller_identity()

    def identity(self) -> Identity:
        """Resolve the caller identity. Result is cached per Context instance."""
        cached = getattr(self, "_identity", None)
        if cached is not None:
            return cached
        raw = self._call_identity()
        ident = Identity(
            account=str(raw.get("Account", "")),
            arn=str(raw.get("Arn", "")),
            user_id=str(raw.get("UserId", "")),
        )
        self._identity = ident
        return ident

    def identity_or_none(self) -> Identity | None:
        """Same as identity(), but never raises - for the header bar."""
        try:
            return self.identity()
        except Exception:
            return None

    def describe(self) -> dict[str, str]:
        """Flat description for `clitka ctx show` and the TUI header."""
        ident = self.identity_or_none()
        return {
            "profile": self.profile or "(default)",
            "profile_from": self.source.get("profile", "aws"),
            "region": self.effective_region or "(unset)",
            "account": ident.account if ident else "(unknown)",
            "identity": ident.display if ident else "(unauthenticated)",
            "read_only": "yes" if self.read_only else "no",
        }
