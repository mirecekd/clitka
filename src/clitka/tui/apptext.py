"""The welcome and F1 help text of the shell.

Prose only, no logic - split out of `tui/app.py` to keep that file under 8 kB.
"""

from __future__ import annotations

WELCOME = """\
CLITKA - CLI ToolKit for AWS

  :    open a resource type (Cloud Control explorer)
  F1   help
  P    switch profile
  R    switch region
  W    time window - how far back the log preview looks

  F3   view the selected resource in full
  F4   edit the selected resource
  F5   refresh identity
  F10  quit


Every screen has a scriptable CLI equivalent - try `clitka resources --help`.
"""

HELP = """\
  :    command palette - pick a resource type to explore
  F1   this help (F1 or escape closes it)
  P    switch profile - for this session only
  R    switch region  - for this session only
  W    time window - how far back the log preview and F9 look

  F3   view  - the selected resource in full (GetResource), as YAML
  F4   edit  - the selected resource
  F5   refresh

  F9   actions for the selected resource (inside the explorer)
  F10  quit
  q    quit

Profile, region and the time window are letters (upper or lower case) so the
function keys are free for what you do to a resource: F3 view, F4 edit,
F9 actions.

Signing in is a shell job, not a screen: run `clitka auth login` (or
`aws sso login`) in another terminal, then press F5 here to pick the new token up.


The time window (W)

  1..0        5m, 15m, 1h, 3h, 6h, 12h, 24h, 3d, 7d, 2w
  n / y       1 month / 1 year
  c           custom - type a duration: 90m, 2h, 3d, 2w, 1mo, 1y
              (a bare number means minutes)

It starts at 1h and lasts for this session only. `clitka logs search --since 3h`
is the same thing from a shell.

CLITKA opens on a tree of resource types. Nothing is fetched until you open a
branch; the resources then appear underneath as they load, and closing the branch
keeps them. `:` adds any other type as a further branch.

  up/down          move one node      page up/down   move a screenful
  enter / space    open or close the type under the cursor
  right / left     open / close without moving
  ctrl+home/end    first / last node
  tab              move between the tree and the preview - the focused side is
                   outlined; in the preview, left/right walk the tabs

Resources are loaded page by page and appear as they arrive, so a long listing
is browsable straight away. A resource is listed by its name where it has one
(the `Name` tag on EC2), with the identifier beside it.


Destructive actions always ask first, and "no" is the default answer. The status
bar at the bottom always shows the CLITKA build plus which profile, account and
region every call would use, and says READ-ONLY when writes are refused.
"""


def _self_check() -> None:
    for text in (WELCOME, HELP):
        assert text.endswith("\n")
        assert "F10" in text
        # The context switches are letters now, not F2/F3/F4.
        assert "F2" not in text, text
    assert "page up/down" in HELP
    assert "READ-ONLY" in HELP
    for key in ("P    switch profile", "R    switch region", "F3   view"):
        assert key in HELP, key
    # Every letter key must be documented in both texts - that is the whole
    # point of them, and `W` was added late.
    for text in (WELCOME, HELP):
        assert "W    time window" in text, text
        # Login was taken out of the TUI: no L key, and the shell command instead.
        assert "L  " not in text, text
    assert "clitka auth login" in HELP

    assert "1mo" in HELP, "the custom-duration syntax must be spelled out"
    print("[OK] app text self-check passed")


if __name__ == "__main__":
    _self_check()
