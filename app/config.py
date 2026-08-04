from __future__ import annotations

import os
from pathlib import Path


DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "runtime"


def app_data_dir() -> Path:
    return Path(os.environ.get("MAIL_PORTAL_DATA_DIR", str(DEFAULT_DATA_DIR)))


def data_dir() -> Path:
    return app_data_dir()


def database_url() -> str:
    explicit = os.environ.get("MAIL_PORTAL_DATABASE_URL")
    if explicit:
        return explicit
    return f"sqlite+pysqlite:///{data_dir() / 'data' / 'mail-portal.sqlite3'}"


def instance_secrets_path() -> Path:
    return Path(
        os.environ.get(
            "MAIL_PORTAL_INSTANCE_SECRETS",
            str(data_dir() / "secrets" / "instance-secrets.json"),
        )
    )


def microsoft_oauth_path() -> Path:
    return Path(
        os.environ.get(
            "MAIL_PORTAL_MICROSOFT_OAUTH",
            str(data_dir() / "secrets" / "microsoft-oauth.json"),
        )
    )
def sync_lock_path() -> Path:
    return data_dir() / "sync.lock"


PUBLIC_SESSION_COOKIE = os.environ.get("MAIL_PORTAL_PUBLIC_SESSION_COOKIE", "mail_portal_session")
ADMIN_SESSION_COOKIE = os.environ.get("MAIL_PORTAL_ADMIN_SESSION_COOKIE", "mail_portal_admin")
ADMIN_CSRF_COOKIE = os.environ.get("MAIL_PORTAL_ADMIN_CSRF_COOKIE", "mail_portal_admin_csrf")
PUBLIC_SESSION_TTL_SECONDS = int(os.environ.get("MAIL_PORTAL_PUBLIC_SESSION_TTL", "1800"))
CAPTCHA_TTL_SECONDS = int(os.environ.get("MAIL_PORTAL_CAPTCHA_TTL", "300"))
ADMIN_SESSION_TTL_SECONDS = int(os.environ.get("MAIL_PORTAL_ADMIN_SESSION_TTL", "86400"))
APP_HOST = os.environ.get("MAIL_PORTAL_HOST", "127.0.0.1")
# Keep repository defaults generic; production deployments must provide this
# through MAIL_PORTAL_PORT in the service environment.
APP_PORT = int(os.environ.get("MAIL_PORTAL_PORT", "8000"))
