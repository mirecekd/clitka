"""Resource-type constants and the explorer's help text.


Split out of `tui/explorer.py` purely to keep every file under 8 kB; there is no
logic here. `COMMON_TYPES` is imported by the app for the `:` palette fallback.
"""

from __future__ import annotations

# ponytail: a short starter list of types that list cleanly without a parent
# identifier. Ceiling: not exhaustive - it is only the fallback for when
# `cloudformation:ListTypes` is denied. Upgrade path: none needed, see
# `tui.switch.type_names`.
COMMON_TYPES: tuple[str, ...] = (
    "AWS::S3::Bucket",
    "AWS::Lambda::Function",
    "AWS::DynamoDB::Table",
    "AWS::EC2::Instance",
    "AWS::EC2::VPC",
    "AWS::ECS::Cluster",
    "AWS::ECR::Repository",
    "AWS::CloudFormation::Stack",
    "AWS::Logs::LogGroup",
    "AWS::StepFunctions::StateMachine",
    "AWS::ApiGateway::RestApi",
    "AWS::SNS::Topic",
    "AWS::SQS::Queue",
    "AWS::IAM::Role",
)

# The branches the landing tree opens with - the types the owner actually works
# with, in the order they are most often wanted. Nothing is fetched until a branch
# is expanded, so the list can afford to be a little generous; anything missing is
# one `:` away and is then added as a further branch.
# ponytail: a constant, not a setting. Ceiling: it is the same for every account.
# Upgrade path: remember the branches in ~/.config/clitka/config.toml.
TREE_TYPES: tuple[str, ...] = (
    "AWS::S3::Bucket",
    "AWS::Lambda::Function",
    "AWS::DynamoDB::Table",
    "AWS::Logs::LogGroup",
    "AWS::EC2::Instance",
    "AWS::ECS::Cluster",
    "AWS::ECR::Repository",
    "AWS::CloudFormation::Stack",
    "AWS::StepFunctions::StateMachine",
    "AWS::ApiGateway::RestApi",
)


# How many resources are handed to the table at a time, and where listing stops.

# ponytail: a fixed display cap rather than true on-demand paging. Ceiling: a type
# with more than MAX_ROWS resources is shown truncated (the heading says so).
# Upgrade path: keep the NextToken and fetch more when the cursor nears the end.
PAGE_ROWS = 100
MAX_ROWS = 2000

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

  F3   view the highlighted resource in full (GetResource), as YAML
  F4   edit the highlighted resource
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

  F3   view the highlighted resource in full (GetResource), as YAML
  F4   edit the highlighted resource

  F5   collapse everything and forget it (this is also the retry after an error)
  F9   actions for the highlighted resource (a type branch has none)
  F10  quit


A type shows how many resources it holds once loaded: "(98)", "(none)", or
"(2000+)" when the display limit cut it short. A branch that could not be listed
keeps the error on it - F5 retries.
"""


def _self_check() -> None:
    assert "AWS::S3::Bucket" in COMMON_TYPES
    assert all(name.startswith("AWS::") for name in COMMON_TYPES)
    assert len(set(COMMON_TYPES)) == len(COMMON_TYPES), "duplicate type"
    assert 0 < PAGE_ROWS < MAX_ROWS

    assert all(name.startswith("AWS::") for name in TREE_TYPES)
    assert len(set(TREE_TYPES)) == len(TREE_TYPES), "duplicate branch"
    # Every landing branch must also be a palette fallback, so a ListTypes denial
    # never leaves the user unable to reopen one of them.
    assert set(TREE_TYPES) <= set(COMMON_TYPES)

    assert "F9" in EXPLORER_HELP
    assert "page up/down" in EXPLORER_HELP, "the paging keys must be documented"
    for text in (EXPLORER_HELP, TREE_HELP):
        assert text.endswith("\n") and "F10" in text
        # The context switches are letters now - F2 must not be promised anywhere.
        assert "F2 " not in text, text
        assert "F3   view" in text or "F4   edit" in text, text
    assert "enter / space" in TREE_HELP
    assert "left / right" in TREE_HELP, "keyboard tab switching must be documented"
    for text in (EXPLORER_HELP, TREE_HELP):
        assert "W    time window" in text, text
    assert "1mo" in TREE_HELP, "the custom-duration syntax must be spelled out"

    print("[OK] resource types self-check passed")


if __name__ == "__main__":
    _self_check()
