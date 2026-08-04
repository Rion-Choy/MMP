from __future__ import annotations

import json
import os
import time
from urllib.parse import urlencode
from pathlib import Path
from threading import Lock
from typing import Any

import httpx


DEFAULT_AUTHORITY = "consumers"
GRAPH_SCOPES = "openid profile email offline_access User.Read Mail.Read"
OAuthConfigFingerprint = tuple[str, str, str, str]


def _oauth_error_details(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        return ""
    details: list[str] = []
    for key in ("error", "error_description", "trace_id", "correlation_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            details.append(f"{key}={value.strip()}")
    return "; ".join(details)


def _raise_oauth_http_error(response: httpx.Response, operation: str) -> None:
    details = _oauth_error_details(response)
    suffix = f": {details}" if details else ""
    raise OAuthError(f"{operation} failed with HTTP {response.status_code}{suffix}")


def _oauth_config_fingerprint(config: dict[str, Any]) -> OAuthConfigFingerprint:
    return (
        str(config.get("mailbox_address") or "").strip().casefold(),
        str(config.get("client_id") or "").strip(),
        str(config.get("authority") or DEFAULT_AUTHORITY).strip() or DEFAULT_AUTHORITY,
        str(config.get("refresh_token") or ""),
    )


def build_authorization_url(*, client_id: str, authority: str, redirect_uri: str, state: str, code_challenge: str) -> str:
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": GRAPH_SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"https://login.microsoftonline.com/{authority}/oauth2/v2.0/authorize?{urlencode(params)}"


def exchange_authorization_code(*, client_id: str, authority: str, code: str, code_verifier: str, redirect_uri: str, client: httpx.Client | None = None) -> dict[str, Any]:
    http = client or httpx.Client(timeout=20)
    response = http.post(
        f"https://login.microsoftonline.com/{authority}/oauth2/v2.0/token",
        data={
            "client_id": client_id,
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "scope": GRAPH_SCOPES,
        },
    )
    if response.status_code >= 400:
        _raise_oauth_http_error(response, "authorization code exchange")
    payload = response.json()
    if not isinstance(payload.get("refresh_token"), str):
        raise OAuthError("authorization response has no refresh_token")
    return payload


def request_device_code(*, client_id: str, authority: str, client: httpx.Client | None = None) -> dict[str, Any]:
    http = client or httpx.Client(timeout=20)
    response = http.post(
        f"https://login.microsoftonline.com/{authority}/oauth2/v2.0/devicecode",
        data={"client_id": client_id, "scope": GRAPH_SCOPES},
    )
    if response.status_code >= 400:
        _raise_oauth_http_error(response, "device code request")
    payload = response.json()
    for key in ("device_code", "user_code", "verification_uri"):
        if not isinstance(payload.get(key), str):
            raise OAuthError(f"device code response has no {key}")
    return payload


def poll_device_code(*, client_id: str, authority: str, device_code: str, client: httpx.Client | None = None) -> dict[str, Any]:
    http = client or httpx.Client(timeout=20)
    response = http.post(
        f"https://login.microsoftonline.com/{authority}/oauth2/v2.0/token",
        data={
            "client_id": client_id,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
        },
    )
    if response.status_code >= 400:
        error = response.json().get("error") if response.headers.get("content-type", "").startswith("application/json") else ""
        if error == "authorization_pending":
            raise DeviceAuthorizationPending("device authorization is pending")
        if error == "slow_down":
            raise DeviceAuthorizationSlowDown("device authorization polling is too fast")
        _raise_oauth_http_error(response, "device code exchange")
    payload = response.json()
    if not isinstance(payload.get("refresh_token"), str):
        raise OAuthError("device response has no refresh_token")
    return payload


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def load_oauth_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_oauth_config(path: Path, payload: dict[str, Any]) -> None:
    required = {"mailbox_address", "client_id", "authority", "refresh_token"}
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"missing OAuth configuration fields: {sorted(missing)}")
    _atomic_write_json(path, payload)


def oauth_config_from_tokens(
    *,
    mailbox_address: str,
    client_id: str,
    authority: str,
    token_payload: dict[str, Any],
    auth_method: str,
) -> dict[str, Any]:
    refresh_token = token_payload.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise OAuthError("OAuth response has no refresh_token")
    return {
        "mailbox_address": str(mailbox_address).strip().casefold(),
        "client_id": str(client_id).strip(),
        "authority": str(authority).strip() or DEFAULT_AUTHORITY,
        "refresh_token": refresh_token,
        "auth_method": auth_method,
    }


class OAuthError(RuntimeError):
    pass


def validate_access_token_for_mailbox(
    *,
    access_token: str,
    mailbox_address: str,
    client: httpx.Client | None = None,
) -> dict[str, str]:
    """Validate an access token against Graph /me before persisting OAuth config.

    The token returned by web/device authorization is short-lived, but it lets us
    verify that the Microsoft account the user authorized is the mailbox they
    entered.  Only the resulting long-lived refresh token is persisted.
    """
    token = str(access_token or "").strip()
    configured = str(mailbox_address or "").strip().casefold()
    if not token:
        raise OAuthError("OAuth response has no access_token")
    if not configured:
        raise OAuthError("mailbox address is required")

    owns_client = client is None
    http = client or httpx.Client(timeout=20)
    try:
        response = http.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )
        if response.status_code >= 400:
            raise OAuthError(f"Graph account validation failed with HTTP {response.status_code}")
        payload = response.json()
    finally:
        if owns_client:
            http.close()

    account = payload.get("mail") or payload.get("userPrincipalName")
    if not isinstance(account, str) or account.strip().casefold() != configured:
        raise OAuthError("Graph account does not match configured mailbox address")
    return {"mailbox_address": configured, "account_address": account.strip().casefold()}


class DeviceAuthorizationPending(OAuthError):
    pass


class DeviceAuthorizationSlowDown(OAuthError):
    pass


def validate_oauth_config(config: dict[str, Any], *, client: httpx.Client | None = None) -> dict[str, str]:
    """Exchange the configured refresh token and verify the /me account."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="mail-portal-oauth-check-") as directory:
        path = Path(directory) / "oauth.json"
        save_oauth_config(path, config)
        provider = OAuthTokenProvider(path, client=client)
        token = provider.get_access_token()
        graph = client or httpx.Client(timeout=20)
        response = graph.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        if response.status_code >= 400:
            raise OAuthError(f"Graph account validation failed with HTTP {response.status_code}")
        payload = response.json()
    account = payload.get("mail") or payload.get("userPrincipalName")
    configured = str(config.get("mailbox_address", "")).strip().casefold()
    if not isinstance(account, str) or account.strip().casefold() != configured:
        raise OAuthError("Graph account does not match configured mailbox address")
    return {"mailbox_address": configured, "account_address": account.strip().casefold()}


class OAuthTokenProvider:
    def __init__(self, config_path: Path, *, client: httpx.Client | None = None) -> None:
        self.config_path = config_path
        self.client = client or httpx.Client(timeout=20)
        self._lock = Lock()
        self._access_token: str | None = None
        self._expires_at = 0.0
        self._config_fingerprint: OAuthConfigFingerprint | None = None

    def get_access_token(self, *, force_refresh: bool = False) -> str:
        with self._lock:
            config = load_oauth_config(self.config_path)
            fingerprint = _oauth_config_fingerprint(config)
            if self._config_fingerprint != fingerprint:
                self._access_token = None
                self._expires_at = 0.0
                self._config_fingerprint = fingerprint
            if not force_refresh and self._access_token and time.time() < self._expires_at - 60:
                return self._access_token
            authority = str(config.get("authority") or DEFAULT_AUTHORITY)
            client_id = config.get("client_id")
            refresh_token = config.get("refresh_token")
            if not isinstance(client_id, str) or not isinstance(refresh_token, str):
                raise OAuthError("OAuth client_id or refresh_token is missing")
            url = f"https://login.microsoftonline.com/{authority}/oauth2/v2.0/token"
            response = self.client.post(
                url,
                data={
                    "client_id": client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "scope": "https://graph.microsoft.com/User.Read https://graph.microsoft.com/Mail.Read offline_access",
                },
            )
            if response.status_code >= 400:
                raise OAuthError(f"token refresh failed with HTTP {response.status_code}")
            payload = response.json()
            access_token = payload.get("access_token")
            if not isinstance(access_token, str):
                raise OAuthError("token response has no access_token")
            rotated = payload.get("refresh_token")
            if isinstance(rotated, str) and rotated and rotated != refresh_token:
                config["refresh_token"] = rotated
                save_oauth_config(self.config_path, config)
            self._access_token = access_token
            self._expires_at = time.time() + int(payload.get("expires_in", 3600))
            self._config_fingerprint = _oauth_config_fingerprint(config)
            return access_token
