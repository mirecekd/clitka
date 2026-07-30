"""Resolving "where do I log in" from ~/.aws/config, plus login status.

Split out of `clitka.core.sso` so that both files stay small and so that the
read-only part (which never touches the network) can be used on its own.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clitka.core import ssocache
from clitka.core.awsconfig import AwsConfig, SsoSession
from clitka.core.errors import ConfigError

DEFAULT_SCOPES = ("sso:account:access",)


@dataclass(frozen=True)
class SsoTarget:
    """Everything needed to log in: where, with what scopes, under which key."""

    key: str  # sso-session name, or the start URL for the legacy layout
    start_url: str
    region: str
    scopes: tuple[str, ...] = DEFAULT_SCOPES

    @property
    def cache_file(self) -> Path:
        return ssocache.cache_file(self.key)


def target_from_session(session: SsoSession) -> SsoTarget:
    """Turn an `[sso-session]` block into a login target."""
    if not session.start_url or not session.region:
        raise ConfigError(f"sso-session '{session.name}' is missing sso_start_url or sso_region")
    return SsoTarget(
        key=session.name,
        start_url=session.start_url,
        region=session.region,
        scopes=session.registration_scopes or DEFAULT_SCOPES,
    )


def targets(cfg: AwsConfig) -> list[SsoTarget]:
    """All login targets declared in ~/.aws/config, incomplete ones skipped."""
    out: list[SsoTarget] = []
    for name in sorted(cfg.sso_sessions):
        try:
            out.append(target_from_session(cfg.sso_sessions[name]))
        except ConfigError:
            continue
    return out


def target_for_profile(cfg: AwsConfig, profile: str) -> SsoTarget:
    """The login target a profile authenticates through (follows source_profile)."""
    session, holder = cfg.sso_for(profile)
    if session is None:
        raise ConfigError(f"profile '{holder.name}' does not use IAM Identity Center")
    resolved = target_from_session(session)
    if session.name == holder.name and holder.get("sso_start_url"):
        # Legacy inline layout: aws CLI v2 keys that cache file by the start URL.
        return SsoTarget(
            key=resolved.start_url,
            start_url=resolved.start_url,
            region=resolved.region,
            scopes=resolved.scopes,
        )
    return resolved


def humanize(delta: dt.timedelta | None) -> str | None:
    if delta is None:
        return None
    seconds = int(delta.total_seconds())
    if seconds <= 0:
        return "expired"
    hours, rest = divmod(seconds, 3600)
    return f"{hours}h{rest // 60:02d}m"


def status(cfg: AwsConfig) -> list[dict[str, Any]]:
    """Per sso-session login state, for `clitka auth status`."""
    rows: list[dict[str, Any]] = []
    for target in targets(cfg):
        token = ssocache.read(target.key)
        rows.append(
            {
                "sso_session": target.key,
                "start_url": target.start_url,
                "region": target.region,
                "valid": "yes" if token and token.is_valid() else "no",
                "expires_at": token.raw.get("expiresAt") if token else None,
                "expires_in": humanize(token.expires_in()) if token else None,
                "cache_file": target.cache_file.name,
            }
        )
    return rows
