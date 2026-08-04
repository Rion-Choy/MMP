from __future__ import annotations

import re
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import MailMessage, MailRecipient, PrivateTarget, PublicSession


def test_public_url_get_creates_captcha_session(client: TestClient) -> None:
    token = "11111111-1111-4111-8111-111111111111"
    db = client.app.state.session_factory()
    db.add(
        PrivateTarget(
            email_address="private@example.com",
            normalized_email="private@example.com",
            access_token=token,
        )
    )
    db.commit()
    db.close()

    response = client.get(f"/m/{token}")

    assert response.status_code == 200
    assert "验证码" in response.text
    assert "mail_portal_session" in response.headers.get("set-cookie", "")

    captcha = client.get(f"/m/{token}/captcha.svg")
    assert captcha.status_code == 200
    assert re.search(r">([A-Za-z0-9]{4})</text>", captcha.text)


def test_target_token_is_uuid4_and_can_be_disabled() -> None:
    from app.database import Base, create_engine_for_tests, make_session_factory
    from app.services.target_service import create_target, disable_target, get_active_target

    engine = create_engine_for_tests()
    Base.metadata.create_all(engine)
    db = make_session_factory(engine)()
    target = create_target(db, "private@example.com")
    db.commit()

    assert target.enabled is True
    assert get_active_target(db, target.access_token) is target
    disable_target(db, target.id)
    db.commit()
    assert get_active_target(db, target.access_token) is None


def test_expired_public_session_is_not_usable() -> None:
    from app.services.public_session import session_is_verified

    now = datetime.utcnow()
    session = PublicSession(
        session_id_hash="x",
        target_id=1,
        captcha_answer_mac="x",
        captcha_expires_at=now - timedelta(seconds=1),
        verified_at=now - timedelta(minutes=40),
        expires_at=now - timedelta(seconds=1),
        created_at=now - timedelta(hours=1),
        last_seen_at=now - timedelta(hours=1),
    )

    assert session_is_verified(session, now) is False
