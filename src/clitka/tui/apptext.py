"""The welcome and F1 help text of the shell.

Prose only, no logic - split out of `tui/app.py` to keep that file under 8 kB.
"""

from __future__ import annotations

WELCOME = """\
CLITKA - CLI ToolKit for AWS

  :    open a resource type (Cloud Control explorer)
  F1   help
  F2   switch profile
  F3   switch region
  F5   refresh identity
  F10  quit

Every screen has a scriptable CLI equivalent - try `clitka resources --help`.
"""

HELP = """\
  :    command palette - pick a resource type to explore
  F1   this help (F1 or escape closes it)
  F2   switch profile - for this session only
  F3   switch region  - for this session only
  F5   refresh
  F9   actions for the selected resource (inside the explorer)
  F10  quit
  q    quit

Inside the explorer:

  up/down       move one row          page up/down   move a screenful
  ctrl+home/end first / last row      /              filter every loaded row
  s             sort the current column
  F9            actions for the highlighted row
  escape        back

Resources are loaded page by page and appear as they arrive, so a long listing
is browsable straight away.

Destructive actions always ask first, and "no" is the default answer. The status
bar at the bottom always shows the CLITKA build plus which profile, account and
region every call would use, and says READ-ONLY when writes are refused.
"""


def _self_check() -> None:
    for text in (WELCOME, HELP):
        assert text.endswith("\n")
        assert "F10" in text
    assert "page up/down" in HELP
    assert "READ-ONLY" in HELP
    print("[OK] app text self-check passed")


if __name__ == "__main__":
    _self_check()
