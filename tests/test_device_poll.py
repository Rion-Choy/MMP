from __future__ import annotations

import httpx
import pytest

from app.services.microsoft_oauth import (
    DeviceAuthorizationPending,
    DeviceAuthorizationSlowDown,
    poll_device_code,
)


def test_poll_device_code_returns_tokens_after_authorization() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert b"grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Adevice_code" in request.content
        return httpx.Response(200, json={"access_token": "access", "refresh_token": "refresh", "expires_in": 3600})

    result = poll_device_code(
        client_id="client-id",
        authority="consumers",
        device_code="device-code",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert result["refresh_token"] == "refresh"


def test_poll_device_code_reports_pending_and_slow_down() -> None:
    for error, expected in (("authorization_pending", DeviceAuthorizationPending), ("slow_down", DeviceAuthorizationSlowDown)):
        def handler(request: httpx.Request, error=error) -> httpx.Response:
            return httpx.Response(400, json={"error": error})

        with pytest.raises(expected):
            poll_device_code(
                client_id="client-id",
                authority="consumers",
                device_code="device-code",
                client=httpx.Client(transport=httpx.MockTransport(handler)),
            )
