"""`BranchPicker`: multi-select over resource types, for the `C` panel.

Split out of `tui/configpanel.py` for the 8 kB rule, and the seam is a real one:
this file is a *widget* that answers "which types", and `configpanel.py` is the
panel that decides what to do with the answer.

`DropMenu` dismisses as soon as something is picked, which is right for choosing
a profile and wrong for editing a set. This subclass keeps the panel open and
dismisses with the **whole list**, so the caller writes the file once rather than
once per keystroke.
"""

from __future__ import annotations

from contextlib import suppress

from textual.widgets import Input, ListView

from clitka.tui import configmodel as cm
from clitka.tui.dropmenu import DropMenu


class BranchPicker(DropMenu):
    """`space` toggles the type under the cursor, `escape` is done.

    Two deliberate departures from `DropMenu`:

    - **`TOGGLE_KEY` is empty.** `c` has to reach the filter box, or nobody could
      search for `AWS::CloudFront::Distribution`.
    - **The keyboard starts on the list, not on the filter box.** `space` is the
      verb here, and a focused `Input` swallows it as a literal space - so the set
      could never be edited at all. `/` moves to the filter, which is the key the
      explorer already uses for the same job.
    """

    def __init__(self, candidates, chosen) -> None:
        self.chosen = list(chosen)
        super().__init__(
            cm.BRANCH_TITLE,
            cm.branch_items(candidates, self.chosen),
            "",
            cm.BRANCH_HINT,
        )
        self.candidates = list(candidates)

    def on_mount(self) -> None:
        super().on_mount()
        # Taking the focus back has to happen *after* the refresh: `_refill` runs
        # during mount and hands it to the Input again otherwise. Found the hard
        # way - `space` silently did nothing.
        self.call_after_refresh(self._focus_list)

    def _focus_list(self) -> None:
        with suppress(Exception):
            self.query_one(ListView).focus()

    def _typing(self) -> bool:
        return self.filtered and self.query_one(Input).has_focus

    def on_key(self, event) -> None:
        if self._typing():
            if event.key == "escape":
                # Leave the filter, keep the set - escape only closes from the list.
                event.stop()
                event.prevent_default()
                self._focus_list()
                return
            super().on_key(event)
            return
        if event.key == "space":
            event.stop()
            event.prevent_default()
            self.toggle_cursor()
            return
        if event.key == "slash" and self.filtered:
            event.stop()
            event.prevent_default()
            self.query_one(Input).focus()
            return
        super().on_key(event)

    def on_list_view_selected(self, _event) -> None:
        """Enter on a row toggles it too - it is the obvious thing to press."""
        self.toggle_cursor()

    def on_input_submitted(self, _event) -> None:
        """Enter in the filter box moves to the list rather than accepting."""
        self._focus_list()

    def toggle_cursor(self) -> None:
        listing = self.query_one(ListView)
        index = listing.index
        if not self.matches or index is None or not 0 <= index < len(self.matches):
            return
        self.chosen = cm.toggle_branch(self.chosen, self.matches[index].value)
        # Rebuild the rows so the mark and the "in the tree" note stay truthful,
        # then put the cursor back where the user left it: a list that jumps to
        # the top on every toggle is unusable for picking ten types.
        self.items = cm.branch_items(self.candidates, self.chosen)
        self.repick(index)

    def repick(self, index: int) -> None:
        """Refill the rows and keep the cursor near where it was."""
        needle = self.query_one(Input).value if self.filtered else ""
        self._refill(needle)
        listing = self.query_one(ListView)
        if self.matches:
            listing.index = min(index, len(self.matches) - 1)

    def offer(self, candidates: list[str]) -> None:
        """A later `ListTypes` landed - widen the choice without losing the set."""
        self.candidates = sorted(set(candidates) | set(self.chosen))
        self.items = cm.branch_items(self.candidates, self.chosen)
        self.repick(0)

    def action_accept(self) -> None:
        """There is nothing to "accept" - a set is finished by closing it."""
        self.toggle_cursor()

    def action_close(self) -> None:
        self.dismiss(list(self.chosen))


def _self_check() -> None:
    # The picker must never be a one-shot: `accept` toggles, `close` hands back
    # the whole list. A regression here would save one type and drop the rest.
    assert BranchPicker.action_accept is not DropMenu.action_accept
    assert BranchPicker.action_close is not DropMenu.action_close

    picker = BranchPicker(["AWS::S3::Bucket", "AWS::SQS::Queue"], ["AWS::S3::Bucket"])
    assert picker.chosen == ["AWS::S3::Bucket"]
    # The chosen type is listed first, and marked.
    assert picker.items[0].value == "AWS::S3::Bucket"
    assert picker.items[0].current is True
    # `c` must reach the filter box, so this panel has no toggle key of its own.
    assert picker.TOGGLE_KEY == "", picker.TOGGLE_KEY
    print("[OK] branch picker self-check passed")


if __name__ == "__main__":
    _self_check()
