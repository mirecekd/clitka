"""What F9 offers on an SSM parameter, and the whole plugin's tabs.

**Nothing here decrypts anything, and that is the design.** Revealing a
`SecureString` is not an F9 action for the same reason `lambda.invoke` is not one,
only more so: a keystroke is far too cheap for putting a production password onto
a screen that may be shared, recorded or scrolled back through an hour later. So
F9 offers `How to read the value`, which prints the `clitka ssm get ... --decrypt`
command - one deliberate paste, in a shell the user chose, rather than in a pane
that happened to be open.

The document half is `docactions.py` (the 8 kB rule); both are collected into
`ACTIONS` and `PREVIEWS` here, which is all the hook module sees.

Keys are checked against the baseline `resources.*` (`y j i d`) in the self-check -
that list applies to both of these types too, and `ActionMenu.on_key` runs the
*first* match.
"""

from __future__ import annotations

from clitka.core import preview as pv
from clitka.core import ssm
from clitka.core.actions import Action, ActionResult, ResourceRef
from clitka.core.context import Context
from clitka.core.ssmmodel import stamp
from clitka.services.ssm.docactions import (
    DOCUMENT,
    build_document_tab,
    how_to_run,
    is_document,
    lines,
    show_document,
)

PARAMETER = ssm.PARAMETER


def is_parameter(ref: ResourceRef) -> bool:
    return ref.type_name == PARAMETER


def param_name(ref: ResourceRef) -> str:
    """The parameter's name. Cloud Control identifies a parameter *by* its name."""
    raw = ref.identifier or str(ref.row.get("Name", ""))
    return ssm.param_name_of(raw)


def show_parameter(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: the parameter as it is now. A secret is shown masked."""
    name = param_name(ref)
    one = ssm.get_parameter(ctx, name, decrypt=False)
    pairs = [
        ("type", one.type),
        # display_value() is the only way a screen may render a value - that is
        # what keeps the SecureString rule in one place instead of in every caller.
        ("value", one.display_value() or "[dim](empty)[/dim]"),
        ("version", str(one.version)),
        ("data type", one.data_type or "-"),
        ("modified", stamp(one.last_modified) or "-"),
    ]
    if one.secret:
        pairs.append(("", "[dim]press n for how to read the real value[/dim]"))
    return ActionResult(f"{name} - parameter", lines(pairs))


def how_to_read(_ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: the command that shows a `SecureString`'s value - not the value."""
    name = param_name(ref)
    body = (
        f"[dim]Read it in a shell:[/dim]\n\n"
        f"  clitka ssm get {name} --decrypt\n\n"
        "[dim]CLITKA never decrypts a SecureString on its own - not in the tree,\n"
        "not in this pane. The value would sit on your screen for as long as the\n"
        "pane is open, and a keystroke is too cheap for that.[/dim]"
    )
    return ActionResult(f"{name} - how to read it", body)


def show_history(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: the recent versions. Values are never decrypted here."""
    name = param_name(ref)
    found = ssm.history(ctx, name, limit=10)
    if not found:
        return ActionResult(f"{name} - versions", "[dim](no history)[/dim]")
    pairs = [
        (f"v{one.version}", f"{one.display_value() or '(empty)'}  [dim]{one.type}[/dim]")
        for one in found
    ]
    return ActionResult(f"{name} - versions", lines(pairs))


def show_siblings(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: everything else under the same path - one app's whole config."""
    name = param_name(ref)
    path = ssm.parent_path(name)
    if not path:
        return ActionResult(f"{name} - path", "[dim]this parameter is not under a path[/dim]")
    found = ssm.by_path(ctx, path, recursive=False, decrypt=False)
    pairs = [(one.label, one.display_value() or "[dim](empty)[/dim]") for one in found]
    return ActionResult(f"{path} - {len(found)} parameter(s)", lines(pairs))


def build_parameter_tab(ctx: Context, ref: ResourceRef) -> str:
    """The `Parameter` preview tab - masked, like everything else that only looks."""
    return show_parameter(ctx, ref).body


# Ids are namespaced; keys are not, so the self-check compares them against
# everything else that applies to the same type.
ACTIONS: tuple[Action, ...] = (
    Action(
        id="ssm.parameter",
        label="Parameter",
        run=show_parameter,
        # NOT `d`: `resources.delete` owns that on every type with an identifier.
        key="e",
        applies_to=is_parameter,
    ),
    Action(id="ssm.versions", label="Versions", run=show_history, key="v", applies_to=is_parameter),
    Action(
        id="ssm.path",
        label="Others under this path",
        run=show_siblings,
        key="p",
        applies_to=is_parameter,
    ),
    Action(
        id="ssm.reveal",
        label="How to read the value",
        run=how_to_read,
        key="n",
        applies_to=is_parameter,
    ),
    Action(id="ssm.document", label="Document", run=show_document, key="e", applies_to=is_document),
    Action(id="ssm.run", label="How to run it", run=how_to_run, key="r", applies_to=is_document),
)

PREVIEWS: tuple[pv.PreviewTab, ...] = (
    pv.PreviewTab(
        id="ssm.parameter",
        label="Parameter",
        build=build_parameter_tab,
        applies_to=is_parameter,
        lazy=True,  # it calls GetParameter
    ),
    pv.PreviewTab(
        id="ssm.document",
        label="Document",
        build=build_document_tab,
        applies_to=is_document,
        lazy=True,
    ),
)


def _self_check() -> None:
    from clitka.services.ssm import docactions

    docactions._self_check()

    param = ResourceRef.from_row(PARAMETER, {"identifier": "/db/prod/password"})
    doc = ResourceRef.from_row(DOCUMENT, {"identifier": "AWS-RunShellScript"})
    assert is_parameter(param) and not is_parameter(doc)
    assert param_name(param) == "/db/prod/password"
    # An ARN, and a name that only arrives as a property, must both work.
    arn = "arn:aws:ssm:eu-central-1:1:parameter/db/prod/password"
    assert param_name(ResourceRef(PARAMETER, arn, {})) == "/db/prod/password"
    assert param_name(ResourceRef(PARAMETER, "", {"Name": "/other"})) == "/other"

    ids = [action.id for action in ACTIONS]
    assert len(set(ids)) == len(ids), ids

    # **Nothing here may mutate or decrypt**, so nothing is destructive: every
    # action either reads or hands over a command.
    assert not any(action.destructive for action in ACTIONS), "an SSM action mutates"
    told = how_to_read(Context(), param)
    assert "--decrypt" in told.body and "clitka ssm get" in told.body

    # Per type, no key may be claimed twice - the menu runs the first match, and
    # the baseline `resources.*` applies to both of these types as well.
    from clitka.services.resources.actions import ACTIONS as BASELINE

    for ref in (param, doc):
        mine = [action.key for action in ACTIONS if action.applies_to(ref)]
        assert len(set(mine)) == len(mine), f"duplicate key on {ref.type_name}: {mine}"
        taken = {action.key for action in BASELINE if action.applies_to(ref)}
        clash = set(mine) & taken
        assert not clash, f"key collision with resources.* on {ref.type_name}: {clash}"

    assert [tab.id for tab in PREVIEWS] == ["ssm.parameter", "ssm.document"]
    assert all(tab.lazy for tab in PREVIEWS)
    assert PREVIEWS[0].matches_type(PARAMETER) and PREVIEWS[1].matches_type(DOCUMENT)
    print("[OK] ssm actions self-check passed")


if __name__ == "__main__":
    _self_check()
