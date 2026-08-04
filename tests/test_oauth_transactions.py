from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.models import OAuthTransaction
from app.services.oauth_transactions import create_transaction, decode_transaction_payload, get_transaction


def test_oauth_transaction_payload_is_encrypted_and_expires() -> None:
    from datetime import datetime, timedelta
    from app.database import Base, create_engine_for_tests, make_session_factory

    engine = create_engine_for_tests()
    Base.metadata.create_all(engine)
    db = make_session_factory(engine)()
    transaction = create_transaction(db, flow_type="web", payload={"client_id": "client", "secret": "value"}, secret="secret-key")
    db.commit()

    stored = db.scalar(select(OAuthTransaction).where(OAuthTransaction.id == transaction.id))
    assert stored is not None
    assert "secret" not in stored.payload_encrypted
    assert decode_transaction_payload(stored, "secret-key")["client_id"] == "client"
    assert get_transaction(db, transaction.transaction_id, flow_type="web") is not None


def test_oauth_transaction_cannot_be_reused_after_consumption() -> None:
    from app.database import Base, create_engine_for_tests, make_session_factory
    from app.services.oauth_transactions import consume_transaction

    engine = create_engine_for_tests()
    Base.metadata.create_all(engine)
    db = make_session_factory(engine)()
    transaction = create_transaction(db, flow_type="device", payload={"device_code": "x"}, secret="secret-key")
    db.commit()
    consume_transaction(db, transaction)
    db.commit()

    assert get_transaction(db, transaction.transaction_id, flow_type="device") is None
