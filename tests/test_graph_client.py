from __future__ import annotations

import httpx

from app.services.microsoft_graph import GraphClient


def test_graph_get_follows_next_links_and_sends_immutable_id_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/messages") and "skiptoken" not in request.url.query.decode():
            return httpx.Response(
                200,
                json={
                    "value": [{"id": "m1"}],
                    "@odata.nextLink": "https://graph.test/me/mailFolders/inbox/messages?$skiptoken=2",
                },
            )
        return httpx.Response(200, json={"value": [{"id": "m2"}]})

    client = GraphClient(
        access_token_provider=lambda force_refresh=False: "access-token",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        base_url="https://graph.test",
    )
    values = list(client.iter_collection("/me/mailFolders/inbox/messages"))

    assert [item["id"] for item in values] == ["m1", "m2"]
    assert all(request.headers["Prefer"] == 'IdType="ImmutableId"' for request in seen)


def test_graph_retries_once_after_401() -> None:
    calls: list[str] = []

    def token_provider(force_refresh: bool = False) -> str:
        calls.append("refresh" if force_refresh else "normal")
        return "new-token" if force_refresh else "old-token"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["Authorization"] == "Bearer old-token":
            return httpx.Response(401)
        return httpx.Response(200, json={"id": "me", "mail": "mother@example.com"})

    client = GraphClient(
        access_token_provider=token_provider,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        base_url="https://graph.test",
    )
    assert client.get("/me")["id"] == "me"
    assert calls == ["normal", "refresh"]
