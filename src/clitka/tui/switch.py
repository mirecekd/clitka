"""What the F2 (profile), F3 (region) and `:` (type) choosers are filled with.


The list building is plain functions over already-loaded data so it can be tested
without a screen and without AWS.

ponytail: both sources are read synchronously when the panel opens - parsing
`~/.aws/config` and asking botocore for its region list are local, offline
operations. Ceiling: the very first call also builds a boto3 session, which costs
a few hundred milliseconds. Upgrade path: fill the panel from a thread worker.
"""

from __future__ import annotations

from collections.abc import Sequence

from clitka.core import cloudcontrol
from clitka.core.awsconfig import AwsConfig
from clitka.core.context import Context
from clitka.tui.dropdown import MenuItem

PROFILE_TITLE = "F2  Switch profile - this session only"
REGION_TITLE = "F3  Switch region - this session only"

# `clitka ctx use` is the way to make a choice stick, so say so.
PROFILE_HINT = "session only - use `clitka ctx use <profile>` to make it the default"
REGION_HINT = "session only - use `clitka ctx use <profile> --region <r>` for the default"


def profile_items(config: AwsConfig, current: str | None) -> list[MenuItem]:
    """One row per profile, with its kind and region as the dimmed detail."""
    items: list[MenuItem] = []
    for row in config.summary():
        name = str(row["profile"])
        detail = " ".join(part for part in (row["kind"], row["region"], row["sso_session"]) if part)
        items.append(MenuItem(label=name, value=name, detail=detail, current=name == current))
    return items


def region_items(names: Sequence[str], current: str | None) -> list[MenuItem]:
    """One row per region. The active one is marked and the cursor starts there."""
    return [MenuItem(label=name, value=name, current=name == current) for name in sorted(names)]


# --- resource types for the `:` palette -----------------------------------

# ListTypes returns ~1200 names and takes several seconds over many pages, so the
# answer is kept for the life of the process, keyed by profile+region.
# ponytail: an unbounded process-lifetime dict. Ceiling: a type registered while
# CLITKA is running is not seen until restart, and the cache is not shared between
# runs. Upgrade path: a TTL, or a file under ~/.cache/clitka.
_TYPE_CACHE: dict[tuple[str | None, str | None], tuple[str, ...]] = {}


def type_names(ctx: Context, fallback: Sequence[str]) -> tuple[str, ...]:
    """Every listable resource type name, or `fallback` if AWS will not say.

    `cloudformation:ListTypes` is denied to plenty of real-world roles, and a
    palette that refuses to open would be worse than a short hard-coded list.
    """
    try:
        # `effective_region` builds a boto3 session, which raises for a profile
        # that is not in ~/.aws/config - so even the cache key is not safe.
        key = (ctx.profile, ctx.effective_region)
    except Exception:
        return tuple(fallback)
    cached = _TYPE_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        rows = cloudcontrol.list_types(ctx)
        names = tuple(str(row["type_name"]) for row in rows if row.get("type_name"))
    except Exception:
        # Denied, throttled, offline - the palette still has to open.
        names = ()

    resolved = names or tuple(fallback)
    if names:
        # Only a real answer is worth caching; a failure should be retried.
        _TYPE_CACHE[key] = resolved
    return resolved


def forget_types() -> None:
    """Drop the type cache - used by the tests, and after a F5 refresh."""
    _TYPE_CACHE.clear()


def _self_check() -> None:
    from clitka.core.awsconfig import Profile

    config = AwsConfig(
        profiles={
            "sw-sandbox": Profile(
                "sw-sandbox",
                settings={"sso_session": "sw", "region": "eu-central-1"},
            ),
            "plain": Profile("plain", settings={"aws_access_key_id": "AK"}),
        },
        sso_sessions={},
    )
    # sso_for() raises for the unknown session, and summary() must survive that.
    items = profile_items(config, current="plain")
    assert [item.value for item in items] == ["plain", "sw-sandbox"]
    assert items[0].current is True
    assert items[1].current is False
    assert "eu-central-1" in items[1].detail
    assert "static" in items[0].detail

    regions = region_items(["us-east-1", "eu-central-1"], current="eu-central-1")
    assert [r.value for r in regions] == ["eu-central-1", "us-east-1"]
    assert regions[0].current is True
    assert region_items([], None) == []

    # type_names must fall back rather than fail when ListTypes is unavailable.
    forget_types()
    broken = Context(profile="does-not-exist-anywhere")
    assert type_names(broken, ("AWS::S3::Bucket",)) == ("AWS::S3::Bucket",)
    assert not _TYPE_CACHE, "a failed lookup must not be cached"
    print("[OK] switch self-check passed")


if __name__ == "__main__":
    _self_check()
