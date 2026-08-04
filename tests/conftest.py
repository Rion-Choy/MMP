from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient


# Importing app.main creates the production ASGI object. Keep that import isolated
# from /var/lib/mail-portal when the test suite runs as an unprivileged user.
os.environ["MAIL_PORTAL_DATA_DIR"] = tempfile.mkdtemp(prefix="mail-portal-tests-")


@pytest.fixture
def client(monkeypatch) -> TestClient:
    from app.main import create_app

    monkeypatch.setenv("MAIL_PORTAL_DATA_DIR", tempfile.mkdtemp(prefix="mail-portal-tests-"))
    return TestClient(
        create_app(testing=True, database_url_override="sqlite+pysqlite:///:memory:")
    )
