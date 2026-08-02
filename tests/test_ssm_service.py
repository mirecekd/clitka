"""`services/ssm/` - the eighth plugin seam, the F9 keys and the preview tabs.

The tests that matter here are not about SSM at all, they are about the two rules
this plugin could break by accident:

1. **no action may reveal a secret**, and
2. **no F9 key may collide** with anything else that applies to the same type -
   `ActionMenu.on_key` runs the *first* match, so a shared key silently does the
   wrong thing. That is how `d` for "Details" would once have deleted an EC2
   instance.
"""

from __future__ import annotations

import pytest
from botocore.stub import Stubber

import clitka.services.ssm as plugin
from clitka.core import actions as core_actions
from clitka.core import plugins, ssm
from clitka.core import preview as core_preview
from clitka.core.actions import ResourceRef
from clitka.core.context import Context
from clitka.services.ssm import actions as ssm_actions
from clitka.services.ssm import cli as ssm_cli

CIPHER = "AQICAHgcAAAA"


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    return Context(region="eu-central-1")


@pytest.fixture
def param_ref():
    return ResourceRef.from_row(ssm.PARAMETER, {"identifier": "/db/prod/password"})


@pytest.fixture
def doc_ref():
    return ResourceRef.from_row(ssm.DOCUMENT, {"identifier": "AWS-RunShellScript"})


def test_the_self_checks_pass():
    ssm_actions._self_check()
    ssm_cli._self_check()


# --- the plugin seam -------------------------------------------------------


def test_the_plugin_is_registered_and_answers_both_cli_hooks():
    assert "clitka.services.ssm" in plugins.BUILTIN_SERVICES
    names = dict(plugins.service_apps())
    # The user types `clitka ssm`, so that is the name the hook must return.
    assert "ssm" in names and names["ssm"] is ssm_cli.app
    assert plugin.clitka_service_name() == "ssm"


def test_every_ssm_action_and_tab_reaches_the_registry():
    ids = {action.id for action in core_actions.registered()}
    assert {action.id for action in ssm_actions.ACTIONS} <= ids
    tabs = {tab.id for tab in core_preview.registered()}
    assert {tab.id for tab in ssm_actions.PREVIEWS} <= tabs


def test_no_two_plugins_publish_the_same_action_or_tab_id():
    ids = [action.id for action in core_actions.registered()]
    assert len(set(ids)) == len(ids), "duplicate action id across plugins"
    tabs = [tab.id for tab in core_preview.registered()]
    assert len(set(tabs)) == len(tabs), "duplicate preview tab id across plugins"


def test_a_parameter_is_reachable_as_a_tree_branch():
    # `AWS::SSM::Parameter` is a real Cloud Control type, so the palette must be
    # able to offer it even when ListTypes is denied.
    from clitka.tui.restypes import COMMON_TYPES

    assert ssm.PARAMETER in COMMON_TYPES


# --- nothing here may reveal a secret --------------------------------------


def test_no_ssm_action_ever_decrypts(ctx, param_ref, monkeypatch):
    """Every action that reads a parameter must ask for decrypt=False.

    Checked by intercepting the call rather than by reading the source, so an
    action added later cannot quietly pass decrypt=True.
    """
    asked: list[bool] = []

    def watched(_ctx, name, decrypt=False):
        asked.append(decrypt)
        return ssm.Parameter(name, type=ssm.SECURE, value=CIPHER, decrypted=decrypt)

    monkeypatch.setattr(ssm, "get_parameter", watched)
    monkeypatch.setattr(ssm, "history", lambda *_a, **_k: [])
    monkeypatch.setattr(ssm, "by_path", lambda *_a, **_k: [])

    for action in ssm_actions.ACTIONS:
        if not action.applies_to(param_ref):
            continue
        result = action.run(ctx, param_ref)
        # Whatever it shows, the plaintext-shaped blob must not be in it.
        assert CIPHER not in result.body, action.id
    assert asked and not any(asked), f"an action asked to decrypt: {asked}"


def test_the_preview_tab_masks_a_secure_string(ctx, param_ref):
    with Stubber(ctx.client("ssm")) as stub:
        stub.add_response(
            "get_parameter",
            {
                "Parameter": {
                    "Name": "/db/prod/password",
                    "Type": "SecureString",
                    "Value": CIPHER,
                    "Version": 3,
                }
            },
            {"Name": "/db/prod/password", "WithDecryption": False},
        )
        body = ssm_actions.build_parameter_tab(ctx, param_ref)
    assert ssm.MASK in body and CIPHER not in body


def test_revealing_is_a_command_not_a_value(ctx, param_ref):
    # The whole SecureString decision in one test: F9 hands over the command.
    told = ssm_actions.how_to_read(ctx, param_ref)
    assert "clitka ssm get /db/prod/password --decrypt" in told.body
    assert CIPHER not in told.body


def test_no_ssm_action_mutates_anything():
    # A destructive action would mean a confirm dialog exists for it, and nothing
    # here should ever need one: everything reads or explains.
    assert not any(action.destructive for action in ssm_actions.ACTIONS)
    # And nothing named like a write is offered at all.
    for action in ssm_actions.ACTIONS:
        assert not any(word in action.id for word in ("delete", "put", "write")), action.id


def test_running_a_document_is_not_offered_as_an_action(ctx, doc_ref):
    """`ssm.run` explains; it must not send anything.

    Stubbing nothing but describe_document proves it: a SendCommand would fail
    the test because no response is armed for it.
    """
    with Stubber(ctx.client("ssm")) as stub:
        stub.add_response(
            "describe_document",
            {
                "Document": {
                    "Name": "AWS-RunShellScript",
                    "DocumentType": "Command",
                    "Parameters": [{"Name": "commands", "Type": "StringList"}],
                }
            },
            {"Name": "AWS-RunShellScript"},
        )
        result = ssm_actions.how_to_run(ctx, doc_ref)
        stub.assert_no_pending_responses()
    assert "clitka ssm run AWS-RunShellScript" in result.body
    # It names the parameter the document insists on, so the command is usable.
    assert "-p commands=" in result.body


def test_a_document_that_cannot_be_run_says_so_instead(ctx):
    ref = ResourceRef.from_row(ssm.DOCUMENT, {"identifier": "my-automation"})
    with Stubber(ctx.client("ssm")) as stub:
        stub.add_response(
            "describe_document",
            {"Document": {"Name": "my-automation", "DocumentType": "Automation"}},
            {"Name": "my-automation"},
        )
        result = ssm_actions.how_to_run(ctx, ref)
    assert "not a Command one" in result.body
    # And it must not hand over a command that would fail.
    assert "clitka ssm run" not in result.body


# --- the F9 keys -----------------------------------------------------------


@pytest.mark.parametrize("type_name", [ssm.PARAMETER, ssm.DOCUMENT])
def test_no_key_is_claimed_twice_on_either_type(type_name):
    ref = ResourceRef.from_row(type_name, {"identifier": "x"})
    offered = core_actions.available(core_actions.registered(), ref)
    keys = [action.key for action in offered if action.key]
    assert len(set(keys)) == len(keys), f"duplicate F9 key on {type_name}: {keys}"


def test_the_baseline_actions_still_apply_to_a_parameter(param_ref):
    # Which is exactly why the keys had to be checked: `resources.*` is there too.
    offered = {action.id for action in core_actions.available(core_actions.registered(), param_ref)}
    assert "resources.delete" in offered
    assert "ssm.parameter" in offered


def test_a_parameter_action_does_not_apply_to_a_document_and_vice_versa(param_ref, doc_ref):
    for action in ssm_actions.ACTIONS:
        assert action.applies_to(param_ref) != action.applies_to(doc_ref), action.id


def test_the_tabs_are_lazy_because_they_call_aws():
    # A tab that is not lazy is built for every selection, whether shown or not.
    assert all(tab.lazy for tab in ssm_actions.PREVIEWS)


def test_a_tab_only_applies_to_its_own_type():
    param_tab, doc_tab = ssm_actions.PREVIEWS
    assert param_tab.matches_type(ssm.PARAMETER) and not param_tab.matches_type(ssm.DOCUMENT)
    assert doc_tab.matches_type(ssm.DOCUMENT) and not doc_tab.matches_type(ssm.PARAMETER)
    assert not param_tab.matches_type("AWS::S3::Bucket")


# --- the CLI ---------------------------------------------------------------


def test_document_parameters_are_parsed_as_lists():
    # SendCommand takes a list per parameter whatever the declared type, and a
    # repeated flag is how a multi-line `commands` is given.
    assert ssm_cli.parse_parameters(["commands=a", "commands=b"]) == {"commands": ["a", "b"]}


def test_a_malformed_parameter_is_refused_with_a_sentence():
    with pytest.raises(ValueError, match="--param wants"):
        ssm_cli.parse_parameters(["commands"])


def test_the_document_commands_are_flat_under_ssm():
    # `clidoc.py` exists for the 8 kB rule only - the user must still type
    # `clitka ssm run`, not `clitka ssm doc run`.
    names = {str(command.name) for command in ssm_cli.app.registered_commands}
    assert {"params", "get", "docs", "doc", "run"} <= names, sorted(names)
