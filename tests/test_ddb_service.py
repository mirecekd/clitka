"""The DynamoDB plugin and the `Q` console, driven in the real app.

Two tests here are the ones worth having, and both are about a claim that reading the
code cannot settle:

- **`Q` must not steal `q` = quit.** The console needed a letter, every good one was
  taken, and upper-case `Q` sits next to the app's quit binding. Textual reports the
  two cases separately (measured with `run_test` before the key was chosen), but that
  is exactly the kind of fact that is true until someone writes `q,Q` to be helpful.
- **The cursor has to be on a table for `Q` to mean anything**, which is the ECS
  lesson: "it is in the app" and "the user can get to it" are different claims.
"""

from __future__ import annotations

import pytest

from clitka.core import actions as act
from clitka.core import ddbql
from clitka.core.context import Context, Identity
from clitka.tui import qlconsole
from clitka.tui.app import ClitkaApp
from clitka.tui.qltext import as_text, examples
from clitka.tui.restree import ResourceTree
from clitka.tui.resultview import ResultScreen

TABLE = qlconsole.TABLE_TYPE


@pytest.fixture
def ctx(monkeypatch):
    ident = Identity(account="123456789012", arn="arn:aws:iam::1:user/m", user_id="A")
    monkeypatch.setattr(Context, "identity_or_none", lambda _self: ident)
    return Context(profile="demo", region="eu-central-1")


# --- the plugin seam ------------------------------------------------------


def test_the_plugin_is_registered_as_the_tenth():
    from clitka.core import plugins

    assert "clitka.services.dynamodb" in plugins.BUILTIN_SERVICES
    assert "dynamodb" in [name for name, _ in plugins.service_apps()]


def test_the_plugin_brings_no_actions_listers_or_viewers():
    """Deliberately empty: Cloud Control already lists tables (PoC Q1).

    Asserting what this plugin does NOT publish is the point - a later hand adding a
    duplicate table listing or an `Action` that cannot ask for input would be caught.
    """
    from clitka.core import lister as ls
    from clitka.core import viewer as vw

    assert [one for one in act.registered() if one.id.startswith("dynamodb.")] == []
    assert [one for one in ls.registered() if one.id.startswith("dynamodb.")] == []
    assert [one for one in vw.registered() if one.id.startswith("dynamodb.")] == []


def test_the_table_type_is_a_real_cloud_control_type():
    """The PoC's Q1, as a test: the branch must come from the generic explorer."""
    from clitka.tui.restypes import COMMON_TYPES, TREE_TYPES

    assert TABLE in TREE_TYPES and TABLE in COMMON_TYPES


# --- what Q applies to ----------------------------------------------------


def test_a_table_ref_resolves_to_its_name():
    ref = act.ResourceRef.from_row(TABLE, {"identifier": "audience-resolution"})
    assert qlconsole.table_of(ref) == "audience-resolution"


def test_anything_that_is_not_a_table_resolves_to_nothing():
    bucket = act.ResourceRef.from_row("AWS::S3::Bucket", {"identifier": "b"})
    assert qlconsole.table_of(bucket) is None
    assert qlconsole.table_of(None) is None


def test_the_offered_examples_quote_the_table_name():
    """PartiQL reads `FROM my-table` as a syntax error, not a missing table."""
    for line in examples("my-table"):
        assert '"my-table"' in line


# --- the row block --------------------------------------------------------


def test_a_row_missing_an_attribute_shows_a_placeholder():
    """Schemaless means this WILL happen; shifted columns would misreport values."""
    page = ddbql.Page(rows=[{"pk": "a", "n": "3"}, {"pk": "b"}], statement="S")
    last = as_text(page).splitlines()[-1]
    assert "-" in last, last


def test_a_value_that_looks_like_markup_cannot_break_the_screen():
    """An unmatched closing tag raises `MarkupError` inside Rich - measured.

    Item data is exactly where such a string turns up, so this is a real crash and
    not a hypothetical one.
    """
    from rich.markup import MarkupError
    from rich.text import Text

    with pytest.raises(MarkupError):
        Text.from_markup("[/close]")
    body = as_text(ddbql.Page(rows=[{"pk": "[/close]"}], statement="S"))
    Text.from_markup(body)  # must not raise


# --- the app --------------------------------------------------------------


async def _settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


@pytest.mark.asyncio
async def test_upper_q_opens_the_console_and_lower_q_still_quits(ctx):
    """The key choice, driven rather than reasoned about.

    `q` is quit and `Q` is the console. If the binding were ever written `q,Q` the
    tree would swallow quit, and the only thing that notices is this test.
    """
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        screen = app.screen
        assert isinstance(screen, ResourceTree)

        # Nothing is selected, so `Q` explains itself rather than doing nothing.
        await pilot.press("Q")
        await _settle(app, pilot)
        assert isinstance(app.screen, ResultScreen), "Q must answer even with no selection"
        assert "PartiQL" in app.screen.result.title
        assert "DynamoDB table" in app.screen.result.body
        app.pop_screen()
        await _settle(app, pilot)

        # And lower-case q still quits - the whole reason the key is upper case.
        assert app.is_running
        await pilot.press("q")
        await pilot.pause()
    assert not app.is_running


@pytest.mark.asyncio
async def test_q_on_a_table_prompts_with_the_table_already_quoted(ctx, monkeypatch):
    """The cursor is on a table, so `Q` offers statements naming it.

    The palette is pushed rather than run: what is asserted is that the prompt opens
    with the right candidates, which is the whole contribution of the screen half.
    """
    import clitka.core.cloudcontrol as cc

    def fake_iter(_ctx, type_name, **_kw):
        assert type_name == TABLE
        yield cc.Resource(type_name=TABLE, identifier="audience-resolution", properties={})

    monkeypatch.setattr(cc, "iter_resources", fake_iter)

    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        screen = app.screen
        screen.add_type(TABLE)
        await _settle(app, pilot)

        for index, line in enumerate(screen.rtree._tree_lines):
            data = line.path[-1].data
            if getattr(getattr(data, "resource", None), "identifier", None) == (
                "audience-resolution"
            ):
                screen.rtree.cursor_line = index
                break
        else:
            raise AssertionError("the table is not in the tree")
        await pilot.pause()

        ref = screen.selected_ref()
        assert ref is not None and qlconsole.table_of(ref) == "audience-resolution"

        await pilot.press("Q")
        await _settle(app, pilot)
        from clitka.tui.picker import CommandPalette

        assert isinstance(app.screen, CommandPalette), "Q on a table must prompt"
        assert app.screen.candidates == examples("audience-resolution")


@pytest.mark.asyncio
async def test_an_unfinished_example_is_not_sent_to_aws(ctx):
    """The offered `... WHERE ` stub would only be a ValidationException."""
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        screen = app.screen

        def explode(*_a, **_k):
            raise AssertionError("an unfinished statement must not reach AWS")

        screen.context = ctx
        import clitka.tui.qlconsole as mod

        original = mod.ddbql.run
        mod.ddbql.run = explode  # type: ignore[assignment]
        try:
            screen._ql_typed('SELECT * FROM "t" WHERE ')
            await _settle(app, pilot)
        finally:
            mod.ddbql.run = original  # type: ignore[assignment]

        assert isinstance(app.screen, ResultScreen)
        assert "unfinished" in app.screen.result.body


@pytest.mark.asyncio
async def test_a_cancelled_prompt_does_nothing(ctx):
    """Escape on the prompt must not push a result screen or call AWS."""
    app = ClitkaApp(ctx)
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        screen = app.screen
        before = len(app.screen_stack)
        screen._ql_typed(None)
        screen._ql_typed("   ")
        await _settle(app, pilot)
        assert len(app.screen_stack) == before


# --- the CLI --------------------------------------------------------------


# Driven through the ROOT app (`clitka dynamodb ql ...`), not the plugin's own `app`.
# A Typer group with exactly ONE command collapses into that command when invoked
# standalone, so `invoke(plugin_app, ["ql", stmt])` reads "ql" as the statement and
# complains about an extra argument. Measured: s3 has 3 commands and ec2 has 5, which
# is why no earlier plugin hit this. The root app is what the user runs anyway, so
# this is the more honest test - and the last one here proves `ql` is still reachable.


def _run_cli(monkeypatch, outcome, argv):
    from typer.testing import CliRunner

    from clitka.cli.main import app

    def fake(*_a, **_k):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(ddbql, "run", fake)
    return CliRunner().invoke(app, argv)


def test_the_cli_runs_a_statement_and_prints_the_rows(monkeypatch):
    page = ddbql.Page(rows=[{"pk": "a", "n": "3"}], statement="S")
    result = _run_cli(monkeypatch, page, ["dynamodb", "ql", 'SELECT * FROM "t"'])
    assert result.exit_code == 0, result.output
    assert "pk" in result.output


def test_the_cli_exits_1_on_a_capped_answer(monkeypatch):
    """A partial answer that looks complete is worse than an error - the `s3 ls` rule."""
    page = ddbql.Page(rows=[{"pk": "a"}], statement="S", capped=True, next_token="more")
    result = _run_cli(monkeypatch, page, ["dynamodb", "ql", 'SELECT * FROM "t"'])
    assert result.exit_code == 1, result.output


def test_the_cli_reports_a_refusal_rather_than_crashing(monkeypatch):
    from clitka.core.errors import ReadOnlyError

    refusal = ReadOnlyError("refusing to run a PartiQL delete statement")
    result = _run_cli(monkeypatch, refusal, ["dynamodb", "ql", "DELETE FROM t"])
    assert result.exit_code == 1


def test_the_ql_command_is_reachable_through_the_root_cli():
    """The single-command collapse must not make `clitka dynamodb ql` unreachable."""
    from typer.testing import CliRunner

    from clitka.cli.main import app

    result = CliRunner().invoke(app, ["dynamodb", "--help"])
    assert result.exit_code == 0, result.output
    assert "ql" in result.output
