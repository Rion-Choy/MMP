from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import PrivateTarget, PublicSession
from app.services.instance_secrets import session_id_hash, secret_mac


def test_public_session_schema_can_store_unverified_challenge() -> None:
    from app.database import Base, create_engine_for_tests, make_session_factory

    engine = create_engine_for_tests()
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    db = factory()
    target = PrivateTarget(
        email_address="private@example.com",
        normalized_email="private@example.com",
        access_token="00000000-0000-4000-8000-000000000000",
    )
    db.add(target)
    db.flush()
    now = datetime.utcnow()
    session = PublicSession(
        session_id_hash=session_id_hash("raw-session-id"),
        target_id=target.id,
        captcha_answer_mac=secret_mac("captcha-secret", "Ab3d"),
        captcha_expires_at=now + timedelta(minutes=5),
        expires_at=now + timedelta(minutes=30),
        created_at=now,
        last_seen_at=now,
    )
    db.add(session)
    db.commit()

    stored = db.scalar(select(PublicSession).where(PublicSession.target_id == target.id))
    assert stored is not None
    assert stored.verified_at is None
