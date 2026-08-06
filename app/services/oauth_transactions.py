from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import OAuthTransaction
from app.services.instance_secrets import decrypt_secret_text, encrypt_secret_text, session_id_hash

OAUTH_TRANSACTION_TTL_SECONDS = 900
OAUTH_STATE_COOKIE = "mail_portal_oauth_state"


def generate_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def create_transaction(
    db: Session,
    *,
    flow_type: str,
    payload: dict[str, Any],
    secret: str,
    state: str | None = None,
    mother_mailbox_id: int | None = None,
    now: datetime | None = None,
) -> OAuthTransaction:
    current = now or datetime.utcnow()
    transaction = OAuthTransaction(
        transaction_id=str(uuid4()),
        mother_mailbox_id=mother_mailbox_id,
        state_hash=session_id_hash(state) if state else None,
        flow_type=flow_type,
        payload_encrypted=encrypt_secret_text(secret, json.dumps(payload, separators=(",", ":"))),
        created_at=current,
        expires_at=current + timedelta(seconds=OAUTH_TRANSACTION_TTL_SECONDS),
    )
    db.add(transaction)
    db.flush()
    return transaction


def decode_transaction_payload(transaction: OAuthTransaction, secret: str) -> dict[str, Any]:
    value = decrypt_secret_text(secret, transaction.payload_encrypted)
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError("invalid OAuth transaction payload")
    return payload


def get_transaction_by_id(db: Session, transaction_id: str, *, flow_type: str | None = None) -> OAuthTransaction | None:
    query = select(OAuthTransaction).where(OAuthTransaction.transaction_id == transaction_id)
    if flow_type:
        query = query.where(OAuthTransaction.flow_type == flow_type)
    return db.scalar(query)


def get_transaction(db: Session, transaction_id: str, *, flow_type: str | None = None) -> OAuthTransaction | None:
    query = select(OAuthTransaction).where(
        OAuthTransaction.transaction_id == transaction_id,
        OAuthTransaction.used_at.is_(None),
        OAuthTransaction.expires_at > datetime.utcnow(),
    )
    if flow_type:
        query = query.where(OAuthTransaction.flow_type == flow_type)
    return db.scalar(query)


def get_transaction_by_state(db: Session, state: str) -> OAuthTransaction | None:
    return db.scalar(
        select(OAuthTransaction).where(
            OAuthTransaction.state_hash == session_id_hash(state),
            OAuthTransaction.flow_type == "web",
            OAuthTransaction.used_at.is_(None),
            OAuthTransaction.expires_at > datetime.utcnow(),
        )
    )


def consume_transaction(db: Session, transaction: OAuthTransaction) -> None:
    transaction.used_at = datetime.utcnow()
    db.flush()


def delete_expired_transactions(db: Session) -> int:
    from sqlalchemy import delete

    result = db.execute(delete(OAuthTransaction).where(OAuthTransaction.expires_at <= datetime.utcnow()))
    return result.rowcount or 0
