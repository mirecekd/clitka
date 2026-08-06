"""PartiQL: the paging loop, the write guard, and the row shape.

The test that earns its place is `test_an_empty_page_does_not_end_the_walk`. The PoC
measured (`/tmp/clitka-ddb-poc/`, Q3) that a statement whose filter matches nothing
on the current page still comes back with a `NextToken` - so the obvious loop,
`while items:`, reports "no rows" for a table whose matches all sit further along.
That is a bug that would have shipped and been very hard to see.

Stubbed with `botocore.stub.Stubber`, the ECR/S3 pattern, so the wire shape is
validated both ways - a lesson from `test_s3`: `Stubber` checks the *response*
against the output shape too, so a stub cannot lie about what AWS may return.
"""

from __future__ import annotations

import json

import pytest
from botocore.stub import Stubber

from clitka.core import ddbql
from clitka.core.context import Context
from clitka.core.errors import ReadOnlyError

STATEMENT = 'SELECT * FROM "audience-resolution"'


@pytest.fixture
def ctx():
    return Context(profile="demo", region="eu-central-1")


@pytest.fixture
def stub(ctx, monkeypatch):
    """A stubbed `dynamodb` client wired into the context."""
    import boto3

    client = boto3.Session(region_name="eu-central-1").client(
        "dynamodb", aws_access_key_id="a", aws_secret_access_key="b"
    )
    stubber = Stubber(client)
    monkeypatch.setattr(Context, "client", lambda _self, _service, region=None: client)
    stubber.activate()
    yield stubber
    stubber.deactivate()


# --- the row shape --------------------------------------------------------


def test_a_descriptor_is_unwrapped_and_a_number_stays_a_string():
    """DynamoDB numbers are arbitrary precision - float() would corrupt an id."""
    row = ddbql.flatten({"pk": {"S": "a"}, "n": {"N": "123456789012345678901234"}})
    assert row == {"pk": "a", "n": "123456789012345678901234"}


def test_descriptors_are_stripped_all_the_way_down():
    """Found by running it: a live `SELECT` printed `[{"S":"acme:aud:orders"}]`.

    The first version unwrapped one level only, which is correct for a scalar and
    leaks the wire format for a list or a map. Nothing but a real query showed it.
    """
    assert ddbql.flatten({"m": {"M": {"inner": {"S": "x"}}}})["m"] == {"inner": "x"}
    assert ddbql.flatten({"l": {"L": [{"S": "a"}, {"S": "b"}]}})["l"] == ["a", "b"]
    assert ddbql.flatten({"l": {"L": [{"M": {"k": {"N": "1"}}}]}})["l"] == [{"k": "1"}]


def test_an_attribute_named_like_a_descriptor_keeps_its_name():
    """`{"M": {"S": {"S": "x"}}}` is a map holding an attribute called `S`.

    The recursion walks a map's values, never its keys, or this would collapse.
    """
    assert ddbql.flatten({"m": {"M": {"S": {"S": "x"}}}})["m"] == {"S": "x"}


def test_a_big_number_survives_the_whole_way_to_json():
    """Why `core/ddbvalue.py` exists, pinned at the step that actually loses data.

    This test corrected its own first version, which is worth keeping in mind:
    `TypeDeserializer` returns `Decimal`, and **`Decimal` is exact** - it is not the
    lossy step. The loss happens because `Decimal` is not JSON-serialisable and
    `jsonable` converts it to `float`. Keeping the string DynamoDB sent avoids the
    entire chain.
    """
    import json
    from decimal import Decimal

    from boto3.dynamodb.types import TypeDeserializer

    from clitka.core.output import jsonable

    digits = "123456789012345678901234"
    exact = TypeDeserializer().deserialize({"N": digits})
    assert str(exact) == digits, "Decimal is exact - it is not what loses the digits"
    # The float conversion any JSON step needs is the lossy one.
    assert str(jsonable(Decimal(digits))) != digits
    # CLITKA's own route keeps every digit, all the way into JSON.
    assert ddbql.flatten({"n": {"N": digits}})["n"] == digits
    assert json.dumps(ddbql.flatten({"n": {"N": digits}})) == f'{{"n": "{digits}"}}'


def test_a_binary_attribute_survives_into_json():
    """Measured (PoC Q4/Q9): botocore decodes the wire base64, so a B value is bytes
    and plain `json.dumps` raises `TypeError` on it."""
    raw = {"blob": {"B": b"\x89PNG"}}
    with pytest.raises(TypeError):
        json.dumps(raw)
    assert json.dumps(ddbql.flatten(raw))


def test_a_null_attribute_reads_as_null_not_as_the_flag():
    """`{"NULL": true}` means "this attribute is null".

    Reporting the flag would print `True` for something whose value is nothing - the
    opposite of the truth. The attribute itself must still be present, though: a
    dropped key and a null value are different answers.
    """
    row = ddbql.flatten({"nothing": {"NULL": True}})
    assert row == {"nothing": None}
    assert "nothing" in row


def test_columns_are_the_union_because_dynamodb_is_schemaless():
    """Two rows in one answer need not have the same attributes."""
    rows = [{"pk": "a", "only_here": 1}, {"pk": "b", "and_here": 2}]
    assert ddbql.columns_of(rows) == ["pk", "only_here", "and_here"]
    # A column nothing has must not be invented just because it was asked for.
    assert ddbql.columns_of([{"a": 1}], first=("pk",)) == ["a"]


# --- is_write -------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    ["INSERT INTO t VALUE {'pk':'a'}", "update t SET x=1", "  DELETE FROM t WHERE pk='a'"],
)
def test_a_write_verb_is_recognised_whatever_the_case(statement):
    assert ddbql.is_write(statement)


@pytest.mark.parametrize("statement", ['SELECT * FROM "t"', "", "   ", "updated_at FROM t"])
def test_everything_else_is_a_read(statement):
    """`updated_at` starts with a write verb and is not one - hence a word split."""
    assert not ddbql.is_write(statement)


# --- the paging loop ------------------------------------------------------


def test_an_empty_page_does_not_end_the_walk(ctx, stub):
    """The PoC's finding, as the test that would have caught the bug.

    A filtered statement answers `Count 0` WITH a `NextToken`. Stopping there would
    report "no rows" for a table whose matches are all on a later page.
    """
    stub.add_response(
        "execute_statement",
        {"Items": [], "NextToken": "page-2"},
        {"Statement": STATEMENT, "Limit": ddbql.MAX_ROWS},
    )
    stub.add_response(
        "execute_statement",
        {"Items": [{"pk": {"S": "found-later"}}]},
        {"Statement": STATEMENT, "NextToken": "page-2", "Limit": ddbql.MAX_ROWS},
    )

    page = ddbql.run(ctx, STATEMENT)
    assert page.count == 1, "an empty first page must not end the walk"
    assert page.rows[0] == {"pk": "found-later"}
    assert page.requests == 2
    assert page.next_token is None and not page.capped
    stub.assert_no_pending_responses()


def test_a_single_complete_page_asks_once(ctx, stub):
    stub.add_response(
        "execute_statement",
        {"Items": [{"pk": {"S": "a"}}, {"pk": {"S": "b"}}]},
        {"Statement": STATEMENT, "Limit": ddbql.MAX_ROWS},
    )
    page = ddbql.run(ctx, STATEMENT)
    assert page.count == 2 and page.requests == 1
    assert not page.capped
    assert page.summary() == "2 row(s)"
    stub.assert_no_pending_responses()


def test_the_row_cap_trims_and_says_so(ctx, stub):
    """A capped answer must never look like the whole answer - the `s3 ls` rule."""
    stub.add_response(
        "execute_statement",
        {"Items": [{"pk": {"S": str(i)}} for i in range(5)], "NextToken": "more"},
        {"Statement": STATEMENT, "Limit": 3},
    )
    page = ddbql.run(ctx, STATEMENT, max_rows=3)
    assert page.count == 3 and page.capped
    assert page.next_token == "more", "a capped answer must stay resumable"
    assert "capped" in page.summary()
    stub.assert_no_pending_responses()


def test_the_request_cap_stops_a_statement_that_pages_for_ever(ctx, stub):
    """A highly selective statement can answer empty page after empty page."""
    for index in range(ddbql.MAX_PAGES):
        expected = {"Statement": STATEMENT, "Limit": ddbql.MAX_ROWS}
        if index:
            expected["NextToken"] = "more"
        stub.add_response("execute_statement", {"Items": [], "NextToken": "more"}, expected)

    page = ddbql.run(ctx, STATEMENT)
    assert page.requests == ddbql.MAX_PAGES
    assert page.capped and page.count == 0
    assert page.next_token == "more"
    stub.assert_no_pending_responses()


def test_a_resume_token_is_sent_when_given(ctx, stub):
    stub.add_response(
        "execute_statement",
        {"Items": [{"pk": {"S": "z"}}]},
        {"Statement": STATEMENT, "NextToken": "carry-on", "Limit": ddbql.MAX_ROWS},
    )
    page = ddbql.run(ctx, STATEMENT, start_token="carry-on")
    assert page.count == 1
    stub.assert_no_pending_responses()


# --- the write guard ------------------------------------------------------


def test_a_write_statement_is_refused_in_read_only_mode(stub):
    """PartiQL is the one place a *typed string* can mutate data."""
    ctx = Context(profile="demo", region="eu-central-1", read_only=True)
    with pytest.raises(ReadOnlyError) as caught:
        ddbql.run(ctx, "DELETE FROM \"t\" WHERE pk='a'")
    # The verb has to be in the message, or the refusal says nothing useful.
    assert "delete" in str(caught.value)
    # Nothing was sent: a refusal must happen before the call, not after.
    stub.assert_no_pending_responses()


def test_a_select_is_allowed_in_read_only_mode(stub):
    ctx = Context(profile="demo", region="eu-central-1", read_only=True)
    stub.add_response(
        "execute_statement", {"Items": []}, {"Statement": STATEMENT, "Limit": ddbql.MAX_ROWS}
    )
    assert ddbql.run(ctx, STATEMENT).count == 0
    stub.assert_no_pending_responses()


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_an_empty_statement_is_refused(ctx, blank):
    with pytest.raises(ValueError):
        ddbql.run(ctx, blank)


def test_an_aws_error_is_translated_not_leaked(ctx, stub):
    """Nothing botocore raises may reach the CLI or the TUI (`wrap_aws_errors`)."""
    from clitka.core.errors import AwsError

    stub.add_client_error(
        "execute_statement",
        service_error_code="ValidationException",
        service_message="Statement wasn't well formed...",
    )
    with pytest.raises(AwsError) as caught:
        ddbql.run(ctx, "THIS IS NOT SQL")
    assert "ValidationException" in str(caught.value)
