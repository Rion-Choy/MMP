from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import PrivateTarget

MAX_NOTE_LENGTH = 5


def normalize_note(note: str | None) -> str | None:
    value = str(note or "").strip()
    if len(value) > MAX_NOTE_LENGTH:
        raise ValueError("备注不能超过 5 个字符")
    return value or None


def update_target_note(db: Session, target_id: int, note: str | None) -> PrivateTarget | None:
    target = db.get(PrivateTarget, target_id)
    if target is None or target.removed_at is not None:
        return None
    target.note = normalize_note(note)
    db.flush()
    return target
