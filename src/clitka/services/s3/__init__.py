"""S3 as CLITKA's ninth pluggy plugin - and the second to bring its own listing.

The seam holds again: a CLI group, F9 actions, a preview tab and the tree
sub-branches, with **one line changed outside the package** - one entry in
`plugins.BUILTIN_SERVICES`. Nine for nine.

What is different here from the eight before it: S3 publishes `clitka_listers` as
well, and it is the first plugin whose children are **recursive**. A bucket holds
prefixes, a prefix holds prefixes, and one `ChildLister` covers every depth (see
`listers.py`). The ECS plugin needed the hook to go one level; this one proves it
goes as far down as the bucket does, which a PoC established before a line of this
was written.
"""

from __future__ import annotations

from typing import Any

from clitka.core.hookspecs import hookimpl
from clitka.services.s3.actions import ACTIONS, PREVIEWS
from clitka.services.s3.cli import app
from clitka.services.s3.listers import LISTERS
from clitka.services.s3.viewers import VIEWERS


@hookimpl
def clitka_service_name() -> str:
    return "s3"


@hookimpl
def clitka_cli_app() -> Any:
    return app


@hookimpl
def clitka_actions() -> list[Any]:
    """Contents / details / URI-and-commands, on a bucket, a prefix or an object."""
    return list(ACTIONS)


@hookimpl
def clitka_previews() -> list[Any]:
    """The `Contents` tab in the preview pane - lazy, so a leaf costs nothing."""
    return list(PREVIEWS)


@hookimpl
def clitka_listers() -> list[Any]:
    """The `Objects` sub-branch, which is what makes a bucket walkable in the tree."""
    return list(LISTERS)


@hookimpl
def clitka_viewers() -> list[Any]:
    """What F3 means on an object: the file itself.

    The owner asked for it in those words, and it is why the fifth hook exists -
    `GetResource` has no answer for a type CLITKA invented.
    """
    return list(VIEWERS)
