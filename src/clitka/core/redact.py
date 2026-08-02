"""Properties that must not be shown, whatever asked for them. Boto3-free.

This exists because of a hole found live on `sw-sandbox` (2026-08-02): the SSM
plugin is careful never to decrypt a `SecureString`, but **`cloudcontrol`
`GetResource` hands back its `Value` anyway** - so `F3`, the Raw tab and
`clitka resources get` all printed a real KMS blob and walked straight around
`Parameter.display_value()`.

The lesson is the general one: **a rule enforced in one plugin is not enforced.**
The generic explorer speaks for every resource type, so anything that must never
reach a screen has to be dropped *here*, at the single seam where Cloud Control
properties enter the app.

ponytail: a small explicit table rather than a scan for secret-shaped strings.
Ceiling: only the types named below are covered, so a service that invents a new
secret-bearing property needs a line adding. Upgrade path: none wanted - guessing
which values are secret would be both leaky and surprising.
"""

from __future__ import annotations

from typing import Any

__all__ = ["MASK", "SECRET_PROPERTIES", "redact"]

# What a hidden value reads as. Deliberately not the ciphertext: a KMS blob is
# unreadable *and* looks like data worth pasting somewhere.
MASK = "<SecureString, hidden>"

# type name -> the properties never to show, and what has to be true for the
# type for the rule to bite. A `String` parameter's value is not a secret, so
# only a `SecureString` is masked.
SECRET_PROPERTIES: dict[str, tuple[str, ...]] = {
    "AWS::SSM::Parameter": ("Value",),
}

# The properties whose value decides whether the rule applies at all.
_ONLY_WHEN: dict[str, tuple[str, str]] = {
    # (property, value) - only mask a parameter that really is a SecureString
    "AWS::SSM::Parameter": ("Type", "SecureString"),
}


def redact(type_name: str, properties: dict[str, Any]) -> dict[str, Any]:
    """`properties` with anything secret replaced by `MASK`.

    Returns the same dict object when there is nothing to hide, so the common
    case costs one dict lookup - this runs for every row of every listing.
    """
    secrets = SECRET_PROPERTIES.get(type_name)
    if not secrets:
        return properties
    condition = _ONLY_WHEN.get(type_name)
    if condition is not None:
        key, wanted = condition
        if str(properties.get(key, "")) != wanted:
            return properties
    if not any(key in properties for key in secrets):
        return properties
    hidden = dict(properties)
    for key in secrets:
        if key in hidden:
            hidden[key] = MASK
    return hidden


def _self_check() -> None:
    # The case this module was written for: Cloud Control volunteers the value.
    raw = {"Type": "SecureString", "Value": "AQICAHgcBLOB", "Name": "/db/pw"}
    safe = redact("AWS::SSM::Parameter", raw)
    assert safe["Value"] == MASK
    assert "AQICAHgcBLOB" not in str(safe), safe
    # The input is not mutated - the caller may still hold it.
    assert raw["Value"] == "AQICAHgcBLOB"
    # Everything else about the resource survives.
    assert safe["Name"] == "/db/pw" and safe["Type"] == "SecureString"

    # A plain String is not a secret and must read normally.
    plain = {"Type": "String", "Value": "eu-central-1"}
    assert redact("AWS::SSM::Parameter", plain) is plain
    assert redact("AWS::SSM::Parameter", {"Type": "StringList", "Value": "a,b"})["Value"] == "a,b"

    # A type with nothing to hide is returned untouched, by identity.
    bucket = {"BucketName": "b1"}
    assert redact("AWS::S3::Bucket", bucket) is bucket
    # ...and so is a secret type that happens not to carry the property.
    listed = {"Type": "SecureString"}
    assert redact("AWS::SSM::Parameter", listed) is listed
    assert redact("AWS::SSM::Parameter", {}) == {}

    assert MASK and "hidden" in MASK
    print("[OK] redact self-check passed")


if __name__ == "__main__":
    _self_check()
