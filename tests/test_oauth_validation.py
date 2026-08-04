from __future__ import annotations

import httpx
import pytest

from app.services.microsoft_oauth import OAuthError, validate_oauth_config


def _client_for_account(account: str) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return httpx.Response(200, json={"access_token": "access-token", "expires_in": 3600})
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"mail": account, "userPrincipalName": account})
        raise AssertionError(request.url)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _config(address: str = "mother@outlook.com") -> dict[str, str]:
    return {
        "mailbox_address": address,
        "client_id": "client-id",
        "authority": "consumers",
        "refresh_token": "refresh-token",
    }


def test_validate_oauth_config_confirms_graph_account() -> None:
    result = validate_oauth_config(_config(), client=_client_for_account("mother@outlook.com"))

    assert result["mailbox_address"] == "mother@outlook.com"
    assert result["account_address"] == "mother@outlook.com"


def test_validate_oauth_config_rejects_wrong_graph_account() -> None:
    with pytest.raises(OAuthError, match="does not match"):
        validate_oauth_config(_config(), client=_client_for_account("different@outlook.com"))
