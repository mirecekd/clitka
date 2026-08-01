"""ECR: repositories, images and the one destructive thing worth having.

Same shape as `core/logs.py` and `core/lambdafn.py` - generators plus
`wrap_aws_errors`, with the boto3-free row types in `core/ecrmodel.py`.

The one thing about ECR that shaped this module: **`DescribeImages` is the useful
call, not `ListImages`.** `ListImages` returns only digests and tags; the size,
the push time and the scan verdict - the three things anyone actually looks at -
come from `DescribeImages`.

The write half (deleting images) and the `docker login` helper live in
`core/ecrops.py` for the 8 kB rule, and are re-exported here.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from clitka.core.context import Context
from clitka.core.ecrmodel import Image, Repository, moment, repo_name_of
from clitka.core.ecrops import delete_images, login_command
from clitka.core.errors import wrap_aws_errors

__all__ = [
    "PAGE",
    "Image",
    "Repository",
    "delete_images",
    "get_repository",
    "iter_images",
    "iter_repositories",
    "list_images",
    "list_repositories",
    "login_command",
    "repo_name_of",
]

PAGE = 50  # DescribeRepositories allows up to 1000, but 50 paints sooner


def _client(ctx: Context) -> Any:
    return ctx.client("ecr")


def _repo_from(raw: dict[str, Any]) -> Repository:
    """One `repository` entry as a `Repository`. Shared by list and get."""
    scanning = raw.get("imageScanningConfiguration") or {}
    encryption = raw.get("encryptionConfiguration") or {}
    return Repository(
        name=str(raw.get("repositoryName", "")),
        arn=str(raw.get("repositoryArn", "")),
        uri=str(raw.get("repositoryUri", "")),
        registry_id=str(raw.get("registryId", "")),
        created=moment(raw.get("createdAt")),
        tag_mutability=str(raw.get("imageTagMutability", "")),
        scan_on_push=bool(scanning.get("scanOnPush", False)),
        encryption=str(encryption.get("encryptionType", "")),
    )


def _image_from(raw: dict[str, Any], repository: str) -> Image:
    """One `imageDetails` entry as an `Image`."""
    findings = raw.get("imageScanFindingsSummary") or {}
    counts = findings.get("findingSeverityCounts") or {}
    status = (raw.get("imageScanStatus") or {}).get("status", "")
    return Image(
        digest=str(raw.get("imageDigest", "")),
        repository=repository or str(raw.get("repositoryName", "")),
        tags=tuple(str(one) for one in raw.get("imageTags", []) or ()),
        size=int(raw.get("imageSizeInBytes", 0) or 0),
        pushed=moment(raw.get("imagePushedAt")),
        manifest_type=str(raw.get("artifactMediaType", "")),
        scan_status=str(status),
        findings={str(key): int(value) for key, value in counts.items()} or None,
    )


# --- repositories ----------------------------------------------------------


def iter_repositories(ctx: Context, page_size: int = PAGE) -> Iterator[Repository]:
    """Yield every repository in the region, page by page."""
    client = _client(ctx)
    kwargs: dict[str, Any] = {"maxResults": page_size}
    token: str | None = None
    while True:
        if token:
            kwargs["nextToken"] = token
        page = _repos_page(ctx, client, kwargs)
        for raw in page.get("repositories", []):
            yield _repo_from(raw)
        token = page.get("nextToken")
        if not token:
            return


@wrap_aws_errors
def _repos_page(ctx: Context, client: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    return client.describe_repositories(**kwargs)


def list_repositories(ctx: Context, limit: int | None = None) -> list[Repository]:
    """Eager variant for the CLI."""
    out: list[Repository] = []
    for repo in iter_repositories(ctx):
        out.append(repo)
        if limit is not None and len(out) >= limit:
            break
    return out


@wrap_aws_errors
def get_repository(ctx: Context, name: str) -> Repository:
    """One repository by name, ARN or URI."""
    wanted = repo_name_of(name)
    page = _client(ctx).describe_repositories(repositoryNames=[wanted])
    for raw in page.get("repositories", []):
        return _repo_from(raw)
    raise LookupError(f"no ECR repository named {wanted!r}")


# --- images ----------------------------------------------------------------


def iter_images(ctx: Context, repository: str, page_size: int = PAGE) -> Iterator[Image]:
    """Yield the repository's images, newest push first.

    `DescribeImages` returns them in no useful order, so a page is sorted before
    it is handed on. That is per page, not globally - see the ponytail note.

    ponytail: sorted per page. Ceiling: with more than one page the order is
    "newest within each page", not newest overall. Upgrade path: collect
    everything then sort, which `list_images` already does.
    """
    client = _client(ctx)
    wanted = repo_name_of(repository)
    kwargs: dict[str, Any] = {"repositoryName": wanted, "maxResults": page_size}
    token: str | None = None
    while True:
        if token:
            kwargs["nextToken"] = token
        page = _images_page(ctx, client, kwargs)
        found = [_image_from(raw, wanted) for raw in page.get("imageDetails", [])]
        yield from sorted(found, key=_newest_first)
        token = page.get("nextToken")
        if not token:
            return


def _newest_first(image: Image) -> tuple[int, float]:
    """Sort key: pushed descending, with "no push time" last rather than crashing."""
    if image.pushed is None:
        return (1, 0.0)
    return (0, -image.pushed.timestamp())


@wrap_aws_errors
def _images_page(ctx: Context, client: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    return client.describe_images(**kwargs)


def list_images(ctx: Context, repository: str, limit: int | None = None) -> list[Image]:
    """Eager variant for the CLI - sorted across every page, newest push first."""
    found = sorted(iter_images(ctx, repository), key=_newest_first)
    return found if limit is None else found[:limit]


def _self_check() -> None:
    """The AWS shapes that would otherwise be found at runtime."""
    repo = _repo_from(
        {
            "repositoryName": "my-app",
            "repositoryArn": "arn:aws:ecr:eu-central-1:1:repository/my-app",
            "repositoryUri": "1.dkr.ecr.eu-central-1.amazonaws.com/my-app",
            "imageTagMutability": "IMMUTABLE",
            "imageScanningConfiguration": {"scanOnPush": True},
        }
    )
    assert repo.name == "my-app" and repo.scan_on_push and repo.region == "eu-central-1"
    # A repository with neither scanning nor encryption config must not crash.
    assert _repo_from({"repositoryName": "bare"}).scan_on_push is False

    # An untagged image is normal, and a missing scan summary is not an error.
    plain = _image_from({"imageDigest": "sha256:abc", "imageSizeInBytes": "10"}, "r")
    assert plain.untagged and plain.size == 10 and plain.findings is None
    scanned = _image_from(
        {
            "imageDigest": "sha256:def",
            "imageTags": ["latest"],
            "imageScanStatus": {"status": "COMPLETE"},
            "imageScanFindingsSummary": {"findingSeverityCounts": {"HIGH": 2}},
        },
        "r",
    )
    assert scanned.worst == "HIGH" and scanned.scan_status == "COMPLETE"

    # An image with no push time sorts last instead of raising on None.
    assert _newest_first(Image("sha256:a"))[0] == 1

    # The write half lives in ecrops but must stay reachable through this module,
    # which is what every caller imports.
    assert callable(delete_images) and callable(login_command)
    print("[OK] ecr self-check passed")


if __name__ == "__main__":
    _self_check()
