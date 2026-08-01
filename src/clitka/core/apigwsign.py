"""SigV4 for an `execute-api` request. Ten lines, and one of them is the point.

Split out of `apigwinvoke.py` for the 8 kB rule. botocore does all the work - it
is already a dependency and hand-rolling a canonical request is exactly the kind
of thing that fails silently.

**The signing service name is `execute-api`, not `apigateway`.** Signing with the
latter produces a signature AWS rejects with a 403 that says nothing about why,
which is an afternoon nobody gets back.
"""

from __future__ import annotations

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from clitka.core.context import Context
from clitka.core.errors import ClitkaError

__all__ = ["SERVICE", "signed_headers"]

SERVICE = "execute-api"


def signed_headers(
    ctx: Context, url: str, method: str, headers: dict[str, str], payload: bytes = b""
) -> dict[str, str]:
    """`headers` plus the SigV4 `Authorization` an AWS_IAM route wants."""
    credentials = ctx.session.get_credentials()
    if credentials is None:
        raise ClitkaError("no credentials to sign with - run `clitka auth login`")
    request = AWSRequest(method=method, url=url, data=payload or b"", headers=dict(headers))
    region = ctx.effective_region or ""
    SigV4Auth(credentials.get_frozen_credentials(), SERVICE, region).add_auth(request)
    return dict(request.headers)


def _self_check() -> None:
    # The service name is the only thing here worth asserting, and it is asserted
    # because getting it wrong costs an afternoon of unexplained 403s.
    assert SERVICE == "execute-api"

    class _NoCredentials:
        """A context whose credential resolver found nothing."""

        class _Session:
            @staticmethod
            def get_credentials() -> None:
                return None

        session = _Session()
        effective_region = "eu-central-1"

    try:
        signed_headers(_NoCredentials(), "https://x/", "GET", {})  # type: ignore[arg-type]
    except ClitkaError as exc:
        assert "auth login" in str(exc), exc
    else:
        raise AssertionError("signing without credentials must be refused")
    print("[OK] apigw sign self-check passed")


if __name__ == "__main__":
    _self_check()
