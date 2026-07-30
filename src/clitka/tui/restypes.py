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

# What the app opens on. CLITKA lands on real data rather than a splash screen,
# and S3 buckets are the one type nearly every account has and that lists without
# a parent identifier.
# ponytail: a constant, not a setting. Ceiling: an account with no S3 access sees
# an error on the first screen (and `:` still works). Upgrade path: remember the
# last type used in ~/.config/clitka/config.toml.
START_TYPE = "AWS::S3::Bucket"

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
  F2   switch profile - reloads this list against the new one
  F3   switch region  - reloads this list against the new one
  F5   reload the list
  F9   actions for the highlighted resource
  F10  quit

  escape   back

Resources arrive page by page and the list stays usable while they load - the
heading says "loading..." until the last page is in. Destructive actions always
ask first, and "no" is the default answer. Columns are derived from the properties
Cloud Control actually returned for this type, so they differ from type to type.
"""


def _self_check() -> None:
    assert "AWS::S3::Bucket" in COMMON_TYPES
    assert all(name.startswith("AWS::") for name in COMMON_TYPES)
    assert len(set(COMMON_TYPES)) == len(COMMON_TYPES), "duplicate type"
    assert 0 < PAGE_ROWS < MAX_ROWS
    assert START_TYPE in COMMON_TYPES, "the start type must survive a ListTypes denial"
    assert "F9" in EXPLORER_HELP
    assert "page up/down" in EXPLORER_HELP, "the paging keys must be documented"
    print("[OK] resource types self-check passed")


if __name__ == "__main__":
    _self_check()
