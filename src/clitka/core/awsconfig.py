"""Read-only view of ~/.aws/config and ~/.aws/credentials.

Those files are INI, so stdlib `configparser` does the parsing; CLITKA only adds
the AWS section naming rules ("profile x", "sso-session y") and the resolution of
a profile to its SSO session. CLITKA never writes here - its own settings live in
`clitka.core.clitkaconfig`.
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path

from clitka.core.errors import ConfigError

_MAX_CHAIN = 10


def aws_config_path() -> Path:
    """Path of ~/.aws/config, honouring AWS_CONFIG_FILE."""
    override = os.environ.get("AWS_CONFIG_FILE")
    return Path(override).expanduser() if override else Path.home() / ".aws" / "config"


def aws_credentials_path() -> Path:
    """Path of ~/.aws/credentials, honouring AWS_SHARED_CREDENTIALS_FILE."""
    override = os.environ.get("AWS_SHARED_CREDENTIALS_FILE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".aws" / "credentials"


@dataclass(frozen=True)
class SsoSession:
    """An `[sso-session name]` block."""

    name: str
    start_url: str | None = None
    region: str | None = None
    registration_scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Profile:
    """One profile, whatever file it came from."""

    name: str
    origin: str = "config"  # "config" | "credentials"
    settings: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> str | None:
        return self.settings.get(key)

    @property
    def region(self) -> str | None:
        return self.get("region")

    @property
    def sso_session(self) -> str | None:
        return self.get("sso_session")

    @property
    def sso_account_id(self) -> str | None:
        return self.get("sso_account_id")

    @property
    def sso_role_name(self) -> str | None:
        return self.get("sso_role_name")

    @property
    def source_profile(self) -> str | None:
        return self.get("source_profile")

    @property
    def role_arn(self) -> str | None:
        return self.get("role_arn")

    @property
    def kind(self) -> str:
        """Rough classification, good enough for a table column."""
        if self.sso_session or self.get("sso_start_url"):
            return "sso"
        if self.role_arn:
            return "assume-role"
        if self.get("credential_process"):
            return "process"
        if self.get("aws_access_key_id"):
            return "static"
        return "other"


@dataclass(frozen=True)
class AwsConfig:
    """Parsed ~/.aws/config + ~/.aws/credentials."""

    profiles: dict[str, Profile] = field(default_factory=dict)
    sso_sessions: dict[str, SsoSession] = field(default_factory=dict)

    def profile(self, name: str) -> Profile:
        try:
            return self.profiles[name]
        except KeyError:
            raise ConfigError(f"profile '{name}' not found in ~/.aws/config") from None

    def sso_for(self, profile_name: str) -> tuple[SsoSession | None, Profile]:
        """Which SSO session a profile ultimately authenticates through.

        Follows `source_profile` chains, because an assume-role profile is
        usually rooted in an SSO profile. Returns (session or None, holder).
        """
        seen: set[str] = set()
        current = self.profile(profile_name)
        for _ in range(_MAX_CHAIN):
            if current.name in seen:
                raise ConfigError(f"circular source_profile chain at '{current.name}'")
            seen.add(current.name)
            session_name = current.sso_session
            if session_name:
                session = self.sso_sessions.get(session_name)
                if session is None:
                    raise ConfigError(
                        f"profile '{current.name}' references unknown sso-session '{session_name}'"
                    )
                return session, current
            if current.get("sso_start_url"):
                # Legacy pre-sso-session layout: the session data is inline.
                legacy = SsoSession(
                    name=current.name,
                    start_url=current.get("sso_start_url"),
                    region=current.get("sso_region"),
                )
                return legacy, current
            parent = current.source_profile
            if not parent or parent not in self.profiles:
                return None, current
            current = self.profiles[parent]
        raise ConfigError(f"source_profile chain too deep for '{profile_name}'")

    def summary(self) -> list[dict[str, str | None]]:
        """Flat rows for `clitka ctx profiles`."""
        rows: list[dict[str, str | None]] = []
        for name in sorted(self.profiles):
            prof = self.profiles[name]
            try:
                session, _ = self.sso_for(name)
            except ConfigError:
                session = None
            rows.append(
                {
                    "profile": name,
                    "kind": prof.kind,
                    "region": prof.region,
                    "account": prof.sso_account_id,
                    "role": prof.sso_role_name,
                    "sso_session": session.name if session else None,
                }
            )
        return rows


def _read_ini(path: Path) -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser()
    # AWS keys are case sensitive in practice; keep them verbatim.
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    if path.is_file():
        try:
            parser.read(path, encoding="utf-8")
        except configparser.Error as exc:
            raise ConfigError(f"cannot parse {path}: {exc}") from exc
    return parser


def _scopes(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def load_aws_config(
    config_path: Path | None = None,
    credentials_path: Path | None = None,
) -> AwsConfig:
    """Parse both AWS files. Missing files are simply empty, not an error."""
    profiles: dict[str, Profile] = {}
    sso_sessions: dict[str, SsoSession] = {}

    cfg = _read_ini(config_path or aws_config_path())
    for section in cfg.sections():
        head, _, tail = section.partition(" ")
        name = tail.strip() or head
        values = dict(cfg.items(section))
        if head == "profile" and tail:
            profiles[name] = Profile(name=name, origin="config", settings=values)
        elif head == "sso-session" and tail:
            sso_sessions[name] = SsoSession(
                name=name,
                start_url=values.get("sso_start_url"),
                region=values.get("sso_region"),
                registration_scopes=_scopes(values.get("sso_registration_scopes")),
            )
        elif section == "default":
            profiles["default"] = Profile(name="default", origin="config", settings=values)
        # ponytail: other section types are ignored. Ceiling: `[services x]`
        # blocks are not honoured; upgrade path is one more dict here.

    creds = _read_ini(credentials_path or aws_credentials_path())
    for section in creds.sections():
        values = dict(creds.items(section))
        existing = profiles.get(section)
        if existing is None:
            profiles[section] = Profile(name=section, origin="credentials", settings=values)
        else:
            merged = {**values, **existing.settings}
            profiles[section] = Profile(name=section, origin=existing.origin, settings=merged)

    return AwsConfig(profiles=profiles, sso_sessions=sso_sessions)


def _self_check() -> None:
    cfg = load_aws_config()
    print(f"[OK] {len(cfg.profiles)} profiles, {len(cfg.sso_sessions)} sso-sessions")
    for row in cfg.summary()[:5]:
        print(f"  {row['profile']:<28} {row['kind']:<12} {row['sso_session'] or '-'}")


if __name__ == "__main__":
    _self_check()
