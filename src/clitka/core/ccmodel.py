"""What Cloud Control hands back, as a row. Boto3-free.

Split from `core/cloudcontrol.py` for the 8 kB rule, and it landed on the seam
worth having: this half is the *shape* of a resource and the column arithmetic,
with no API call in it, so both are testable without a stub. `cloudcontrol`
re-exports `Resource` and `columns_for`, so every existing caller
(`tui/treemodel.py`, `core/lister.py`, the `resources` plugin) is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from clitka.core.resname import name_of

__all__ = ["Resource", "columns_for"]


@dataclass(frozen=True)
class Resource:
    """One row of the explorer."""

    type_name: str
    identifier: str
    properties: dict[str, Any]

    def name(self) -> str:
        """A human-readable name, or "" when the identifier is all there is.

        `i-0abc...` is not what anyone calls the machine - the `Name` tag is
        (owner's request). The guessing lives in `core/resname.py`.
        """
        return name_of(self.identifier, self.properties)

    def row(self) -> dict[str, Any]:
        """Flat row: identifier, the name if there is one, then the properties.

        `name` is *derived*, not a property - `previewmodel.resource_from` drops
        it again so it never shows up in the Raw tab.
        """
        row: dict[str, Any] = {"identifier": self.identifier}
        name = self.name()
        if name:
            row["name"] = name
        for key, value in self.properties.items():
            if key not in row:
                row[key] = value
        return row


def columns_for(resources: list[Resource], limit: int = 6) -> list[str]:
    """Pick table columns: identifier, `name` if any row has one, then the rest.

    Cloud Control returns a different property subset per type (and sometimes per
    resource), so the columns are derived from the data rather than declared.
    `name` is put second on purpose: on EC2 the identifier alone is unreadable.
    """
    counts: dict[str, int] = {}
    for resource in resources:
        for key in resource.properties:
            counts[key] = counts.get(key, 0) + 1
    head = ["identifier"]
    if any(resource.name() for resource in resources):
        head.append("name")
    ranked = sorted(counts, key=lambda key: (-counts[key], key))
    chosen = [key for key in ranked if key not in head][: max(limit - len(head), 0)]
    return [*head, *chosen]


def _self_check() -> None:
    one = Resource("AWS::S3::Bucket", "b1", {"BucketName": "b1", "Arn": "arn:x"})
    assert next(iter(one.row())) == "identifier", "the table keys on it"
    assert one.row()["BucketName"] == "b1"

    # The columns are derived from the data: the properties most rows share win,
    # and a rare one is dropped first.
    rows = [
        Resource("T", "a", {"Name": "a", "State": "on"}),
        Resource("T", "b", {"Name": "b", "State": "off"}),
        Resource("T", "c", {"Name": "c", "Rare": 1}),
    ]
    assert columns_for(rows, limit=3) == ["identifier", "Name", "State"]
    assert columns_for(rows, limit=4)[-1] == "Rare", "the rarest is last, not absent"
    assert columns_for([]) == ["identifier"]

    # **A bare `Name` property is not a name**: `resname` reads the `Name` *tag*,
    # which is what the console shows, so these rows get no `name` column. Worth
    # pinning - guessing the other way round is how the column would start
    # duplicating a property that is already displayed.
    assert "name" not in columns_for(rows)
    assert rows[0].name() == ""
    tagged = Resource("T", "i-1", {"Tags": [{"Key": "Name", "Value": "web-01"}]})
    assert tagged.name() == "web-01"
    assert columns_for([tagged])[1] == "name", "a real name comes second"
    assert tagged.row()["name"] == "web-01"

    # A resource with nothing to call itself gets no `name` column at all.
    assert columns_for([Resource("T", "x", {})]) == ["identifier"]

    print("[OK] cloud control model self-check passed")


if __name__ == "__main__":
    _self_check()
