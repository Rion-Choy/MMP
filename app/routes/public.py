from __future__ import annotations

import hashlib
from datetime import datetime

from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response as PlainResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import CAPTCHA_TTL_SECONDS, PUBLIC_SESSION_TTL_SECONDS, PUBLIC_SESSION_COOKIE
from app.models import MailMessage, MailRecipient, PrivateTarget
from app.services.instance_secrets import decrypt_secret_text, load_instance_secrets
from app.services.public_session import (
    create_captcha_session,
    find_session,
    rotate_captcha,
    session_is_verified,
    touch_session,
    verify_session_captcha,
)
from app.services.target_service import get_active_target
from app.templates import templates

router = APIRouter()


def db_session(request: Request) -> Session:
    return request.app.state.session_factory()


def instance_secret(request: Request, key: str) -> str:
    secrets = load_instance_secrets(request.app.state.instance_secrets_path)
    value = secrets.get(key)
    if not isinstance(value, str):
        raise HTTPException(status_code=503, detail="instance is not initialized")
    return value


def public_session_cookie_name(token: str) -> str:
    """Return a browser cookie name isolated to one public mailbox token."""
    suffix = hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]
    return f"{PUBLIC_SESSION_COOKIE}_{suffix}"


def no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def set_public_session_cookie(request: Request, response: Response, token: str, raw_id: str) -> Response:
    response.set_cookie(
        public_session_cookie_name(token),
        raw_id,
        httponly=True,
        samesite="strict",
        secure=not request.app.state.testing,
        max_age=PUBLIC_SESSION_TTL_SECONDS,
        path="/",
    )
    return response


def render_captcha_fragment(request: Request, token: str) -> str:
    return templates.env.get_template("public/_captcha_content.html").render(
        request=request,
        token=token,
        error=None,
        captcha_ttl=CAPTCHA_TTL_SECONDS,
    )


def _messages_context(
    db: Session,
    *,
    token: str,
    target: PrivateTarget,
    page: int,
) -> dict[str, object]:
    page_size = 20
    offset = (page - 1) * page_size
    query = (
        select(MailMessage)
        .join(MailRecipient)
        .where(MailRecipient.normalized_email == target.normalized_email)
        .order_by(MailMessage.received_at.desc(), MailMessage.id.desc())
        .distinct()
        .offset(offset)
        .limit(page_size + 1)
    )
    messages = list(db.scalars(query))
    has_next = len(messages) > page_size
    messages = messages[:page_size]
    return {
        "token": token,
        "messages": messages,
        "page": page,
        "has_next": has_next,
        "target_email": target.email_address,
    }


def render_messages_fragment(
    request: Request,
    *,
    token: str,
    target: PrivateTarget,
    page: int,
    db: Session,
) -> str:
    context = _messages_context(
        db,
        token=token,
        target=target,
        page=page,
    )
    context["request"] = request
    return templates.env.get_template("public/_messages_content.html").render(**context)


def get_or_create_public_session(request: Request, db: Session, token: str, target_id: int):
    cookie_name = public_session_cookie_name(token)
    raw_id = request.cookies.get(cookie_name)
    if raw_id:
        session = find_session(db, raw_id, target_id)
        if session is not None:
            return raw_id, session, False
    target = db.get(PrivateTarget, target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="not found")
    captcha_secret = instance_secret(request, "captcha_secret")
    raw_id, session, answer = create_captcha_session(db, target, captcha_secret)
    db.commit()
    return raw_id, session, True


@router.get("/m/{token}", response_class=HTMLResponse)
def public_entry(request: Request, token: str) -> Response:
    db = db_session(request)
    try:
        target = get_active_target(db, token)
        if target is None:
            raise HTTPException(status_code=404, detail="not found")
        raw_id, session, created = get_or_create_public_session(request, db, token, target.id)
        if session_is_verified(session):
            response = RedirectResponse(f"/m/{token}/view?page=1", status_code=303)
        else:
            response = templates.TemplateResponse(
                request,
                "public/captcha.html",
                {"token": token, "error": None, "captcha_ttl": CAPTCHA_TTL_SECONDS},
            )
        if created:
            set_public_session_cookie(request, response, token, raw_id)
        return no_store(response)
    finally:
        db.close()


@router.get("/m/{token}/captcha.svg")
def captcha_image(request: Request, token: str) -> Response:
    db = db_session(request)
    try:
        target = get_active_target(db, token)
        if target is None:
            raise HTTPException(status_code=404, detail="not found")
        raw_id = request.cookies.get(public_session_cookie_name(token))
        session = find_session(db, raw_id, target.id) if raw_id else None
        if session is None or session_is_verified(session):
            raise HTTPException(status_code=404, detail="captcha unavailable")
        if not session.captcha_payload:
            raise HTTPException(status_code=404, detail="captcha unavailable")
        captcha_secret = instance_secret(request, "captcha_secret")
        try:
            svg = decrypt_secret_text(captcha_secret, session.captcha_payload)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="captcha unavailable") from exc
        response = PlainResponse(svg, media_type="image/svg+xml")
        return no_store(response)
    finally:
        db.close()


@router.post("/m/{token}/verify")
def verify_public_captcha(request: Request, token: str, answer: str = Form(...)) -> Response:
    db = db_session(request)
    try:
        target = get_active_target(db, token)
        if target is None:
            raise HTTPException(status_code=404, detail="not found")
        raw_id = request.cookies.get(public_session_cookie_name(token))
        session = find_session(db, raw_id, target.id) if raw_id else None
        if session is None:
            return no_store(RedirectResponse(f"/m/{token}", status_code=303))
        captcha_secret = instance_secret(request, "captcha_secret")
        if not verify_session_captcha(session, answer, captcha_secret):
            db.commit()
            response = RedirectResponse(f"/m/{token}", status_code=303)
            return no_store(response)
        touch_session(session)
        db.commit()
        return no_store(RedirectResponse(f"/m/{token}/view?page=1", status_code=303))
    finally:
        db.close()


@router.get("/m/{token}/view", response_class=HTMLResponse)
def public_messages(request: Request, token: str, page: int = 1) -> Response:
    if page < 1:
        raise HTTPException(status_code=400, detail="invalid page")
    db = db_session(request)
    try:
        target = get_active_target(db, token)
        if target is None:
            raise HTTPException(status_code=404, detail="not found")
        raw_id = request.cookies.get(public_session_cookie_name(token))
        session = find_session(db, raw_id, target.id) if raw_id else None
        if session is None or not session_is_verified(session):
            return no_store(RedirectResponse(f"/m/{token}", status_code=303))
        touch_session(session)
        db.commit()
        context = _messages_context(db, token=token, target=target, page=page)
        response = templates.TemplateResponse(
            request,
            "public/messages.html",
            context,
        )
        return no_store(response)
    finally:
        db.close()


@router.post("/m/{token}/refresh")
def refresh_public_messages(request: Request, token: str, page: int = 1) -> Response:
    if page < 1:
        raise HTTPException(status_code=400, detail="invalid page")
    db = db_session(request)
    try:
        target = get_active_target(db, token)
        if target is None:
            raise HTTPException(status_code=404, detail="not found")

        raw_id = request.cookies.get(public_session_cookie_name(token))
        session = find_session(db, raw_id, target.id) if raw_id else None
        created = False
        if session is None:
            captcha_secret = instance_secret(request, "captcha_secret")
            raw_id, session, _ = create_captcha_session(db, target, captcha_secret)
            created = True

        if not session_is_verified(session):
            if session.captcha_expires_at <= datetime.utcnow():
                rotate_captcha(session, instance_secret(request, "captcha_secret"))
            db.commit()
            response = JSONResponse(
                {"status": "captcha_required", "html": render_captcha_fragment(request, token)}
            )
            if created:
                set_public_session_cookie(request, response, token, raw_id)
            return no_store(response)

        touch_session(session)
        db.commit()
        payload = {
            "status": "ok",
            "source": "database",
            "html": render_messages_fragment(
                request,
                token=token,
                target=target,
                page=page,
                db=db,
            ),
        }
        response = JSONResponse(payload)
        return no_store(response)
    finally:
        db.close()
