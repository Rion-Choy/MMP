from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from pathlib import Path

from app.config import database_url, instance_secrets_path, microsoft_oauth_path, oauth_config_path
from app.database import Base, create_engine_for_url, ensure_database_parent, make_session_factory
from app.models import AppSetting  # noqa: F401 - register models
from app.routes.public import router as public_router
from app.routes.admin import router as admin_router
from app.services.instance_secrets import initialize_instance


def create_app(*, testing: bool = False, database_url_override: str | None = None) -> FastAPI:
    url = database_url_override or database_url()
    ensure_database_parent(url)
    engine = create_engine_for_url(url, testing=testing)
    session_factory = make_session_factory(engine)
    if testing:
        Base.metadata.create_all(engine)

    app = FastAPI(title="Mail Portal", docs_url=None if not testing else "/docs")
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.testing = testing
    app.state.instance_secrets_path = instance_secrets_path()
    app.state.microsoft_oauth_path = microsoft_oauth_path()
    app.state.oauth_config_dir = app.state.microsoft_oauth_path.parent / "microsoft-oauth"
    app.state.oauth_redirect_uri = os.environ.get(
        "MAIL_PORTAL_OAUTH_REDIRECT_URI",
        "http://localhost:8000/admin/oauth/callback",
    )
    if testing and not app.state.instance_secrets_path.exists():
        initialize_instance(app.state.instance_secrets_path)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(public_router)
    app.include_router(admin_router)
    app.mount(
        "/static",
        StaticFiles(directory=str(Path(__file__).resolve().parent / "static")),
        name="static",
    )

    return app


app = create_app()
