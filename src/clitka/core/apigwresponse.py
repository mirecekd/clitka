"""What an API Gateway endpoint answered, and what a refusal really means.

Split out of `apigwinvoke.py` for the 8 kB rule; no socket and no boto3 here, so
every status-code interpretation is testable offline.

**The whole reason `hint()` exists** is the single most misleading message in AWS:
a 403 with `Missing Authentication Token`. It almost never means a token is
missing - it is what API Gateway says when the method+path matched *no route at
all*. Everybody loses an hour to it once.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

__all__ = ["MISLEADING", "Response"]

MISLEADING = "Missing Authentication Token"


@dataclass(frozen=True)
class Response:
    """What came back. `ok` is the HTTP verdict, `hint()` explains a refusal."""

    status: int
    body: str
    headers: dict[str, str] = field(default_factory=dict)
    elapsed_ms: int = 0
    url: str = ""
    signed: bool = False

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def content_type(self) -> str:
        """Header names arrive in whatever case the server chose."""
        for key, value in self.headers.items():
            if key.lower() == "content-type":
                return value
        return ""

    def pretty(self) -> str:
        """The body as indented JSON when it is JSON, otherwise verbatim."""
        try:
            return json.dumps(json.loads(self.body), indent=2, sort_keys=True)
        except ValueError:
            return self.body

    def summary(self) -> str:
        """One line for a table or a status bar."""
        return f"HTTP {self.status} in {self.elapsed_ms} ms"

    def hint(self) -> str:
        """What a refusal probably means, or "" when there is nothing to add."""
        if self.status == 403 and MISLEADING in self.body and not self.signed:
            return (
                "403 'Missing Authentication Token' from API Gateway almost never means "
                "a missing token - it is what an unmatched route says. Check the path and "
                "the stage; if the route is AWS_IAM-protected, invoke it with --sign."
            )
        if self.status == 403 and self.signed:
            return "signed but refused - the identity may lack execute-api:Invoke on this route"
        if self.status == 401:
            return "an authorizer refused this - the route is not open"
        if self.status == 404:
            return "the route matched a stage but not a resource - check the path"
        if self.status == 429:
            return "throttled by the stage's rate limit, or by the account's"
        if self.status == 502:
            return "the integration answered with something API Gateway could not use"
        if self.status == 504:
            return "the integration did not answer in time (29 s is the hard ceiling)"
        return ""


def _self_check() -> None:
    ok = Response(200, '{"b":1}', {"Content-Type": "application/json"}, elapsed_ms=12)
    assert ok.ok and ok.hint() == "" and ok.pretty() == '{\n  "b": 1\n}'
    assert ok.content_type == "application/json"
    assert ok.summary() == "HTTP 200 in 12 ms"
    # A lower-cased header name is the same header.
    assert Response(200, "", {"content-type": "text/plain"}).content_type == "text/plain"
    assert Response(200, "").content_type == ""
    # A non-JSON body is handed back untouched rather than losing it to a parser.
    assert Response(200, "not json").pretty() == "not json"

    # The 403 everyone misreads - and the same code, signed, means something else.
    assert "unmatched route" in Response(403, MISLEADING).hint()
    assert "execute-api:Invoke" in Response(403, MISLEADING, signed=True).hint()
    assert "authorizer" in Response(401, "").hint()
    assert "throttled" in Response(429, "").hint()
    assert "29 s" in Response(504, "").hint()
    # A 500 from someone's own handler has nothing to add - the body is the answer.
    assert not Response(500, "boom").ok and Response(500, "boom").hint() == ""
    # 2xx boundaries: 299 is still a success, 300 is not.
    assert Response(299, "").ok and not Response(300, "").ok
    print("[OK] apigw response self-check passed")


if __name__ == "__main__":
    _self_check()
