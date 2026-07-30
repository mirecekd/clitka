"""IAM Identity Center (SSO) device authorization flow.

The whole point is interoperability: the token this writes is the token
`aws` CLI v2 reads, and vice versa (see `clitka.core.ssocache`). The flow is the
standard OAuth 2.0 device grant against `sso-oidc`:

    register_client -> start_device_authorization -> create_token (polled)

Target resolution and status reporting live in `clitka.core.ssotargets`.

ponytail: the refresh-token grant is not used - an expired token means a new
device authorization. Ceiling: one browser confirmation per token lifetime
(typically 8-12 h). Upgrade path: try `create_token(grantType=refresh_token)`
with the stored refreshToken before falling back to the device flow.
"""

from __future__ import annotations

import datetime as dt
import time
import webbrowser
from collections.abc import Callable
from typing import Any

import boto3
from botocore import UNSIGNED
from botocore.config import Config

from clitka.core import ssocache
from clitka.core.errors import AuthError, wrap_aws_errors
from clitka.core.ssotargets import SsoTarget

_CLIENT_NAME = "clitka"
_CLIENT_TYPE = "public"
_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
_POLL_FLOOR = 1.0
_DEFAULT_TIMEOUT = 300.0

Reporter = Callable[[str], None]


def oidc_client(region: str) -> Any:
    """An unsigned `sso-oidc` client - no credentials exist yet at login time."""
    return boto3.Session(region_name=region).client(
        "sso-oidc",
        region_name=region,
        config=Config(signature_version=UNSIGNED, user_agent_extra=_CLIENT_NAME),
    )


@wrap_aws_errors
def _register(client: Any, target: SsoTarget) -> dict[str, Any]:
    return client.register_client(
        clientName=_CLIENT_NAME,
        clientType=_CLIENT_TYPE,
        scopes=list(target.scopes),
    )


@wrap_aws_errors
def _authorize(client: Any, client_id: str, secret: str, target: SsoTarget) -> dict[str, Any]:
    return client.start_device_authorization(
        clientId=client_id,
        clientSecret=secret,
        startUrl=target.start_url,
    )


def _registration(client: Any, target: SsoTarget) -> tuple[str, str, str | None]:
    """Reuse a cached client registration if it is still valid, else register."""
    cached = ssocache.read(target.key)
    if cached is not None:
        client_id, secret = cached.registration
        if client_id and secret:
            return client_id, secret, None
    reg = _register(client, target)
    expires = reg.get("clientSecretExpiresAt")
    expires_at = (
        ssocache.to_aws_time(dt.datetime.fromtimestamp(int(expires), dt.UTC))
        if isinstance(expires, int | float)
        else None
    )
    return str(reg["clientId"]), str(reg["clientSecret"]), expires_at


def _poll_token(
    client: Any,
    client_id: str,
    secret: str,
    device_code: str,
    interval: float,
    timeout: float,
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    """Poll create_token until the user confirms in the browser."""
    deadline = time.monotonic() + timeout
    wait = max(interval, _POLL_FLOOR)
    errs = client.exceptions
    while True:
        try:
            return client.create_token(
                clientId=client_id,
                clientSecret=secret,
                grantType=_GRANT,
                deviceCode=device_code,
            )
        except errs.AuthorizationPendingException:
            pass
        except errs.SlowDownException:
            wait += 5
        except errs.ExpiredTokenException as exc:
            raise AuthError("the device code expired before it was approved") from exc
        except errs.AccessDeniedException as exc:
            raise AuthError("the login request was denied in the browser") from exc
        if time.monotonic() >= deadline:
            raise AuthError("timed out waiting for the browser confirmation")
        sleep(wait)


def login(
    target: SsoTarget,
    open_browser: bool = False,
    report: Reporter | None = None,
    client: Any | None = None,
    force: bool = False,
    timeout: float = _DEFAULT_TIMEOUT,
    sleep: Callable[[float], None] = time.sleep,
) -> ssocache.SsoToken:
    """Log in to `target`, reusing a still-valid cached token unless `force`."""
    say = report or (lambda _msg: None)
    if not force:
        cached = ssocache.read(target.key)
        if cached is not None and cached.is_valid():
            say(f"[OK] already signed in to {target.start_url}")
            return cached

    oidc = client or oidc_client(target.region)
    client_id, secret, reg_expires = _registration(oidc, target)
    auth = _authorize(oidc, client_id, secret, target)

    url = auth.get("verificationUriComplete") or auth["verificationUri"]
    say(f"Confirm the login in a browser:\n  {url}")
    say(f"User code: {auth.get('userCode', '-')}")
    if open_browser:
        webbrowser.open(url)

    token = _poll_token(
        oidc,
        client_id,
        secret,
        str(auth["deviceCode"]),
        float(auth.get("interval", 5)),
        timeout,
        sleep,
    )
    expires_at = ssocache.now() + dt.timedelta(seconds=int(token.get("expiresIn", 3600)))
    stored = ssocache.write(
        target.key,
        {
            "startUrl": target.start_url,
            "region": target.region,
            "accessToken": token.get("accessToken"),
            "refreshToken": token.get("refreshToken"),
            "expiresAt": ssocache.to_aws_time(expires_at),
            "clientId": client_id,
            "clientSecret": secret,
            "registrationExpiresAt": reg_expires,
            "scopes": list(target.scopes),
        },
    )
    say(f"[OK] signed in, token valid until {stored.raw['expiresAt']}")
    return stored


def logout(target: SsoTarget, forget_registration: bool = False) -> bool:
    """Remove the cached token for `target`. Returns True if anything changed."""
    return ssocache.forget(target.key, keep_registration=not forget_registration)
