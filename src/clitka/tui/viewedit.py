"""`ViewEditHost`: what F3 (view) and F4 (edit) do on a selected resource.


The owner freed F3 and F4 by moving the context switches onto letters
(P / R / L), and asked for the two things a resource screen always needs:

- **F3 view** - the whole resource, fetched with `GetResource`, as YAML in the
  scrollable result screen. The preview pane only has the row the listing
  returned; this is the full shape.
- **F4 edit** - editing the resource. Only the types that have a real editor
  answer it; everything else says so instead of pretending.

Mixed into any screen that already has `context` and `selected_ref()` - the same
contract `ActionHost` uses, so a screen gets all three keys for one mixin line.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from clitka.core import actions as act
from clitka.core import cloudcontrol as cc
from clitka.core.context import Context
from clitka.core.output import jsonable

NOTHING = "Nothing selected - move the cursor onto a resource first."

# ponytail: F4 works for whatever a plugin claims (S3 objects today) and explains
# itself for everything else. Ceiling: Cloud Control's own `update_resource` is not
# wired up, so a bucket's *properties* still cannot be edited here. Upgrade path: a
# generic `Viewer.edit` in `services/resources` that round-trips the YAML.
EDIT_HINT = """\
Editing is not wired up for this type yet.

An S3 object opens in $EDITOR and is put back when you save; that is the shape the
rest will follow. For a Cloud Control resource the plan is to edit its properties
and call UpdateResource.

For now, F9 has the actions that do work on this resource, and F3 shows it in
full.
"""


@dataclass
class EditSession:
    """What a plugin hands back from `Viewer.edit`, ready for the terminal handoff.

    Two halves on purpose. `handoff` is the child that wants the terminal - built
    *before* the app suspends, so a missing `$EDITOR` or a read-only context is a
    sentence on screen rather than a surprise afterwards (the `ec2.power()` rule,
    and the handoff PoC's one real finding). `finish` runs after the editor exits
    and returns what to tell the user - "saved 412 bytes", or "unchanged".
    """

    handoff: Any
    finish: Callable[[], str]
    label: str = "Edit"


class ViewEditHost:
    """Mixed into a `Screen`. Expects `context`, `selected_ref()` and `_title()`."""

    context: Context

    def selected_ref(self) -> act.ResourceRef | None:  # pragma: no cover - overridden
        raise NotImplementedError

    def _title(self, text: str) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    # --- F3: view ---------------------------------------------------------

    def action_view(self) -> None:
        """F3: fetch the resource in full and show it as YAML."""
        ref = self.selected_ref()
        if ref is None:
            self._show_result("View", NOTHING)
            return

        self._title(f"{ref.identifier} - fetching...")
        self.run_worker(  # type: ignore[attr-defined]
            lambda: self._fetch_view(ref), thread=True, exclusive=False, group="view"
        )

    # Deliberately NOT called `_fetch`: `BranchLoader` already owns that name on
    # `ResourceTree`, and the mixin that lost the MRO race was called with the
    # other one's arguments. Every mixin method here is prefixed for that reason.
    def _fetch_view(self, ref: act.ResourceRef) -> None:
        try:
            body = view_yaml(self.context, ref)
        except Exception as exc:
            body = f"[red][ERROR] {exc}[/red]"
        self.app.call_from_thread(  # type: ignore[attr-defined]
            self._show_result, f"{ref.type_name}  {ref.identifier}", body
        )

    # --- F4: edit ---------------------------------------------------------

    def action_edit(self) -> None:
        """F4: hand the resource to `$EDITOR` - or say why that is not possible.

        The owner's question (2026-08-03) was whether read-only was in the way. It
        was not: F4 was wired up for **nothing at all**. Now a plugin can claim a
        type by giving its `Viewer` an `edit`, and everything unclaimed still
        explains itself rather than pretending.

        Every foreseeable complaint is raised **before** `app.suspend()`: read-only,
        a binary body, a missing `$EDITOR`. After the suspend the app cannot show a
        message any more - the handoff PoC's one real finding.
        """
        ref = self.selected_ref()
        if ref is None:
            self._show_result("Edit", NOTHING)
            return

        from clitka.core import viewer as vw

        claimed = vw.first_for(ref)
        if claimed is None or claimed.edit is None:
            self._show_result(f"F4  Edit {ref.type_name}  {ref.identifier}", EDIT_HINT)
            return

        title = f"Edit  {ref.identifier}"
        try:
            session = claimed.edit(self.context, ref)
        except Exception as exc:  # read-only, binary, no editor - all say why
            self._show_result(title, f"[red]{exc}[/red]")
            return

        gone = session.handoff.unavailable()
        if gone:
            self._show_result(session.label, f"[red]{gone}[/red]")
            return

        # `ShellHost._shell_run` owns the suspend dance, and every screen with F4
        # mixes it in as well - so the editor is launched exactly the way a shell is.
        runner = getattr(self, "_shell_run", None)
        if runner is None:  # pragma: no cover - a screen without ShellHost
            self._show_result(session.label, "[red]this screen cannot open an editor[/red]")
            return
        runner(session.handoff)

        try:
            outcome = session.finish()
        except Exception as exc:
            self._show_result(session.label, f"[red]{exc}[/red]")
            return
        self._show_result(session.label, outcome)

    # --- shared -----------------------------------------------------------

    def _show_result(self, title: str, body: str) -> None:

        from clitka.tui.resultview import ResultScreen

        self.app.push_screen(  # type: ignore[attr-defined]
            ResultScreen(self.context, act.ActionResult(title, body))
        )


def view_yaml(context: Context, ref: act.ResourceRef) -> str:
    """The full resource, as whatever reading it means for its type.

    **A plugin gets asked first** (`core/viewer.clitka_viewers`), because
    `GetResource` is the right answer only for a type Cloud Control knows. The
    owner's report: F3 on an S3 object showed its size and ETag, since
    `AWS::S3::Object` is CLITKA's own type string and there is nothing to fetch.
    Now `services/s3` claims it and F3 shows the file.

    Everything unclaimed behaves exactly as before: Cloud Control, then the
    listing row as a last resort - a type it cannot `GetResource` (or an identity
    without the permission) must still show *something*, and say which it is.
    """
    import yaml

    from clitka.core import viewer as vw

    claimed = vw.first_for(ref)
    if claimed is not None:
        return claimed.view(context, ref)

    try:
        resource = cc.get_resource(context, ref.type_name, ref.identifier)
        properties = resource.properties
        note = ""
    except Exception as exc:
        from clitka.tui.previewmodel import resource_from

        properties = resource_from(ref.type_name, ref.identifier, ref.row).properties
        note = f"# GetResource failed ({exc}) - showing the listing row instead\n"
    document = {"identifier": ref.identifier, **jsonable(properties)}
    return note + yaml.safe_dump(document, sort_keys=False, default_flow_style=False).rstrip()


def _self_check() -> None:
    """The contract: two actions, and a view that never raises."""
    for name in ("action_view", "action_edit"):
        assert callable(getattr(ViewEditHost, name)), name

    ref = act.ResourceRef.from_row(
        "AWS::S3::Bucket", {"identifier": "b1", "name": "b1", "Arn": "arn:x"}
    )
    # No credentials here, so GetResource fails - and the row must carry it.
    text = view_yaml(Context(profile="no-such-profile-exists"), ref)
    assert "identifier: b1" in text, text
    assert "Arn: arn:x" in text, text
    assert text.startswith("# GetResource failed"), text
    # The derived `name` column is not a property and must not leak into the YAML.
    assert "\nname:" not in text, text

    assert "F9" in EDIT_HINT and EDIT_HINT.endswith("\n")
    print("[OK] view/edit host self-check passed")


if __name__ == "__main__":
    _self_check()
