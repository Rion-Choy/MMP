from __future__ import annotations

from pathlib import Path

from app.config import microsoft_oauth_path
from app.services.microsoft_graph import GraphClient
from app.services.microsoft_oauth import OAuthTokenProvider


def build_graph_client(config_path: Path | None = None) -> GraphClient:
    path = config_path or microsoft_oauth_path()
    provider = OAuthTokenProvider(path)
    return GraphClient(provider.get_access_token)
