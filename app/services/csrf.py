from __future__ import annotations

import secrets
from hmac import compare_digest

from fastapi import HTTPException, Request

from app.config import ADMIN_CSRF_COOKIE


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def validate_csrf(request: Request, submitted: str | None) -> None:
    cookie = request.cookies.get(ADMIN_CSRF_COOKIE)
    if not cookie or not submitted or not compare_digest(cookie, submitted):
        raise HTTPException(status_code=403, detail="invalid CSRF token")
