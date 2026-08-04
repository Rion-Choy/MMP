from __future__ import annotations

from typing import Callable

from fastapi import Depends, HTTPException, Request

from app.config import ADMIN_SESSION_COOKIE
from app.services.admin_session import verify_admin_cookie
from app.services.instance_secrets import load_instance_secrets


def require_admin(request: Request) -> None:
    try:
        instance = load_instance_secrets(request.app.state.instance_secrets_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail="instance is not initialized") from exc
    if not verify_admin_cookie(instance, request.cookies.get(ADMIN_SESSION_COOKIE)):
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
