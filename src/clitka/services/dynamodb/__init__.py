# src/clitka/services/dynamodb/__init__.py
"""DynamoDB as CLITKA's tenth pluggy plugin - and the smallest one yet.

Ten for ten: a CLI group with **one line changed outside the package**, one entry in
`plugins.BUILTIN_SERVICES`. What is different here is how little there is, and that
is the point of having done the PoC first (`/tmp/clitka-ddb-poc/findings.md`):

- **No `clitka_resource_kinds`, no listers, no viewers.** `AWS::DynamoDB::Table` is a
  real Cloud Control type (PoC Q1), already in `restypes.TREE_TYPES`, and
  `GetResource` already returns `KeySchema` and the GSIs - so the tree branch, F3 and
  the preview pane all worked before this plugin existed. Adding a second listing
  would only be a second thing to keep true.
- **No `clitka_actions`.** The console needs typed input and an `Action` cannot ask
  for any (it returns a finished `ActionResult` from a worker), so `Q` is a key on
  the resource screen - `tui/qlconsole.py`, next to `t` and `x`, which exist for the
  same reason.

So the hook this plugin answers is the CLI, and the TUI half is a screen mixin. The
key-condition builder, item browsing and item editing are deliberately **not** here:
the owner's call was "jen partiql, builder prozatim odsuneme".
"""

from __future__ import annotations

from typing import Any

from clitka.core.hookspecs import hookimpl
from clitka.services.dynamodb.cli import app


@hookimpl
def clitka_service_name() -> str:
    return "dynamodb"


@hookimpl
def clitka_cli_app() -> Any:
    return app
