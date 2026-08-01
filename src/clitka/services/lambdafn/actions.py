"""What F9 offers on a Lambda function, and what the preview pane shows.

Both are published through pluggy hooks, so the tree and the F9 menu never import
this module - they only ever see `Action` and `PreviewTab` objects.

Invoking is deliberately **not** an F9 action: an action runs unattended and
returns one finished result, and firing someone's function with `{}` because they
pressed a key is not a thing this app does. F9 offers the *command* to invoke it,
the same way the logs plugin offers the tail command.
"""

from __future__ import annotations

from clitka.core import lambdafn as lb
from clitka.core import logs as lg
from clitka.core import preview as pv
from clitka.core import timerange as tr
from clitka.core.actions import Action, ActionResult, ResourceRef
from clitka.core.context import Context
from clitka.core.lambdamodel import function_name_of, stamp
from clitka.services.logs.actions import escape_markup

TYPE_NAME = "AWS::Lambda::Function"
EVENT_LIMIT = 200


def is_function(ref: ResourceRef) -> bool:
    return ref.type_name == TYPE_NAME


def function_name(ref: ResourceRef) -> str:
    """The function's name. Cloud Control identifies a function *by* its name.

    An ARN is tolerated because a plugin, a CLI argument or a hand-typed palette
    entry can all supply one, and `GetFunction` would take it - but the title of
    a result reading `arn:aws:lambda:...` instead of `my-fn` would not.
    """
    raw = ref.identifier or str(ref.row.get("FunctionName", ""))
    return function_name_of(raw)


def _lines(pairs: list[tuple[str, str]]) -> str:
    """Label/value pairs as an aligned block - the shape every tab here uses."""
    if not pairs:
        return "[dim](nothing to show)[/dim]"
    width = max(len(label) for label, _ in pairs)
    return "\n".join(f"[dim]{label:<{width}}[/dim]  {value}" for label, value in pairs)


def show_config(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: the function's configuration, as `GetFunction` reports it."""
    fn = lb.get_function(ctx, function_name(ref))
    pairs = [
        ("runtime", fn.runtime or fn.package_type),
        ("handler", fn.handler or "-"),
        ("memory", f"{fn.memory} MB"),
        ("timeout", f"{fn.timeout} s"),
        ("version", fn.version or "-"),
        ("arch", ", ".join(fn.architectures) or "-"),
        ("role", fn.role or "-"),
        ("modified", stamp(fn.modified) or "-"),
        ("log group", fn.log_group),
    ]
    if not fn.healthy:
        pairs.insert(0, ("state", f"{fn.state} - {fn.state_reason or 'no reason given'}"))
    return ActionResult(f"{fn.name} - configuration", _lines(pairs))


def show_env(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: the environment variables.

    The values are shown as they are: anyone who can read them here can read them
    in the console, and hiding them would only make the tab useless.
    """
    fn = lb.get_function(ctx, function_name(ref))
    pairs = sorted(fn.env.items())
    body = _lines(pairs) if pairs else "[dim](no environment variables)[/dim]"
    return ActionResult(f"{fn.name} - environment", body)


def show_invoke_hint(ctx: Context, ref: ResourceRef) -> ActionResult:
    """F9: how to invoke this function, from a shell.

    ponytail: the menu hands over the command instead of invoking. Ceiling: the
    user changes window. Upgrade path: a payload editor screen plus a confirm,
    which is the `editors` hook M4 leaves for later.
    """
    name = function_name(ref)
    where = f" -p {ctx.profile}" if ctx.profile else ""
    return ActionResult(
        f"{name} - invoke",
        "Invoke it from a shell:\n\n"
        f"  clitka{where} lambda invoke {name} --payload '{{}}'\n"
        f"  clitka{where} lambda invoke {name} --payload-file event.json\n\n"
        "Add `--async` to fire and forget, `--no-logs` to skip the log tail.",
    )


# The ids are namespaced, so two plugins can never collide in the F9 menu.
ACTIONS: tuple[Action, ...] = (
    Action(
        id="lambda.config",
        label="Configuration",
        run=show_config,
        key="c",
        applies_to=is_function,
    ),
    Action(
        id="lambda.env",
        label="Environment variables",
        run=show_env,
        key="e",
        applies_to=is_function,
    ),
    Action(
        id="lambda.invoke",
        label="Invoke (how to)",
        run=show_invoke_hint,
        key="v",
        applies_to=is_function,
    ),
)


def build_config_tab(ctx: Context, ref: ResourceRef) -> str:
    """The `Function` preview tab - the same block F9 shows, beside the tree."""
    return show_config(ctx, ref).body


def build_logs_tab(ctx: Context, ref: ResourceRef) -> str:
    """The `Recent logs` tab: the function's own log group, without leaving it.

    This is the payoff of `Function.log_group` - the logs plugin's machinery, aimed
    at a Lambda. A function that has never run has no log group at all, which is
    normal and must read as such.
    """
    name = function_name(ref)
    group = lb.Function(name).log_group
    minutes = tr.minutes()
    try:
        events = lg.recent_events(ctx, group, minutes=minutes, limit=EVENT_LIMIT)
    except Exception as exc:
        if "ResourceNotFound" in type(exc).__name__ or "ResourceNotFound" in str(exc):
            return f"[dim](no log group yet - {group} does not exist)[/dim]"
        raise
    window = tr.human(minutes)
    if not events:
        return f"[dim](nothing in {group} in the last {window})[/dim]"
    head = f"[dim]{len(events)} event(s) in the last {window} - {group}[/dim]"
    body = "\n".join(escape_markup(event.line()) for event in events)
    return f"{head}\n\n{body}"


PREVIEWS: tuple[pv.PreviewTab, ...] = (
    pv.PreviewTab(
        id="lambda.config",
        label="Function",
        build=build_config_tab,
        applies_to=is_function,
        lazy=True,  # it calls GetFunction
    ),
    pv.PreviewTab(
        id="lambda.logs",
        label="Recent logs",
        build=build_logs_tab,
        applies_to=is_function,
        lazy=True,  # it calls FilterLogEvents
    ),
)


def _self_check() -> None:
    ref = ResourceRef.from_row(TYPE_NAME, {"identifier": "my-fn"})
    assert is_function(ref)
    assert not is_function(ResourceRef.from_row("AWS::S3::Bucket", {}))
    assert function_name(ref) == "my-fn"
    # Cloud Control sometimes reports the name as a property, or as an ARN.
    assert function_name(ResourceRef(TYPE_NAME, "", {"FunctionName": "other"})) == "other"
    arn = "arn:aws:lambda:eu-central-1:1:function:from-arn"
    assert function_name(ResourceRef(TYPE_NAME, arn, {})) == "from-arn"

    ids = [action.id for action in ACTIONS]
    keys = [action.key for action in ACTIONS]
    assert len(set(ids)) == len(ids) and len(set(keys)) == len(keys), keys
    assert all(action.applies_to(ref) for action in ACTIONS)
    # Nothing here mutates anything, so nothing needs a confirm dialog.
    assert not any(action.destructive for action in ACTIONS)

    hint = show_invoke_hint(Context(profile="sw-sandbox"), ref)
    assert "clitka -p sw-sandbox lambda invoke my-fn" in hint.body
    assert "-p " not in show_invoke_hint(Context(), ref).body

    assert _lines([("a", "1"), ("bbb", "2")]).count("\n") == 1
    assert "nothing to show" in _lines([])

    assert [tab.id for tab in PREVIEWS] == ["lambda.config", "lambda.logs"]
    assert all(tab.lazy and tab.matches_type(TYPE_NAME) for tab in PREVIEWS)
    print("[OK] lambda actions self-check passed")


if __name__ == "__main__":
    _self_check()
