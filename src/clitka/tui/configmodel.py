"""What the `C` panel offers, and what each row means. No Textual in here.

The rows and the toggling are plain functions so they can be tested without a
screen - the same seam as `tablemodel.py` and `previewgroups.py`. The panel
itself is `tui/configpanel.py`.

`C` is the **only** screen in CLITKA that writes `~/.config/clitka/config.toml`.
`P`, `R` and `W` remain session-only, which is the owner's standing rule; this
panel is where a session choice is deliberately promoted to a default.
"""

from __future__ import annotations

from collections.abc import Sequence

from clitka.core import timerange as tr
from clitka.core.clitkaconfig import ClitkaConfig
from clitka.tui.dropdown import MenuItem
from clitka.tui.restypes import TREE_TYPES, valid_types

TITLE = "C  Configuration - saved to disk"
HINT = "the only screen that writes config.toml; P/R/W stay session-only"
BRANCH_TITLE = "C  Explorer branches - space toggles, escape closes"
BRANCH_HINT = "these are the types the tree opens with; `/` filters, `:` still reaches any other"


# Row identifiers. They are the panel's dismiss values, so they are strings.
BRANCHES = "branches"
SAVE_PROFILE = "profile"
SAVE_REGION = "region"
SAVE_WINDOW = "window"
READ_ONLY = "read_only"
REMEMBER = "remember_last"
RESET = "reset"

UNSET = "(unset)"


def settings_items(
    cfg: ClitkaConfig,
    profile: str | None = None,
    region: str | None = None,
    window: tr.TimeRange | None = None,
) -> list[MenuItem]:
    """The `C` panel's rows: what is saved now, and what pressing the key does.

    `profile` / `region` / `window` are the *session* values, so each row can say
    what it would promote to a default - "save eu-west-1" is a far better prompt
    than "region...".
    """
    now = window or tr.current()
    branches = valid_types(cfg.tree_types)
    return [
        MenuItem(
            label="Explorer branches...",
            value=BRANCHES,
            key="b",
            detail=f"{len(branches) or len(TREE_TYPES)} types"
            + ("" if branches else " (defaults)"),
        ),
        MenuItem(
            label=f"Default profile: save {profile or UNSET}",
            value=SAVE_PROFILE,
            key="p",
            detail=f"now: {cfg.profile or UNSET}",
            current=bool(profile) and profile == cfg.profile,
        ),
        MenuItem(
            label=f"Default region: save {region or UNSET}",
            value=SAVE_REGION,
            key="r",
            detail=f"now: {cfg.region or UNSET}",
            current=bool(region) and region == cfg.region,
        ),
        MenuItem(
            label=f"Default time window: save {now.label}",
            value=SAVE_WINDOW,
            key="w",
            detail=f"now: {cfg.default_window or tr.DEFAULT.label + ' (built in)'}",
            current=now.label == cfg.default_window,
        ),
        MenuItem(
            label="Read-only by default",
            value=READ_ONLY,
            key="o",
            detail="on" if cfg.read_only else "off",
            current=cfg.read_only,
        ),
        MenuItem(
            label="Start where the last session stopped",
            value=REMEMBER,
            key="l",
            detail="on" if cfg.remember_last else "off",
            current=cfg.remember_last,
        ),
        MenuItem(
            label="Reset branches to the built-in list",
            value=RESET,
            key="d",
            detail=f"{len(TREE_TYPES)} types",
        ),
    ]


# How many rows the branch picker will build at once.
#
# **This is not a nicety, it is the fix for a 92-second freeze** (owner's report,
# 2026-08-02). `ListTypes` returns 1831 types on a real account, and mounting that
# many Textual `ListItem` widgets took 92 s *measured* - and every `space` toggle
# rebuilt them all. The picker therefore shows the chosen types plus a window of
# candidates, and says so; `/` is how the rest are reached.
#
# Measured on the 1831-type list: 1831 rows = 92 s to open and unusable per
# toggle, 200 rows = 3.0 s and 1.4 s, 60 rows = fast enough to feel instant. The
# panel only shows about twenty lines at a time, so a bigger window buys nothing
# a scroll could not.
#
# ponytail: a cap plus a filter, rather than a virtualised list. Ceiling: the
# unfiltered view is a window, not the whole catalogue. Upgrade path: Textual's
# `DataTable` renders lazily and would take all 1831 - but the filter is the
# better answer to "1831 rows" anyway.
BRANCH_ROWS = 60


def branch_items(
    candidates: Sequence[str], chosen: Sequence[str], limit: int = BRANCH_ROWS
) -> list[MenuItem]:
    """One row per type the user could put in the tree, the chosen ones marked.

    The chosen types come **first and in their own order**, because that order is
    the order of the branches on screen and it has to be visible to be editable.
    They are **never** cut by the limit: a type in the tree must always be
    un-tickable, however long the catalogue is.
    """
    picked = valid_types(chosen)
    rest = [name for name in valid_types(candidates) if name not in set(picked)]
    room = max(limit - len(picked), 0)
    return [
        MenuItem(label=name, value=name, detail="in the tree" if marked else "", current=marked)
        for name, marked in [(n, True) for n in picked] + [(n, False) for n in rest[:room]]
    ]


def toggle_branch(chosen: Sequence[str], name: str) -> list[str]:
    """Add a type to the tree, or take it out again. Order is preserved.

    A newly added type goes to the **end**, where a new branch appears - moving
    the existing ones about would be a surprise nobody asked for.
    """
    current = valid_types(chosen)
    if not valid_types([name]):
        return current
    clean = name.strip()
    return [n for n in current if n != clean] if clean in current else [*current, clean]


def summary(cfg: ClitkaConfig, path: str) -> str:
    """What the panel says after it wrote something."""
    branches = valid_types(cfg.tree_types)
    lines = [
        f"Saved to {path}",
        "",
        f"  branches       {len(branches)} types" if branches else "  branches       (defaults)",
        f"  profile        {cfg.profile or UNSET}",
        f"  region         {cfg.region or UNSET}",
        f"  time window    {cfg.default_window or tr.DEFAULT.label + ' (built in)'}",
        f"  read-only      {'on' if cfg.read_only else 'off'}",
        f"  remember last  {'on' if cfg.remember_last else 'off'}",
    ]
    return "\n".join(lines) + "\n"


def _self_check() -> None:
    tr.reset()
    empty = ClitkaConfig()
    rows = settings_items(empty, profile="sw-sandbox", region="eu-central-1")
    keys = [row.key for row in rows]
    assert len(set(keys)) == len(keys), keys
    # `C` itself must not be a row's shortcut, or it would close the panel.
    assert "c" not in keys, keys
    values = [row.value for row in rows]
    assert values[0] == BRANCHES and RESET in values

    # A row names the session value it would save - "save region..." is useless.
    assert "sw-sandbox" in rows[1].label, rows[1].label
    assert "eu-central-1" in rows[2].label, rows[2].label
    assert "1h" in rows[3].label, rows[3].label
    # Nothing is marked current on a fresh config: no default has been chosen.
    assert not any(row.current for row in rows), [r.label for r in rows if r.current]
    # With no profile in the session the row still reads, rather than crashing.
    assert UNSET in settings_items(empty)[1].label

    saved = ClitkaConfig(profile="sw-sandbox", read_only=True, remember_last=True)
    marked = settings_items(saved, profile="sw-sandbox")
    assert marked[1].current is True, "an already-saved default is marked"
    assert [r.value for r in marked if r.current] == [SAVE_PROFILE, READ_ONLY, REMEMBER]

    # Branch rows: the chosen ones first, in their own order, and marked.
    items = branch_items(["AWS::S3::Bucket", "AWS::SQS::Queue"], ["AWS::SQS::Queue"])
    assert items[0].value == "AWS::SQS::Queue" and items[0].current is True
    assert items[1].value == "AWS::S3::Bucket" and items[1].current is False
    # A chosen type absent from the candidate list is still shown - it is in use.
    kept = branch_items(["AWS::S3::Bucket"], ["AWS::Kinesis::Stream"])
    assert kept[0].value == "AWS::Kinesis::Stream"
    assert len(kept) == 2, kept

    # THE FREEZE FIX: 1831 real types must never become 1831 mounted widgets.
    many = [f"AWS::Svc{n // 60}::Thing{n % 60}" for n in range(1831)]
    assert len(branch_items(many, [])) == BRANCH_ROWS
    # ...and a chosen type is never cut, however long the catalogue is.
    mine = ["AWS::Kinesis::Stream", "AWS::SQS::Queue"]
    windowed = branch_items(many, mine, limit=5)
    assert [row.value for row in windowed][:2] == mine, windowed
    assert len(windowed) == 5, windowed
    # A limit smaller than the chosen list still shows every chosen type.
    assert len(branch_items(many, mine, limit=1)) == 2

    # Toggling: appended at the end, removed from anywhere, order kept.
    start = ["AWS::S3::Bucket", "AWS::SQS::Queue"]
    assert toggle_branch(start, "AWS::SNS::Topic") == [*start, "AWS::SNS::Topic"]
    assert toggle_branch(start, "AWS::S3::Bucket") == ["AWS::SQS::Queue"]
    assert toggle_branch(start, "nonsense") == start, "nonsense changes nothing"
    assert toggle_branch([], "AWS::S3::Bucket") == ["AWS::S3::Bucket"]

    text = summary(saved, "/tmp/config.toml")
    assert text.endswith("\n") and "/tmp/config.toml" in text
    assert "read-only      on" in text, text
    assert "(defaults)" in text, text
    print("[OK] config model self-check passed")


if __name__ == "__main__":
    _self_check()
