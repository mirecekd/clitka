"""Systems Manager, as one importable surface.

The service split into six modules under the 8 kB rule, and each split landed on
a seam worth having - but a caller should not have to know which of the six a
name lives in. So this module is a pure re-export, the same trick `core/ecs.py`
and `core/apigw.py` already use:

```
core/ssmmodel.py    Parameter, MASK, TYPES        (boto3-free)
core/ssmrunbook.py  Document, DocumentParameter   (boto3-free)
core/ssmcommand.py  refuses_run, Invocation       (boto3-free)
core/ssmparam.py    the parameter listing and reads
core/ssmput.py      writing and deleting a parameter
core/ssmdoc.py      the document listing and content
core/ssmrun.py      SendCommand and its result
```

**The one rule that runs through all of them: a `SecureString` is never decrypted
unless that exact call asked to be.** `--decrypt` on the CLI is the only thing
that asks; the tree, the preview pane and the F9 actions never do, and an
undecrypted secret renders as `MASK`. See `ssmmodel.py` for why the ciphertext is
not shown either.
"""

from __future__ import annotations

from clitka.core.ssmcommand import (
    DONE_STATES,
    Invocation,
    missing_parameters,
    refuses_run,
)
from clitka.core.ssmdoc import (
    Document,
    get_document,
    invocation,
    iter_documents,
    list_documents,
    run,
    wait_for,
)
from clitka.core.ssmmodel import (
    MASK,
    SECURE,
    TIERS,
    TYPES,
    Parameter,
    param_name_of,
    parent_path,
)
from clitka.core.ssmparam import (
    by_path,
    delete_parameter,
    get_parameter,
    history,
    iter_parameters,
    list_parameters,
    put_parameter,
)
from clitka.core.ssmrunbook import COMMAND, DocumentParameter

# CLITKA's own type strings for the tree and the F9 menu. `AWS::SSM::Parameter`
# is a real Cloud Control type, so a parameter can be a tree branch like any
# other resource. `AWS::SSM::Document` is real too, but a *Command* document is
# only interesting through this plugin.
PARAMETER = "AWS::SSM::Parameter"
DOCUMENT = "AWS::SSM::Document"

__all__ = [
    "COMMAND",
    "DOCUMENT",
    "DONE_STATES",
    "MASK",
    "PARAMETER",
    "SECURE",
    "TIERS",
    "TYPES",
    "Document",
    "DocumentParameter",
    "Invocation",
    "Parameter",
    "by_path",
    "delete_parameter",
    "get_document",
    "get_parameter",
    "history",
    "invocation",
    "iter_documents",
    "iter_parameters",
    "list_documents",
    "list_parameters",
    "missing_parameters",
    "param_name_of",
    "parent_path",
    "put_parameter",
    "refuses_run",
    "run",
    "wait_for",
]


def _self_check() -> None:
    # Everything promised must actually be importable from here - the whole
    # point of the module.
    missing = [name for name in __all__ if name not in globals()]
    assert not missing, f"re-export is incomplete: {missing}"

    # The SecureString rule, checked through the public surface a plugin sees.
    secret = Parameter("/db/pw", type=SECURE, value="AQICAHgc")
    assert secret.display_value() == MASK
    assert "AQICAHgc" not in secret.display_value()

    # And the run refusal, likewise.
    assert "not a Command one" in refuses_run(Document("x", document_type="Automation"), ["i-1"])

    assert PARAMETER.startswith("AWS::SSM::") and DOCUMENT.startswith("AWS::SSM::")
    print(f"[OK] ssm self-check passed ({len(__all__)} names re-exported)")


if __name__ == "__main__":
    _self_check()
