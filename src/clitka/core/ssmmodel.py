"""What Systems Manager hands back, as things CLITKA can render.

No boto3 call in here - the same seam as `logsmodel.py`, `ec2model.py` and
`ecsmodel.py`, so the one decision that matters in this service is testable
without a network.

**That decision is `SecureString`.** A parameter's whole purpose can be to hold a
database password, and this app paints resources into a terminal that may be
shared, recorded or scrolled back through hours later. So:

- **nothing is ever decrypted unless the user asked for it in that exact call**
  (`--decrypt` on the CLI, never from the tree, never from a preview tab),
- an undecrypted `SecureString` reads as `MASK`, **not** as its ciphertext: the
  blob is useless to a human and looks like data worth pasting somewhere,
- `Parameter.decrypted` records which of the two happened, so a row can never
  claim to show a value it does not have.

`stamp` is reused from `logsmodel` rather than written again. The document half
lives in `core/ssmrunbook.py` - the 8 kB rule, and the two have nothing in common
beyond the service name.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from clitka.core.logsmodel import stamp

__all__ = [
    "MASK",
    "SECURE",
    "TIERS",
    "TYPES",
    "Parameter",
    "param_name_of",
    "parent_path",
    "stamp",
]

SECURE = "SecureString"

# What an undecrypted SecureString shows instead of its value. Deliberately not
# the ciphertext: AWS hands back a base64 blob, which is unreadable *and* looks
# like something worth copying.
MASK = "<SecureString, hidden>"

# The three parameter types AWS has. `StringList` is a comma-joined String.
TYPES: tuple[str, ...] = ("String", "StringList", SECURE)

# Standard is free and caps at 4 kB; advanced costs money, holds 8 kB and is the
# only tier that can have a policy. Intelligent-Tiering decides per parameter.
TIERS: tuple[str, ...] = ("Standard", "Advanced", "Intelligent-Tiering")


@dataclass(frozen=True)
class Parameter:
    """One Parameter Store entry, as the CLI, the tree and the preview show it.

    `value` may be empty for two entirely different reasons - `DescribeParameters`
    never returns values at all, and a `SecureString` was not decrypted - so
    `has_value` and `secret` answer them separately.
    """

    name: str
    type: str = "String"
    value: str = ""
    version: int = 0
    last_modified: dt.datetime | None = None
    last_modified_user: str = ""
    tier: str = ""
    key_id: str = ""
    data_type: str = ""
    description: str = ""
    allowed_pattern: str = ""
    decrypted: bool = False
    """True only when this value came back from a call that asked to decrypt."""

    @property
    def secret(self) -> bool:
        return self.type == SECURE

    @property
    def has_value(self) -> bool:
        """False when the value was never fetched - a listing returns metadata only."""
        return self.value != ""

    @property
    def label(self) -> str:
        """The last path segment - what a human calls it in a tree."""
        return self.name.rsplit("/", 1)[-1] or self.name

    @property
    def path(self) -> str:
        """The parameter's parent path, or "" for a name without one."""
        return parent_path(self.name)

    def display_value(self) -> str:
        """The value as it may be shown. A secret is masked unless it was decrypted.

        This is the only function any screen may use to render a value, which is
        what keeps the rule in one place rather than in every caller.
        """
        if self.secret and not self.decrypted:
            return MASK
        if not self.has_value:
            return ""
        return self.value

    def row(self) -> dict[str, Any]:
        """The explorer table row. `identifier` is the column every screen keys on."""
        return {
            "identifier": self.name,
            "type": self.type,
            "value": self.display_value(),
            "version": str(self.version or ""),
            "tier": self.tier,
            "modified": stamp(self.last_modified),
        }


def param_name_of(identifier: str) -> str:
    """A name or an ARN reduced to the plain parameter name.

    A parameter ARN is `arn:aws:ssm:<region>:<acct>:parameter/db/password` - note
    that AWS drops the leading slash of the name into the ARN's own separator, so
    it has to be put back or `GetParameter` answers `ParameterNotFound`.
    """
    marker = ":parameter/"
    if identifier.startswith("arn:") and marker in identifier:
        return "/" + identifier.split(marker, 1)[1]
    return identifier


def parent_path(name: str) -> str:
    """The path a parameter lives under, or "" when it has none.

    `/db/prod/password` -> `/db/prod`; a bare `password` -> "".
    """
    if "/" not in name:
        return ""
    head = name.rsplit("/", 1)[0]
    return head or "/"


def _self_check() -> None:
    # The rule this module exists for: a secret is masked unless it was decrypted.
    secret = Parameter("/db/prod/password", type=SECURE, value="AQICAHgc...", version=3)
    assert secret.secret and secret.display_value() == MASK
    assert "AQICAHgc" not in secret.display_value(), "the ciphertext must not leak"
    assert secret.row()["value"] == MASK
    told = Parameter("/db/prod/password", type=SECURE, value="hunter2", decrypted=True)
    assert told.display_value() == "hunter2"
    # A plain String is shown as-is, decrypted or not - there is nothing to hide.
    plain = Parameter("/app/region", value="eu-central-1")
    assert plain.display_value() == "eu-central-1" and not plain.secret

    # A listing returns metadata only, so "no value" and "hidden" must not be
    # the same answer.
    listed = Parameter("/app/region", type="String")
    assert not listed.has_value and listed.display_value() == ""
    assert not Parameter("/db/pw", type=SECURE).has_value

    assert secret.label == "password" and secret.path == "/db/prod"
    assert Parameter("bare").path == "" and Parameter("/top").path == "/"

    assert param_name_of("/db/pw") == "/db/pw"
    # The leading slash is eaten by the ARN separator and has to come back.
    arn = "arn:aws:ssm:eu-central-1:111122223333:parameter/db/prod/password"
    assert param_name_of(arn) == "/db/prod/password", param_name_of(arn)
    assert param_name_of("nameonly") == "nameonly"

    assert SECURE in TYPES and len(TYPES) == 3

    print("[OK] ssm model self-check passed")


if __name__ == "__main__":
    _self_check()
