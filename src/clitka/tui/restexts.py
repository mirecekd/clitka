"""The F1 help texts of the two resource screens. Prose only, no logic.

Split out of `tui/restypes.py` for the 8 kB rule when the `C` panel added a line
to both. `restypes.py` re-exports them, so no caller changed - and the seam is
the same one `apptext.py` already sits on: prose in one file, constants in another.

**Every keyboard shortcut must be documented here** (the owner's rule). The
assertions that enforce it live in `restypes._self_check`, beside the constants
they check.
"""

from __future__ import annotations

EXPLORER_HELP = """\
Moving around

  up/down          one row            page up/down   a screenful
  ctrl+home/end    first / last row

Doing things

  /    filter - matches every row loaded so far (escape clears it)
  s    sort by the current column (again reverses it)
  :    open a different resource type
  F1   this help (F1 or escape closes it)
  P    switch profile - reloads this list against the new one
  R    switch region  - reloads this list against the new one
  W    time window - how far back anything time-based looks
  C    config - saved to disk: branches, defaults, read-only

  F3   view the highlighted resource in full (GetResource), as YAML
  F4   edit the highlighted resource
  x    open a shell on it - an EC2 instance or an ECS task
  F5   reload the list
  F9   actions for the highlighted resource
  F10  quit

  escape   back

Resources arrive page by page and the list stays usable while they load - the
heading says "loading..." until the last page is in. Destructive actions always
ask first, and "no" is the default answer. Columns are derived from the properties
Cloud Control actually returned for this type, so they differ from type to type -
plus a `name` column wherever the type has a name (the `Name` tag on EC2).
"""


TREE_HELP = """\
The screen is split: the tree of resource types on the left, a preview of what you
picked on the right. Nothing is fetched until you open a branch, and what is
fetched appears while it loads.

Moving around

  up/down          one node           page up/down   a screenful
  ctrl+home/end    first / last node

Opening and closing

  enter / space    open a type (loads it) or close it again
  right / left     open / close without moving off the node
  :                add any other resource type as a new branch

Some resources hold more: an ECS cluster folds out into Services and Tasks, and a
service into its own Tasks. Those come from the service plugin, not from Cloud
Control - which is the only way an ECS task is reachable at all, since it has no
resource type. Open the cluster leaf and the sub-branches appear under it; what is
inside them is a normal resource, so F3, F9 and x all work on it.

Which types are branches here is up to you: C, then b. `:` still reaches any
other type for the session, without saving anything.

The preview

  enter            on a *resource*: show it in the pane on the right
  tab              move between the tree and the preview, and back - whichever
                   side has the keyboard is outlined
  left / right     inside the preview: walk the tabs (Overview, Raw, ...)
  up / down        inside the preview: scroll the tab
  page up/down     inside the preview: page the tab; home / end jump to the ends

  t                on a log group: follow it live (CloudWatch live tail)
  w                how far back the Events tab looks: 1..0 for the presets,
                   n / y for 1 month / 1 year, c to type one (90m, 2h, 1mo)


Only enter (or a mouse click) fills the preview - moving the cursor never calls
AWS, so you can scroll a long branch for free. The pane has an Overview of the
grouped properties and a Raw tab with the API response; a service can add tabs of
its own, such as the last log events of a log group.

Doing things

  F1   this help (F1 or escape closes it)
  P    switch profile - everything loaded is dropped, reopen to refetch
  R    switch region  - the same
  W    time window - the Events tab is refetched through the new one
  C    config - which types are branches here, and the startup defaults.
       This one writes to disk; P, R and W last for this session only.

  F3   view the highlighted resource in full (GetResource), as YAML
  F4   edit the highlighted resource
  x    open a shell on it - an EC2 instance (SSM) or an ECS task (exec).
       CLITKA steps aside for the session and comes back when you exit.
       To reach a task: open the ECS cluster leaf, then its Tasks sub-branch.

  F5   collapse everything and forget it (this is also the retry after an error)
  F9   actions for the highlighted resource (a type branch has none)
  F10  quit


A type shows how many resources it holds once loaded: "(98)", "(none)", or
"(2000+)" when the display limit cut it short. A branch that could not be listed
keeps the error on it - F5 retries.
"""
