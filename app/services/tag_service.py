from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PrivateTarget, TargetTag

_TAG_NAME_RE = re.compile(r"^.{1,64}$", re.DOTALL)
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def normalize_tag_name(name: str) -> str:
    value = " ".join(str(name or "").strip().split())
    if not _TAG_NAME_RE.fullmatch(value):
        raise ValueError("标签名称不能为空且不能超过 64 个字符")
    return value.casefold()


def normalize_tag_color(color: str | None) -> str | None:
    value = str(color or "").strip()
    if not value:
        return None
    if not _COLOR_RE.fullmatch(value):
        raise ValueError("标签颜色必须是六位十六进制颜色")
    return value.lower()


def create_tag(db: Session, name: str, color: str | None = None) -> TargetTag:
    display_name = " ".join(str(name or "").strip().split())
    normalized = normalize_tag_name(display_name)
    normalized_color = normalize_tag_color(color)
    if db.scalar(select(TargetTag).where(TargetTag.normalized_name == normalized)) is not None:
        raise ValueError("标签名称已存在")
    tag = TargetTag(name=display_name, normalized_name=normalized, color=normalized_color)
    db.add(tag)
    db.flush()
    return tag


def rename_tag(db: Session, tag_id: int, name: str, color: str | None = None) -> TargetTag | None:
    tag = db.get(TargetTag, tag_id)
    if tag is None:
        return None
    display_name = " ".join(str(name or "").strip().split())
    normalized = normalize_tag_name(display_name)
    duplicate = db.scalar(
        select(TargetTag).where(TargetTag.normalized_name == normalized, TargetTag.id != tag_id)
    )
    if duplicate is not None:
        raise ValueError("标签名称已存在")
    tag.name = display_name
    tag.normalized_name = normalized
    tag.color = normalize_tag_color(color)
    db.flush()
    return tag


def delete_tag(db: Session, tag_id: int) -> TargetTag | None:
    tag = db.get(TargetTag, tag_id)
    if tag is None:
        return None
    for target in list(tag.targets):
        target.tag_id = None
    db.delete(tag)
    db.flush()
    return tag


def assign_tag(db: Session, target_id: int, tag_id: int | None) -> PrivateTarget | None:
    target = db.get(PrivateTarget, target_id)
    if target is None or target.removed_at is not None:
        return None
    if tag_id is not None and db.get(TargetTag, tag_id) is None:
        raise ValueError("标签不存在")
    target.tag_id = tag_id
    db.flush()
    return target
