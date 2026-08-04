from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping
from typing import Any

import httpx


class GraphError(RuntimeError):
    pass


TokenProvider = Callable[..., str]


class GraphClient:
    def __init__(
        self,
        access_token_provider: TokenProvider,
        *,
        client: httpx.Client | None = None,
        base_url: str = "https://graph.microsoft.com/v1.0",
        max_retries: int = 3,
    ) -> None:
        self.access_token_provider = access_token_provider
        self.client = client or httpx.Client(timeout=30)
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries

    def _request(self, method: str, url: str, *, force_refresh: bool = False, **kwargs: Any) -> httpx.Response:
        token = self.access_token_provider(force_refresh=force_refresh)
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Prefer": 'IdType="ImmutableId"',
            }
        )
        response = self.client.request(method, url, headers=headers, **kwargs)
        return response

    def get(self, path_or_url: str, **kwargs: Any) -> dict[str, Any]:
        url = path_or_url if path_or_url.startswith("http") else f"{self.base_url}/{path_or_url.lstrip('/')}"
        response = self._request("GET", url, **kwargs)
        if response.status_code == 401:
            response = self._request("GET", url, force_refresh=True, **kwargs)
        if response.status_code >= 400:
            raise GraphError(f"Graph GET failed with HTTP {response.status_code}")
        return response.json()

    def iter_collection(self, path_or_url: str, **kwargs: Any) -> Iterator[dict[str, Any]]:
        next_url = path_or_url
        attempts = 0
        while next_url:
            url = next_url if next_url.startswith("http") else f"{self.base_url}/{next_url.lstrip('/')}"
            response = self._request("GET", url, **kwargs)
            if response.status_code == 401:
                response = self._request("GET", url, force_refresh=True, **kwargs)
            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempts >= self.max_retries:
                    raise GraphError(f"Graph collection failed with HTTP {response.status_code}")
                attempts += 1
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = min(float(retry_after), 8.0) if retry_after else min(2**attempts, 8)
                except ValueError:
                    delay = min(2**attempts, 8)
                time.sleep(delay)
                continue
            if response.status_code >= 400:
                raise GraphError(f"Graph collection failed with HTTP {response.status_code}")
            attempts = 0
            payload = response.json()
            values = payload.get("value", [])
            if not isinstance(values, list):
                raise GraphError("Graph collection response has invalid value")
            for value in values:
                if isinstance(value, Mapping):
                    yield dict(value)
            next_url = payload.get("@odata.nextLink")
            if next_url is not None and not isinstance(next_url, str):
                raise GraphError("Graph response has invalid nextLink")
