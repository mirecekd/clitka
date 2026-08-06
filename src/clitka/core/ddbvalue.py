# src/clitka/core/ddbvalue.py
"""DynamoDB's wire format, in and out: the type descriptors.

Split out of `core/ddbqlmodel.py` for the 8 kB rule, and it is the right seam - this
is the value codec, with no notion of statements, pages or rows. It is also the part
the item editor will need next (M5's remaining half), where the *encode* direction
matters as much as this one.

The whole module exists because of one measured chain, and the first version of this
comment got it wrong in a way its own test caught - worth recording precisely:

- `TypeDeserializer` returns `Decimal` for an `N`, and **`Decimal` is exact** - it is
  not the lossy step.
- But `Decimal` is not JSON-serialisable, so something has to convert it, and
  `core/output.jsonable` converts it to **`float`**. That is where a 24-digit id
  becomes `1.2345678901234569e+23`.
- `TypeDeserializer` also returns `Binary` for a `B`, and `set` for `NS`/`SS`, neither
  of which `json.dumps` accepts either.

So the deserialiser is not "broken"; it is simply aimed at Python arithmetic, while
CLITKA needs to *display and re-serialise* values without altering them. Keeping every
number as the **string DynamoDB sent** sidesteps the whole chain.
"""

from __future__ import annotations

from typing import Any

from clitka.core.output import jsonable

__all__ = ["DESCRIPTORS", "unwrap"]

# The DynamoDB type descriptors, as the set that makes a single-key dict a *value*
# rather than a map that happens to have one entry.
DESCRIPTORS = frozenset({"S", "N", "B", "SS", "NS", "BS", "M", "L", "NULL", "BOOL"})


def unwrap(node: Any) -> Any:
    """Strip DynamoDB type descriptors at every depth.

    `{"L": [{"S": "a"}]}` becomes `["a"]`, and a nested `M` becomes a plain dict, so a
    list of strings prints as `["a"]` rather than `[{"S":"a"}]`. That difference is not
    cosmetic guesswork - it is what a live run actually showed on the owner's sandbox
    before the recursion was added.

    Numbers stay **strings**: DynamoDB numbers are arbitrary precision, and the route
    through `Decimal` to JSON goes via `float`, which turns a 24-digit id into
    `1.2345678901234569e+23` (measured - see the module docstring). A `NULL`
    descriptor becomes `None`, not `True`, because `{"NULL": true}` means "this
    attribute is null" and reporting the flag would say the opposite for a falsy
    value.

    Only the *values* of a map are walked, never its keys, so an attribute literally
    named `S` keeps its name.
    """
    if isinstance(node, dict) and len(node) == 1:
        kind, value = next(iter(node.items()))
        if kind in DESCRIPTORS:
            if kind == "NULL":
                return None
            if kind == "M":
                return {name: unwrap(inner) for name, inner in value.items()}
            if kind == "L":
                return [unwrap(inner) for inner in value]
            # S, N, B, SS, NS, BS, BOOL are all leaves. `jsonable` is what makes a `B`
            # survive - it decodes bytes, which plain json.dumps cannot.
            return jsonable(value)
    if isinstance(node, dict):
        return {name: unwrap(inner) for name, inner in node.items()}
    if isinstance(node, list):
        return [unwrap(inner) for inner in node]
    return jsonable(node)


def _self_check() -> None:
    import json

    # --- leaves ---------------------------------------------------------------
    assert unwrap({"S": "a"}) == "a"
    assert unwrap({"BOOL": True}) is True
    # A number stays a string. The loss is not in `Decimal` (which is exact) but in
    # the float conversion any JSON step needs - see the module docstring.
    digits = "123456789012345678901234"
    assert unwrap({"N": digits}) == digits
    assert json.dumps(unwrap({"N": digits})) == f'"{digits}"'
    assert jsonable(__import__("decimal").Decimal(digits)) != digits, (
        "the float conversion is the lossy step this module exists to avoid"
    )
    # A NULL means the attribute IS null - reporting the flag would invert it.
    assert unwrap({"NULL": True}) is None
    # A set of numbers stays a list of strings, same precision argument.
    assert unwrap({"NS": ["1", "2"]}) == ["1", "2"]

    # --- depth: the live-run finding ------------------------------------------
    assert unwrap({"M": {"inner": {"S": "x"}}}) == {"inner": "x"}
    assert unwrap({"L": [{"S": "a"}, {"S": "b"}]}) == ["a", "b"]
    # A list of maps, which is the shape the owner's real data actually took.
    assert unwrap({"L": [{"M": {"k": {"N": "1"}}}]}) == [{"k": "1"}]
    # Bytes survive at depth, which plain json.dumps cannot (PoC Q4/Q9).
    assert json.dumps(unwrap({"L": [{"M": {"b": {"B": b"\xff"}}}]}))

    # --- what must NOT be unwrapped -------------------------------------------
    assert unwrap({"only": "value"}) == {"only": "value"}
    # An attribute literally NAMED "S" inside a map is well-formed DynamoDB and must
    # keep its name - the recursion walks a map's values, never its keys. (The first
    # assert written here described malformed input and failed; measured, then fixed.)
    assert unwrap({"M": {"S": {"S": "x"}}}) == {"S": "x"}
    # An empty list or map must not collapse into None.
    assert unwrap({"L": []}) == [] and unwrap({"M": {}}) == {}

    print("[OK] ddb value self-check passed")


if __name__ == "__main__":
    _self_check()
