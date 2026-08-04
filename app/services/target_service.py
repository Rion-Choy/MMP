from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PrivateTarget
from app.services.email_normalization import normalize_email_address


def create_target(db: Session, email_address: str) -> PrivateTarget:
    normalized = normalize_email_address(email_address)
    for _ in range(10):
        token = str(uuid4())
        if db.scalar(select(PrivateTarget).where(PrivateTarget.access_token == token)) is None:
            target = PrivateTarget(
                email_address=email_address.strip(),
                normalized_email=normalized,
                access_token=token,
                enabled=True,
            )
            db.add(target)
            db.flush()
            return target
    raise RuntimeError("could not generate a unique access token")


def get_active_target(db: Session, token: str) -> PrivateTarget | None:
    try:
        UUID(token, version=4)
    except ValueError:
        return None
    return db.scalar(
        select(PrivateTarget).where(
            PrivateTarget.access_token == token,
            PrivateTarget.enabled.is_(True),
            PrivateTarget.removed_at.is_(None),
        )
    )


def get_target_by_id(db: Session, target_id: int) -> PrivateTarget | None:
    return db.get(PrivateTarget, target_id)


def enable_target(db: Session, target_id: int) -> PrivateTarget | None:
    target = db.get(PrivateTarget, target_id)
    if target is None or target.removed_at is not None:
        return None
    target.enabled = True
    return target


def disable_target(db: Session, target_id: int) -> PrivateTarget | None:
    target = db.get(PrivateTarget, target_id)
    if target is None or target.removed_at is not None:
        return None
    target.enabled = False
    return target


def delete_target(db: Session, target_id: int) -> PrivateTarget | None:
    target = db.get(PrivateTarget, target_id)
    if target is None or target.removed_at is not None:
        return None
    target.enabled = False
    target.removed_at = datetime.utcnow()
    return target
