from __future__ import annotations

import httpx
import pytest

from app.services.microsoft_oauth import (
    OAuthError,
    build_authorization_url,
    exchange_authorization_code,
    request_device_code,
)


def test_authorization_url_contains_pkce_and_state() -> None:
    url = build_authorization_url(
        client_id="client-id",
        authority="consumers",
        redirect_uri="https://mail.example.com/admin/oauth/callback",
        state="state-value",
        code_challenge="challenge-value",
    )

    assert "response_type=code" in url
    assert "client_id=client-id" in url
    assert "state=state-value" in url
    assert "code_challenge=challenge-value" in url
    assert "code_challenge_method=S256" in url
    assert "offline_access" in url


def test_exchange_authorization_code_returns_refresh_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/consumers/oauth2/v2.0/token")
        assert b"grant_type=authorization_code" in request.content
        assert b"code_verifier=verifier" in request.content
        return httpx.Response(200, json={"access_token": "access", "refresh_token": "refresh", "expires_in": 3600})

    result = exchange_authorization_code(
        client_id="client-id",
        authority="consumers",
        code="auth-code",
        code_verifier="verifier",
        redirect_uri="https://mail.example.com/admin/oauth/callback",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert result["access_token"] == "access"
    assert result["refresh_token"] == "refresh"


def test_request_device_code_returns_user_instructions() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/consumers/oauth2/v2.0/devicecode")
        assert b"client_id=client-id" in request.content
        return httpx.Response(200, json={
            "device_code": "device-secret",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://microsoft.com/devicelogin",
            "expires_in": 900,
            "interval": 5,
            "message": "Use the code",
        })

    result = request_device_code(
        client_id="client-id",
        authority="consumers",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert result["user_code"] == "ABCD-EFGH"
    assert result["verification_uri"].startswith("https://")


def test_request_device_code_preserves_microsoft_error_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": "unauthorized_client",
                "error_description": "AADSTS700016: application was not found",
                "trace_id": "trace-device",
            },
        )

    with pytest.raises(OAuthError, match="unauthorized_client.*AADSTS700016.*trace-device"):
        request_device_code(
            client_id="client-id",
            authority="consumers",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )


def test_exchange_authorization_code_preserves_microsoft_error_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": "invalid_grant",
                "error_description": "AADSTS50011: redirect URI mismatch",
                "correlation_id": "corr-web",
            },
        )

    with pytest.raises(OAuthError, match="invalid_grant.*AADSTS50011.*corr-web"):
        exchange_authorization_code(
            client_id="client-id",
            authority="consumers",
            code="auth-code",
            code_verifier="verifier",
            redirect_uri="https://mail.example.com/admin/oauth/callback",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
