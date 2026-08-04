from __future__ import annotations

import json
from pathlib import Path

import httpx

from app.services.microsoft_oauth import OAuthTokenProvider, load_oauth_config, save_oauth_config


def test_refresh_token_exchange_and_rotation(tmp_path: Path) -> None:
    path = tmp_path / "microsoft-oauth.json"
    save_oauth_config(
        path,
        {
            "mailbox_address": "mother@outlook.com",
            "client_id": "client-id",
            "authority": "consumers",
            "refresh_token": "old-refresh",
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/consumers/oauth2/v2.0/token")
        form = dict(httpx.QueryParams(request.content.decode()))
        assert form["refresh_token"] == "old-refresh"
        return httpx.Response(
            200,
            json={"access_token": "access-1", "expires_in": 3600, "refresh_token": "new-refresh"},
        )

    provider = OAuthTokenProvider(path, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert provider.get_access_token() == "access-1"
    assert load_oauth_config(path)["refresh_token"] == "new-refresh"


def test_token_provider_invalidates_cached_access_token_when_config_changes(tmp_path: Path) -> None:
    path = tmp_path / "microsoft-oauth.json"
    save_oauth_config(
        path,
        {
            "mailbox_address": "mother@outlook.com",
            "client_id": "client-id",
            "authority": "consumers",
            "refresh_token": "old-refresh",
        },
    )
    seen_refresh_tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        form = dict(httpx.QueryParams(request.content.decode()))
        refresh_token = form["refresh_token"]
        seen_refresh_tokens.append(refresh_token)
        return httpx.Response(
            200,
            json={"access_token": f"access-for-{refresh_token}", "expires_in": 3600},
        )

    provider = OAuthTokenProvider(path, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert provider.get_access_token() == "access-for-old-refresh"

    save_oauth_config(
        path,
        {
            "mailbox_address": "mother@outlook.com",
            "client_id": "client-id",
            "authority": "consumers",
            "refresh_token": "new-refresh",
        },
    )

    assert provider.get_access_token() == "access-for-new-refresh"
    assert seen_refresh_tokens == ["old-refresh", "new-refresh"]


def test_token_provider_reuses_access_token_without_repeating_refresh_exchange(tmp_path: Path) -> None:
    path = tmp_path / "microsoft-oauth.json"
    save_oauth_config(
        path,
        {
            "mailbox_address": "mother@outlook.com",
            "client_id": "client-id",
            "authority": "consumers",
            "refresh_token": "refresh",
        },
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"access_token": "access", "expires_in": 3600})

    provider = OAuthTokenProvider(path, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert provider.get_access_token() == "access"
    assert provider.get_access_token() == "access"
    assert calls == 1


def test_oauth_config_write_is_restricted(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "oauth.json"
    save_oauth_config(
        path,
        {
            "mailbox_address": "a@b.com",
            "client_id": "client-id",
            "authority": "consumers",
            "refresh_token": "x",
        },
    )

    assert json.loads(path.read_text())["mailbox_address"] == "a@b.com"
    assert path.stat().st_mode & 0o777 == 0o600
