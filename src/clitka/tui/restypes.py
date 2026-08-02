"""Which resource types the explorer knows about, and which it opens with.

Split out of `tui/explorer.py` to keep every file under 8 kB. `COMMON_TYPES` is
the `:` palette's fallback; `tree_types()` answers "which branches does the
explorer open with" - the built-in list, or whatever the `C` panel saved.

The two F1 help texts moved to `tui/restexts.py` when this file went over 8 kB and
are re-exported here, so no caller had to change. The assertions that keep them
honest stay in `_self_check` below, beside the keys they are about.
"""

from __future__ import annotations

from collections.abc import Sequence

from clitka.core import clitkaconfig
from clitka.tui.restexts import EXPLORER_HELP, TREE_HELP

__all__ = [
    "COMMON_TYPES",
    "EXPLORER_HELP",
    "MAX_ROWS",
    "PAGE_ROWS",
    "TREE_HELP",
    "TREE_TYPES",
    "tree_types",
    "valid_types",
]

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
    "AWS::ECS::Service",
    "AWS::ECR::Repository",
    "AWS::CloudFormation::Stack",
    "AWS::Logs::LogGroup",
    "AWS::StepFunctions::StateMachine",
    "AWS::ApiGateway::RestApi",
    "AWS::ApiGatewayV2::Api",
    "AWS::SNS::Topic",
    "AWS::SQS::Queue",
    "AWS::IAM::Role",
    "AWS::SSM::Parameter",
)


# The branches the landing tree opens with *by default* - the types most often
# wanted, in the order they are most often wanted. Nothing is fetched until a
# branch is expanded, so the list can afford to be a little generous; anything
# missing is one `:` away and is then added as a further branch.
# The `C` panel overrides this per user (`config.tree_types`); this list stays the
# fallback for a fresh install, and the target of "reset to defaults".
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
    # A parameter is where an app's configuration actually lives, so it is worth
    # a landing branch - and a `SecureString` leaf is safe to show because a
    # listing carries no value at all (`DescribeParameters` never returns one).
    "AWS::SSM::Parameter",
)


def valid_types(names: Sequence[str]) -> list[str]:
    """Keep only what could actually be a Cloud Control type, in order, once each.

    A branch whose name is not a resource type can never load anything, so it
    would sit there with an error on it forever - worse than not offering it at
    all. `TypeName` also has an API minimum length of 10, which is why a
    "AWS::" prefix on its own is not enough of a check.
    """
    seen: set[str] = set()
    good: list[str] = []
    for name in names:
        text = (name or "").strip()
        if not text.startswith("AWS::") or len(text) < 10 or text.count("::") != 2:
            continue
        if text not in seen:
            seen.add(text)
            good.append(text)
    return good


def tree_types(configured: Sequence[str] | None = None) -> list[str]:
    """The branches to open the explorer with: the user's list, else the default.

    An **empty** answer is never returned. A config that lists nothing usable is a
    config that would show an empty tree, which is not a state anyone chose on
    purpose - so it falls back to `TREE_TYPES` rather than to nothing.
    """
    names = configured if configured is not None else clitkaconfig.load().tree_types
    return valid_types(names) or list(TREE_TYPES)


# How many resources are handed to the table at a time, and where listing stops.
# ponytail: a fixed display cap rather than true on-demand paging. Ceiling: a type
# with more than MAX_ROWS resources is shown truncated (the heading says so).
# Upgrade path: keep the NextToken and fetch more when the cursor nears the end.
PAGE_ROWS = 100
MAX_ROWS = 2000


def _self_check() -> None:
    assert "AWS::S3::Bucket" in COMMON_TYPES
    assert all(name.startswith("AWS::") for name in COMMON_TYPES)
    assert len(set(COMMON_TYPES)) == len(COMMON_TYPES), "duplicate type"
    assert 0 < PAGE_ROWS < MAX_ROWS

    assert all(name.startswith("AWS::") for name in TREE_TYPES)
    assert len(set(TREE_TYPES)) == len(TREE_TYPES), "duplicate branch"
    # Every default branch must also be a palette fallback, so a ListTypes denial
    # never leaves the user unable to reopen one of them.
    assert set(TREE_TYPES) <= set(COMMON_TYPES)

    # The configured branches: kept in order, deduplicated, and nonsense dropped.
    assert valid_types(["AWS::S3::Bucket"]) == ["AWS::S3::Bucket"]
    assert valid_types([" AWS::S3::Bucket "]) == ["AWS::S3::Bucket"]
    assert valid_types(["AWS::S3::Bucket", "AWS::S3::Bucket"]) == ["AWS::S3::Bucket"]
    two = ["AWS::SQS::Queue", "AWS::S3::Bucket"]
    assert valid_types(two) == two, "the user's order is the order"
    for bad in ("", "   ", "S3::Bucket", "AWS::S3", "AWS::a::b::c", "AWS::x"):
        assert valid_types([bad]) == [], bad

    # An empty or unusable list must never produce an empty tree.
    assert tree_types([]) == list(TREE_TYPES)
    assert tree_types(["nonsense"]) == list(TREE_TYPES)
    assert tree_types(["AWS::SQS::Queue"]) == ["AWS::SQS::Queue"]
    # A configured branch need NOT be in COMMON_TYPES - the user asked for it.
    assert tree_types(["AWS::Kinesis::Stream"]) == ["AWS::Kinesis::Stream"]

    assert "F9" in EXPLORER_HELP
    assert "page up/down" in EXPLORER_HELP, "the paging keys must be documented"
    for text in (EXPLORER_HELP, TREE_HELP):
        assert text.endswith("\n") and "F10" in text
        # The context switches are letters now - F2 must not be promised anywhere.
        assert "F2 " not in text, text
        assert "F3   view" in text or "F4   edit" in text, text
        # The shell handoff has no menu-bar slot, so the help is its only home.
        assert "\n  x    open a shell" in text, text
        assert "W    time window" in text, text
        # `C` is the only key that writes to disk, so it must be documented.
        assert "C    config" in text, text
    assert "enter / space" in TREE_HELP
    # The sub-branches are the only route to an ECS task, so they must be named.
    assert "Tasks sub-branch" in TREE_HELP
    assert "left / right" in TREE_HELP, "keyboard tab switching must be documented"
    assert "1mo" in TREE_HELP, "the custom-duration syntax must be spelled out"

    print("[OK] resource types self-check passed")


if __name__ == "__main__":
    _self_check()
