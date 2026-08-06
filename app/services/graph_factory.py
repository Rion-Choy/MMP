from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import microsoft_oauth_path, oauth_config_path
from app.services.microsoft_graph import GraphClient
from app.services.microsoft_oauth import OAuthTokenProvider


def build_graph_client(config_path: Path | None = None) -> GraphClient:
    path = config_path or microsoft_oauth_path()
    provider = OAuthTokenProvider(path)
    return GraphClient(provider.get_access_token)


def build_graph_client_for_mailbox(mailbox: int | Any | None = None) -> GraphClient:
    if mailbox is None:
        return build_graph_client()
    mailbox_id = int(getattr(mailbox, "id", mailbox))
    return build_graph_client(oauth_config_path(mailbox_id))
