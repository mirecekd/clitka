"""What F9 offers on an ECR repository, and what the preview pane shows.

Both are published through pluggy hooks, so the tree and the F9 menu never import
this module - they only ever see `Action` and `PreviewTab` objects.

Deleting is deliberately **not** an F9 action here. F9 acts on the *repository*,
and "delete images" needs a choice of which ones - there is no image to select in
the tree. The `Cleanup` action tells the user exactly how many untagged images
there are and hands over the command that removes them.
"""

from __future__ import annotations

from clitka.core import ecr
from clitka.core import preview as pv
from clitka.core.actions import Action, ActionResult, ResourceRef
from clitka.core.context import Context
from clitka.core.ecrmodel import human_size, repo_name_of, stamp

TYPE_NAME = "AWS::ECR::Repository"
IMAGE_LIMIT = 100


def is_repository(ref: ResourceRef) -> bool:
    return ref.type_name == TYPE_NAME


def repo_name(ref: ResourceRef) -> str:
    """The repository's name. Cloud Control identifies a repository *by* its name.

    A URI or an ARN is tolerated because a CLI argument or a hand-typed palette
    entry can supply one.
    """
    raw = ref.identifier or str(ref.row.get("RepositoryName", ""))
    return repo_name_of(raw)


def _lines(pairs: list[tuple[str, str]]) -> str:
    """Label/value pairs as an aligned block - the shape every tab here uses."""
    if not pairs:
        return "[dim](nothing to show)[/dim]"
    width = max(len(label) for label, _ in pairs)
    return "\n".join(f"[dim]{label:<{width}}[/dim]  {value}" for label, value in pairs)


def show_config(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: the repository's configuration, as `DescribeRepositories` reports it."""
    repo = ecr.get_repository(ctx, repo_name(ref))
    pairs = [
        ("uri", repo.uri or "-"),
        ("registry", repo.registry or "-"),
        ("tags", repo.tag_mutability.lower() or "-"),
        ("scan on push", "yes" if repo.scan_on_push else "no"),
        ("encryption", repo.encryption or "(default)"),
        ("created", stamp(repo.created) or "-"),
    ]
    return ActionResult(f"{repo.name} - configuration", _lines(pairs))


def _image_block(ctx: Context, name: str) -> tuple[str, int]:
    """The image listing as text, plus how many of them are untagged."""
    found = ecr.list_images(ctx, name, limit=IMAGE_LIMIT)
    if not found:
        return "[dim](no images in this repository)[/dim]", 0
    width = max(len(image.label) for image in found)
    rows = [
        f"{image.label:<{width}}  [dim]{human_size(image.size):>8}  "
        f"{stamp(image.pushed) or '-'}[/dim]"
        for image in found
    ]
    untagged = sum(1 for image in found if image.untagged)
    head = f"[dim]{len(found)} image(s), {untagged} untagged[/dim]"
    return f"{head}\n\n" + "\n".join(rows), untagged


def show_images(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: what is in the repository, newest push first."""
    name = repo_name(ref)
    body, _ = _image_block(ctx, name)
    return ActionResult(f"{name} - images", body)


def show_cleanup(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: how many untagged images there are, and the command that removes them.

    ponytail: the menu hands over the command instead of deleting. Ceiling: the
    user changes window. Upgrade path: an image-selection screen, which is also
    what `x` and the live tail wait for.
    """
    name = repo_name(ref)
    found = ecr.list_images(ctx, name)
    untagged = [image for image in found if image.untagged]
    where = f" -p {ctx.profile}" if ctx.profile else ""
    if not untagged:
        body = f"[dim]Nothing to clean up - all {len(found)} image(s) are tagged.[/dim]"
        return ActionResult(f"{name} - cleanup", body)
    freed = human_size(sum(image.size for image in untagged))
    return ActionResult(
        f"{name} - cleanup",
        f"{len(untagged)} untagged image(s), {freed} in total.\n\n"
        "Remove them from a shell:\n\n"
        f"  clitka{where} ecr images {name} --untagged\n"
        f"  clitka{where} ecr delete {name} --untagged\n\n"
        "The delete asks first; add `--yes` to skip that. It always goes by digest, "
        "so a tag that shares an image with another tag cannot be lost by accident.",
    )


def show_login(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: the `docker login` one-liner for this repository's registry."""
    name = repo_name(ref)
    try:
        registry = ecr.get_repository(ctx, name).registry
    except Exception:
        registry = ""
    return ActionResult(
        f"{name} - docker login",
        "Log docker in to this registry:\n\n"
        f"  {ecr.login_command(ctx, registry)}\n\n"
        "The token lasts 12 hours. Then push or pull as usual.",
    )


# The ids are namespaced, so two plugins can never collide in the F9 menu.
ACTIONS: tuple[Action, ...] = (
    Action(
        id="ecr.config",
        label="Configuration",
        run=show_config,
        key="c",
        applies_to=is_repository,
    ),
    Action(
        id="ecr.images",
        label="Images",
        run=show_images,
        key="m",
        applies_to=is_repository,
    ),
    Action(
        id="ecr.cleanup",
        label="Untagged images (cleanup)",
        run=show_cleanup,
        key="u",
        applies_to=is_repository,
    ),
    Action(
        id="ecr.login",
        label="docker login (how to)",
        run=show_login,
        key="g",
        applies_to=is_repository,
    ),
)


def build_images_tab(ctx: Context, ref: ResourceRef) -> str:
    """The `Images` preview tab - what is in the repository, beside the tree."""
    body, _ = _image_block(ctx, repo_name(ref))
    return body


PREVIEWS: tuple[pv.PreviewTab, ...] = (
    pv.PreviewTab(
        id="ecr.images",
        label="Images",
        build=build_images_tab,
        applies_to=is_repository,
        lazy=True,  # it calls DescribeImages
    ),
)


def _self_check() -> None:
    ref = ResourceRef.from_row(TYPE_NAME, {"identifier": "my-app"})
    assert is_repository(ref)
    assert not is_repository(ResourceRef.from_row("AWS::S3::Bucket", {}))
    assert repo_name(ref) == "my-app"
    # Cloud Control sometimes reports the name as a property, or as a URI/ARN.
    assert repo_name(ResourceRef(TYPE_NAME, "", {"RepositoryName": "other"})) == "other"
    uri = "1.dkr.ecr.eu-central-1.amazonaws.com/from-uri"
    assert repo_name(ResourceRef(TYPE_NAME, uri, {})) == "from-uri"

    ids = [action.id for action in ACTIONS]
    keys = [action.key for action in ACTIONS]
    assert len(set(ids)) == len(ids) and len(set(keys)) == len(keys), keys
    assert all(action.applies_to(ref) for action in ACTIONS)
    # Nothing here mutates anything, so nothing needs a confirm dialog - the
    # cleanup hands over the command rather than running the delete.
    assert not any(action.destructive for action in ACTIONS)

    assert _lines([("a", "1"), ("bbb", "2")]).count("\n") == 1
    assert "nothing to show" in _lines([])

    assert [tab.id for tab in PREVIEWS] == ["ecr.images"]
    assert all(tab.lazy and tab.matches_type(TYPE_NAME) for tab in PREVIEWS)
    print("[OK] ecr actions self-check passed")


if __name__ == "__main__":
    _self_check()
