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

from clitka.core import actions as act
from clitka.core import cloudcontrol as cc
from clitka.core.context import Context
from clitka.core.output import jsonable

NOTHING = "Nothing selected - move the cursor onto a resource first."

# ponytail: no editor yet. Ceiling: F4 explains itself rather than editing, on
# every type. Upgrade path: an `editors` hook in the shape of `clitka_previews`,
# so `services/s3` can publish "edit an object's body" and Cloud Control's
# `update_resource` can back a generic property editor.
EDIT_HINT = """\
Editing is not wired up for this type yet.

What F4 will do, per type:

  AWS::S3::Object          edit the object body and put it back
  anything Cloud Control    edit the properties and call UpdateResource

For now, F9 has the actions that do work on this resource, and F3 shows it in
full.
"""


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
        """F4: edit the resource - or say why that is not possible yet."""
        ref = self.selected_ref()
        if ref is None:
            self._show_result("Edit", NOTHING)
            return
        self._show_result(f"F4  Edit {ref.type_name}  {ref.identifier}", EDIT_HINT)

    # --- shared -----------------------------------------------------------

    def _show_result(self, title: str, body: str) -> None:

        from clitka.tui.resultview import ResultScreen

        self.app.push_screen(  # type: ignore[attr-defined]
            ResultScreen(self.context, act.ActionResult(title, body))
        )


def view_yaml(context: Context, ref: act.ResourceRef) -> str:
    """The full resource as YAML. Falls back to the row we already have.

    A type that Cloud Control cannot `GetResource` (or an identity without the
    permission) must still show *something* - the listing row is better than an
    error, and it says which one it is showing.
    """
    import yaml

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
