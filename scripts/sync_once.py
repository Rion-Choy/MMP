from __future__ import annotations

import os

from app.config import database_url
from app.database import create_engine_for_url, ensure_database_parent, make_session_factory
from app.services.graph_factory import build_graph_client
from app.worker import SyncWorker


if __name__ == "__main__":
    url = database_url()
    ensure_database_parent(url)
    engine = create_engine_for_url(url)
    factory = make_session_factory(engine)
    worker = SyncWorker(factory, build_graph_client)
    worker.run_forever()
