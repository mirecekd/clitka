"""`PreviewPane`: the 2/3 of the screen that shows what is under the tree cursor.

The owner's layout: 1/3 tree on the left, 2/3 detail on the right. The detail is
tabbed - "Overview" (grouped properties), "Raw" (the API shape) and whatever a
service publishes through `clitka_previews`.

Two deliberate rules, both from the owner:

- **Nothing is fetched on cursor movement.** `show()` is called by enter, space or
  a mouse click, never by `NodeHighlighted`, so holding the down arrow costs no
  API calls at all.
- Only the *visible* tab is built. A lazy tab (one that calls AWS) runs on a
  thread worker when it is first shown, and its result is cached per resource.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static, TabbedContent, TabPane

from clitka.core import preview as pv
from clitka.core.actions import ResourceRef
from clitka.core.context import Context
from clitka.tui import previewmodel as pm

EMPTY = "[dim]Press enter on a resource to preview it.[/dim]"
BUILDING = "[dim]loading...[/dim]"


# A plugin namespaces its tab ids with a dot, which Textual refuses in a widget
# id; `pm.slug` is what makes them legal. Re-exported for the tests.
slug = pm.slug


def core_tabs() -> list[pv.PreviewTab]:
    """The two tabs every resource gets, built from what the tree already has."""
    return [
        pv.PreviewTab(
            pv.OVERVIEW,
            "Overview",
            lambda _ctx, ref: pm.overview(_resource(ref)),
            lazy=False,
        ),
        pv.PreviewTab(
            pv.RAW,
            "Raw",
            lambda _ctx, ref: pm.raw_yaml(_resource(ref)),
            lazy=False,
        ),
    ]


def _resource(ref: ResourceRef):
    """Rebuild a `cc.Resource` from the row the tree carries - no API call."""
    return pm.resource_from(ref.type_name, ref.identifier, ref.row)


class PreviewPane(Vertical):
    """The tabbed detail pane. `show(ref)` is what fills it."""

    DEFAULT_CSS = """
    PreviewPane {
        width: 2fr;
        border-left: solid $primary;
    }
    PreviewPane #preview-heading {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    PreviewPane VerticalScroll {
        padding: 0 1;
    }
    """

    def __init__(self, context: Context) -> None:
        super().__init__()
        self.context = context
        self.ref: ResourceRef | None = None
        self.tabs: list[pv.PreviewTab] = []
        # (tab id, type name, identifier) -> already built markup
        self.cache: dict[tuple[str, str, str], str] = {}
        # tab id -> what that tab currently displays; see `body_text`
        self.shown: dict[str, str] = {}

        # `TabbedContent.clear_panes()` only *schedules* the removal, so a new
        # pane carrying the same id collides with the one still going away
        # (DuplicateIds on '--content-tab-...'). Every rebuild therefore gets a
        # fresh generation number and with it fresh widget ids.
        self.generation = 0

    def compose(self) -> ComposeResult:
        yield Static("Preview", id="preview-heading")
        with (
            TabbedContent(id="preview-tabs"),
            TabPane("Preview", id=self._tab_id("empty")),
            VerticalScroll(),
        ):
            yield Static(EMPTY, id=self._body_id("empty"))

    def _tab_id(self, tab_id: str) -> str:
        return f"tab-{self.generation}-{slug(tab_id)}"

    def _body_id(self, tab_id: str) -> str:
        return f"body-{self.generation}-{slug(tab_id)}"

    # --- filling ----------------------------------------------------------

    def show(self, ref: ResourceRef | None) -> None:
        """Preview `ref`. Called by enter/space/click only, never on cursor move."""
        self.ref = ref
        if ref is None:
            self._heading("Preview")
            self._rebuild([])
            return
        self._heading(f"{ref.type_name}  {ref.identifier}")
        self.tabs = core_tabs() + pv.available(pv.registered(), ref)
        self._rebuild(self.tabs)
        self._fill_active()

    def adopt_context(self, context: Context) -> None:
        self.context = context
        self.cache.clear()

    def _heading(self, text: str) -> None:
        try:
            self.query_one("#preview-heading", Static).update(text)
        except Exception:
            return

    def _rebuild(self, tabs: list[pv.PreviewTab]) -> None:
        """Replace the tab strip. Cheap: the bodies start as placeholders."""
        try:
            container = self.query_one("#preview-tabs", TabbedContent)
        except Exception:
            return
        container.clear_panes()
        self.generation += 1  # see __init__: the old panes are still being removed
        if not tabs:
            body = Static(EMPTY, id=self._body_id("empty"))
            container.add_pane(TabPane("Preview", VerticalScroll(body), id=self._tab_id("empty")))
            return
        for tab in tabs:
            body = Static(BUILDING, id=self._body_id(tab.id))
            container.add_pane(TabPane(tab.label, VerticalScroll(body), id=self._tab_id(tab.id)))

    # --- building the visible tab ----------------------------------------

    def on_tabbed_content_tab_activated(self, event) -> None:
        self._fill_active()

    def _active_tab(self) -> pv.PreviewTab | None:
        try:
            active = self.query_one("#preview-tabs", TabbedContent).active
        except Exception:
            return None
        for tab in self.tabs:
            if self._tab_id(tab.id) == active:
                return tab
        return self.tabs[0] if self.tabs else None

    def _fill_active(self) -> None:
        ref, tab = self.ref, self._active_tab()
        if ref is None or tab is None:
            return
        key = (tab.id, ref.type_name, ref.identifier)
        cached = self.cache.get(key)
        if cached is not None:
            self._write(tab.id, cached)
            return
        if not tab.lazy:
            self._build_now(tab, ref, key)
            return
        self._write(tab.id, BUILDING)
        self.run_worker(
            lambda: self._build_off_thread(tab, ref, key),
            thread=True,
            exclusive=False,
            group=f"preview-{tab.id}",
        )

    def _build_now(self, tab: pv.PreviewTab, ref: ResourceRef, key) -> None:
        """A non-lazy tab needs no worker - it only reformats what we already have."""
        try:
            text = tab.build(self.context, ref)
        except Exception as exc:
            text = f"[red][ERROR] {exc}[/red]"
        self.cache[key] = text
        self._write(tab.id, text)

    def _build_off_thread(self, tab: pv.PreviewTab, ref: ResourceRef, key) -> None:
        try:
            text = tab.build(self.context, ref)
        except Exception as exc:
            text = f"[red][ERROR] {exc}[/red]"
        self.app.call_from_thread(self._built, tab.id, ref, key, text)

    def _built(self, tab_id: str, ref: ResourceRef, key, text: str) -> None:
        self.cache[key] = text
        if self.ref is ref:  # the cursor may have moved on while we fetched
            self._write(tab_id, text)

    def _write(self, tab_id: str, text: str) -> None:
        self.shown[tab_id] = text
        try:
            self.query_one(f"#{self._body_id(tab_id)}", Static).update(text)
        except Exception:
            return  # the pane was rebuilt under us; the cache keeps the result

    def body_text(self, tab_id: str) -> str:
        """What a tab currently displays.

        `Static` has no readable `renderable` in Textual 8, so the pane keeps its
        own copy - which is also what makes the tests independent of a screen,
        the same trick as `line()` on the other widgets.
        """
        return self.shown.get(tab_id, "")
