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
)

F = TypeVar("F", bound=Callable[..., Any])


class ClitkaError(Exception):
    """Base class for all CLITKA errors."""


class ConfigError(ClitkaError):
    """Something is wrong with the AWS or CLITKA configuration."""


class AuthError(ClitkaError):
    """Credentials are missing or expired."""

    def __init__(self, message: str, profile: str | None = None) -> None:
        self.profile = profile
        super().__init__(message)


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
            raise AwsError(
                operation=_operation_name(exc),
                code=str(err.get("Code", "ClientError")),
                message=str(err.get("Message", str(exc))),
                region=meta.get("HTTPHeaders", {}).get("x-amz-region"),
            ) from exc
        except ProfileNotFound as exc:
            raise ConfigError(str(exc)) from exc
        except NoCredentialsError as exc:
            raise AuthError(str(exc)) from exc
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

    print("[OK] errors self-check passed")


if __name__ == "__main__":
    _self_check()
