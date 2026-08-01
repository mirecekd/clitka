"""The two ECR operations that are not a listing: deleting images, and logging in.

Split out of `core/ecr.py` for the 8 kB rule, and it turned out to be the right
seam anyway: this is the *write* half plus the one thing CLITKA deliberately does
not do itself. Both are re-exported from `core/ecr.py`, which is what callers
import.
"""

from __future__ import annotations

from typing import Any

from clitka.core.context import Context
from clitka.core.ecrmodel import repo_name_of
from clitka.core.errors import wrap_aws_errors

__all__ = ["delete_images", "login_command"]


def delete_images(ctx: Context, repository: str, digests: list[str]) -> dict[str, Any]:
    """Delete images by digest. Returns {"deleted": [...], "failures": [...]}.

    `BatchDeleteImage` never raises for an image it could not delete - it answers
    200 with a `failures` list, the same trap as Lambda's HTTP 200. The caller has
    to read both halves, so both are returned.

    Always by **digest**: `BatchDeleteImage` accepts a tag, but deleting by tag
    removes the image every other tag also points at, which is how people lose
    `latest` and `v3` in one keystroke. See `Image.reference`.
    """
    wanted = repo_name_of(repository)
    if not digests:
        raise ValueError("no image digest given")
    ctx.require_write(f"delete {len(digests)} image(s) from {wanted}")
    raw = _delete_call(ctx, wanted, digests)
    return {
        "deleted": [str(one.get("imageDigest", "")) for one in raw.get("imageIds", [])],
        "failures": [_failure(one) for one in raw.get("failures", [])],
    }


def _failure(one: dict[str, Any]) -> str:
    """One `failures` entry as a sentence: which image, and why not."""
    digest = (one.get("imageId") or {}).get("imageDigest", "?")
    code = one.get("failureCode", "?")
    reason = one.get("failureReason", "")
    return f"{digest}: {code} {reason}".strip()


@wrap_aws_errors
def _delete_call(ctx: Context, repository: str, digests: list[str]) -> dict[str, Any]:
    return ctx.client("ecr").batch_delete_image(
        repositoryName=repository,
        imageIds=[{"imageDigest": digest} for digest in digests],
    )


def login_command(ctx: Context, registry: str = "") -> str:
    """The `docker login` one-liner for this registry.

    ponytail: this hands over the command rather than running it. Ceiling: the
    user pastes one line. Upgrade path: `get_authorization_token` plus a handoff
    to `docker login --password-stdin` - but a token in this process's memory and
    then in a subprocess argv is a worse trade than one paste.
    """
    where = f" --profile {ctx.profile}" if ctx.profile else ""
    region = f" --region {ctx.effective_region}" if ctx.effective_region else ""
    target = registry or "<account>.dkr.ecr.<region>.amazonaws.com"
    return (
        f"aws ecr get-login-password{where}{region} "
        f"| docker login --username AWS --password-stdin {target}"
    )


def _self_check() -> None:
    # A delete with nothing to delete must not reach AWS - and must complain
    # before the read-only guard does, since "nothing" is not a write.
    try:
        delete_images(Context(region="eu-central-1"), "my-app", [])
    except ValueError as exc:
        assert "no image digest" in str(exc), exc
    else:
        raise AssertionError("an empty delete should have been refused")

    # A failure entry that is missing every field must still read as a sentence.
    assert _failure({}) == "?: ?"
    assert "ImageNotFound" in _failure(
        {"imageId": {"imageDigest": "sha256:a"}, "failureCode": "ImageNotFound"}
    )

    command = login_command(Context(profile="sw-sandbox", region="eu-central-1"), "reg.example")
    assert "--profile sw-sandbox" in command and command.endswith("reg.example")
    assert "--region eu-central-1" in command and "--password-stdin" in command
    # No profile means no --profile flag, not "--profile None".
    assert "--profile" not in login_command(Context(region="eu-west-1"))
    print("[OK] ecr ops self-check passed")


if __name__ == "__main__":
    _self_check()
