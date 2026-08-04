from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import ADMIN_SESSION_COOKIE, ADMIN_SESSION_TTL_SECONDS
from app.services.admin_auth import verify_admin_password
from app.services.instance_secrets import load_instance_secrets


def _serializer(cookie_secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(cookie_secret, salt="mail-portal-admin")


def create_admin_cookie(instance_secrets: dict[str, Any]) -> str:
    cookie_secret = instance_secrets.get("cookie_secret")
    if not isinstance(cookie_secret, str):
        raise ValueError("cookie secret is not configured")
    payload = {"nonce": secrets.token_urlsafe(24), "issued_at": int(datetime.utcnow().timestamp())}
    return _serializer(cookie_secret).dumps(payload)


def verify_admin_cookie(instance_secrets: dict[str, Any], cookie: str | None) -> bool:
    if not cookie:
        return False
    cookie_secret = instance_secrets.get("cookie_secret")
    if not isinstance(cookie_secret, str):
        return False
    try:
        _serializer(cookie_secret).loads(cookie, max_age=ADMIN_SESSION_TTL_SECONDS)
        return True
    except (BadSignature, SignatureExpired):
        return False


def authenticate_admin(instance_secrets: dict[str, Any], password: str) -> str | None:
    password_hash = instance_secrets.get("admin_password_hash")
    if not isinstance(password_hash, str) or not verify_admin_password(password, password_hash):
        return None
    return create_admin_cookie(instance_secrets)
