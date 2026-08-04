from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import CAPTCHA_TTL_SECONDS, PUBLIC_SESSION_TTL_SECONDS
from app.models import PrivateTarget, PublicSession
from app.services.captcha import (
    captcha_answer_mac,
    generate_captcha_answer,
    render_captcha_svg,
    verify_captcha_answer,
)
from app.services.instance_secrets import encrypt_secret_text, session_id_hash


def session_is_verified(session: PublicSession, now: datetime | None = None) -> bool:
    current = now or datetime.utcnow()
    return session.verified_at is not None and session.expires_at > current


def session_is_valid(session: PublicSession, now: datetime | None = None) -> bool:
    current = now or datetime.utcnow()
    return session.expires_at > current


def find_session(db: Session, raw_session_id: str, target_id: int) -> PublicSession | None:
    digest = session_id_hash(raw_session_id)
    session = db.scalar(
        select(PublicSession).where(
            PublicSession.session_id_hash == digest,
            PublicSession.target_id == target_id,
        )
    )
    if session is None or not session_is_valid(session):
        if session is not None:
            db.delete(session)
            db.commit()
        return None
    return session


def create_captcha_session(
    db: Session,
    target: PrivateTarget,
    captcha_secret: str,
    *,
    now: datetime | None = None,
) -> tuple[str, PublicSession, str]:
    current = now or datetime.utcnow()
    raw_id = secrets.token_urlsafe(32)
    answer = generate_captcha_answer()
    record = PublicSession(
        session_id_hash=session_id_hash(raw_id),
        target_id=target.id,
        captcha_answer_mac=captcha_answer_mac(captcha_secret, answer),
        captcha_payload=encrypt_secret_text(captcha_secret, render_captcha_svg(answer)),
        captcha_expires_at=current + timedelta(seconds=CAPTCHA_TTL_SECONDS),
        expires_at=current + timedelta(seconds=PUBLIC_SESSION_TTL_SECONDS),
        created_at=current,
        last_seen_at=current,
    )
    db.add(record)
    db.flush()
    return raw_id, record, answer


def rotate_captcha(
    session: PublicSession,
    captcha_secret: str,
    *,
    now: datetime | None = None,
) -> str:
    current = now or datetime.utcnow()
    answer = generate_captcha_answer()
    session.captcha_answer_mac = captcha_answer_mac(captcha_secret, answer)
    session.captcha_payload = encrypt_secret_text(captcha_secret, render_captcha_svg(answer))
    session.captcha_expires_at = current + timedelta(seconds=CAPTCHA_TTL_SECONDS)
    return answer


def verify_session_captcha(
    session: PublicSession,
    answer: str,
    captcha_secret: str,
    *,
    now: datetime | None = None,
) -> bool:
    current = now or datetime.utcnow()
    if session.verified_at is not None or session.captcha_expires_at <= current:
        return False
    if not verify_captcha_answer(captcha_secret, session.captcha_answer_mac, answer):
        rotate_captcha(session, captcha_secret, now=current)
        return False
    session.verified_at = current
    session.expires_at = current + timedelta(seconds=PUBLIC_SESSION_TTL_SECONDS)
    session.captcha_expires_at = current
    session.last_seen_at = current
    return True


def touch_session(session: PublicSession, *, now: datetime | None = None) -> None:
    current = now or datetime.utcnow()
    session.last_seen_at = current


def clear_expired_sessions(db: Session, *, now: datetime | None = None) -> int:
    current = now or datetime.utcnow()
    result = db.execute(delete(PublicSession).where(PublicSession.expires_at <= current))
    return result.rowcount or 0
