"""What F9 offers on an SSM *document*. Split from `actions.py` for the 8 kB rule.

**Running a document is not an F9 action**, and this is the strongest case of that
rule in the whole app: `SendCommand` executes a shell script on someone's machine
and there is no undo. The EC2 power actions were allowed to mutate because a
stopped instance can be started again; a script that dropped a table cannot be
un-run. So F9 explains how, naming the parameters the document insists on - or
says why it could not be run at all.
"""

from __future__ import annotations

from clitka.core import ssm
from clitka.core.actions import ActionResult, ResourceRef
from clitka.core.context import Context

DOCUMENT = ssm.DOCUMENT


def is_document(ref: ResourceRef) -> bool:
    return ref.type_name == DOCUMENT


def doc_name(ref: ResourceRef) -> str:
    return ref.identifier or str(ref.row.get("Name", ""))


def lines(pairs: list[tuple[str, str]]) -> str:
    """Label/value pairs as an aligned block - the shape every tab here uses."""
    if not pairs:
        return "[dim](nothing to show)[/dim]"
    width = max(len(label) for label, _ in pairs)
    return "\n".join(f"[dim]{label:<{width}}[/dim]  {value}" for label, value in pairs)


def show_document(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: the document, including the parameters it insists on."""
    name = doc_name(ref)
    one = ssm.get_document(ctx, name)
    pairs = [
        ("type", one.document_type or "-"),
        ("owner", one.owner or "-"),
        ("version", one.version or "-"),
        ("status", one.status or "-"),
        ("platforms", one.platforms or "any"),
        ("runnable", "yes" if one.runnable else f"no - it is {one.document_type or 'unknown'}"),
    ]
    if one.parameters:
        pairs.append(("", ""))
        pairs.extend(("param", param.line()) for param in one.parameters)
    return ActionResult(f"{name} - document", lines(pairs))


def how_to_run(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: how to run this document - or why it cannot be run at all.

    Fetched fresh, because "is this runnable" is a property of the document and
    not of the possibly-stale tree row.
    """
    name = doc_name(ref)
    one = ssm.get_document(ctx, name)
    if not one.runnable:
        # A placeholder target, so the refusal is about the *document*: the
        # missing instance is not the answer worth having here.
        return ActionResult(
            f"{name} - cannot be run",
            f"[dim]{ssm.refuses_run(one, ['i-...'])}[/dim]",
        )
    wants = " ".join(f"-p {param}=..." for param in one.required)
    body = (
        f"[dim]Run it in a shell:[/dim]\n\n"
        f"  clitka ssm run {name} i-0123456789abcdef {wants}\n\n"
        "[dim]This is not offered as an action: SendCommand runs a script on the\n"
        "machine and there is no undo. Add --no-wait to return immediately, or\n"
        "leave it off and CLITKA exits 1 if the script failed.[/dim]"
    )
    return ActionResult(f"{name} - how to run it", body)


def build_document_tab(ctx: Context, ref: ResourceRef) -> str:
    return show_document(ctx, ref).body


def _self_check() -> None:
    ref = ResourceRef.from_row(DOCUMENT, {"identifier": "AWS-RunShellScript"})
    assert is_document(ref)
    assert not is_document(ResourceRef.from_row(ssm.PARAMETER, {}))
    assert doc_name(ref) == "AWS-RunShellScript"
    # A name that only arrives as a property must work too.
    assert doc_name(ResourceRef(DOCUMENT, "", {"Name": "mine"})) == "mine"

    assert lines([("a", "1"), ("bbb", "2")]).count("\n") == 1
    assert "nothing to show" in lines([])
    print("[OK] ssm document actions self-check passed")


if __name__ == "__main__":
    _self_check()
