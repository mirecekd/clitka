"""Error types and the single place where botocore errors are translated."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, TypeVar

from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    NoCredentialsError,
    ProfileNotFound,
    SSOError,
    TokenRetrievalError,
)

F = TypeVar("F", bound=Callable[..., Any])

# AWS says "your login ran out" in several dialects. All of them mean the same
# thing to a user: sign in again.
EXPIRED_CODES = (
    "ExpiredToken",
    "ExpiredTokenException",
    "InvalidClientTokenId",
    "UnrecognizedClientException",
)


class ClitkaError(Exception):
    """Base class for all CLITKA errors."""


class ConfigError(ClitkaError):
    """Something is wrong with the AWS or CLITKA configuration."""


class AuthError(ClitkaError):
    """Credentials are missing or expired."""

    def __init__(self, message: str, profile: str | None = None) -> None:
        self.profile = profile
        super().__init__(message)


class ExpiredLoginError(AuthError):
    """The SSO login ran out. The only cure is signing in again.

    A distinct type so the TUI can offer the device flow instead of printing a
    red line the user can do nothing about, and so the CLI can say
    "run `clitka auth login`".
    """

    def __init__(self, message: str, profile: str | None = None) -> None:
        super().__init__(message, profile)

    def hint(self) -> str:
        where = f" -p {self.profile}" if self.profile else ""
        return f"Sign in again: `clitka auth login{where}`"


class ReadOnlyError(ClitkaError):
    """A mutating operation was attempted in read-only mode."""


class AwsError(ClitkaError):
    """An AWS API call failed. Carries enough context to be actionable."""

    def __init__(
        self,
        operation: str,
        code: str,
        message: str,
        profile: str | None = None,
        region: str | None = None,
    ) -> None:
        self.operation = operation
        self.code = code
        self.aws_message = message
        self.profile = profile
        self.region = region
        super().__init__(str(self))

    def __str__(self) -> str:
        where = f"profile={self.profile or '-'} region={self.region or '-'}"
        base = f"[{self.code}] {self.operation} failed ({where}): {self.aws_message}"
        if self.code in ("AccessDenied", "AccessDeniedException", "UnauthorizedOperation"):
            return base + "\nHint: the current identity lacks the required IAM permission."
        return base


def _operation_name(exc: ClientError) -> str:
    return str(exc.operation_name or "AWS call")


def _where(args: tuple[Any, ...]) -> tuple[str | None, str | None]:
    """Find the profile/region to blame, if a Context was passed in.

    Duck-typed on purpose: importing Context here would be circular.
    """
    for arg in args[:2]:
        if hasattr(arg, "profile") and hasattr(arg, "effective_region"):
            try:
                return arg.profile, arg.effective_region
            except Exception:
                return getattr(arg, "profile", None), None
    return None, None


def wrap_aws_errors(func: F) -> F:
    """Translate botocore exceptions into CLITKA errors.

    Applied at the boundary of every AWS-calling function so that no raw
    botocore exception ever reaches the CLI or the TUI.
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except ClientError as exc:
            err = exc.response.get("Error", {})
            meta = exc.response.get("ResponseMetadata", {})
            profile, region = _where(args)
            code = str(err.get("Code", "ClientError"))
            if code in EXPIRED_CODES:
                raise ExpiredLoginError(
                    f"{_operation_name(exc)}: {err.get('Message', code)}", profile
                ) from exc
            raise AwsError(
                operation=_operation_name(exc),
                code=str(err.get("Code", "ClientError")),
                message=str(err.get("Message", str(exc))),
                profile=profile,
                region=region or meta.get("HTTPHeaders", {}).get("x-amz-region"),
            ) from exc
        except ProfileNotFound as exc:
            raise ConfigError(str(exc)) from exc
        except NoCredentialsError as exc:
            raise AuthError(str(exc)) from exc
        except (TokenRetrievalError, SSOError) as exc:
            # "Token has expired and refresh failed" arrives here, not as a
            # ClientError - botocore fails while *resolving* credentials.
            profile, _ = _where(args)
            raise ExpiredLoginError(str(exc), profile) from exc
        except BotoCoreError as exc:
            raise ClitkaError(str(exc)) from exc

    return wrapper  # type: ignore[return-value]


def _self_check() -> None:
    """Minimal runnable check for the error translation layer."""

    @wrap_aws_errors
    def boom() -> None:
        raise ClientError(
            {
                "Error": {"Code": "AccessDenied", "Message": "no"},
                "ResponseMetadata": {},
            },
            "ListBuckets",
        )

    try:
        boom()
    except AwsError as exc:
        assert exc.code == "AccessDenied", exc.code
        assert exc.operation == "ListBuckets", exc.operation
        assert "IAM permission" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("AwsError was not raised")

    @wrap_aws_errors
    def stale() -> None:
        raise TokenRetrievalError(provider="sso", error_msg="Token has expired")

    try:
        stale()
    except ExpiredLoginError as exc:
        assert "expired" in str(exc).lower(), exc
        assert "auth login" in exc.hint()
    else:  # pragma: no cover
        raise AssertionError("ExpiredLoginError was not raised")

    @wrap_aws_errors
    def stale_call() -> None:
        raise ClientError(
            {"Error": {"Code": "ExpiredToken", "Message": "gone"}, "ResponseMetadata": {}},
            "ListResources",
        )

    try:
        stale_call()
    except ExpiredLoginError as exc:
        assert "ListResources" in str(exc), exc
    else:  # pragma: no cover
        raise AssertionError("ExpiredLoginError was not raised for ExpiredToken")

    # An ExpiredLoginError must still be catchable as the generic AuthError.
    assert issubclass(ExpiredLoginError, AuthError)
    print("[OK] errors self-check passed")


if __name__ == "__main__":
    _self_check()
