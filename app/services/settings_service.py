from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppSetting

DEFAULT_SYNC_INTERVAL_SECONDS = 30
MIN_SYNC_INTERVAL_SECONDS = 10
MAX_SYNC_INTERVAL_SECONDS = 86400
DEFAULT_SYNC_ENABLED = True
DEFAULT_CAPTCHA_ENABLED = True


def get_setting(db: Session, key: str, default: str | None = None) -> str | None:
    setting = db.get(AppSetting, key)
    return setting.setting_value if setting is not None else default


def set_setting(db: Session, key: str, value: str) -> AppSetting:
    setting = db.get(AppSetting, key)
    if setting is None:
        setting = AppSetting(setting_key=key, setting_value=value, updated_at=datetime.utcnow())
        db.add(setting)
    else:
        setting.setting_value = value
        setting.updated_at = datetime.utcnow()
    db.flush()
    return setting


def get_sync_interval(db: Session) -> int:
    raw = get_setting(db, "sync_interval_seconds", str(DEFAULT_SYNC_INTERVAL_SECONDS))
    try:
        return int(raw or DEFAULT_SYNC_INTERVAL_SECONDS)
    except ValueError:
        return DEFAULT_SYNC_INTERVAL_SECONDS


def set_sync_interval(db: Session, seconds: int) -> int:
    if not MIN_SYNC_INTERVAL_SECONDS <= seconds <= MAX_SYNC_INTERVAL_SECONDS:
        raise ValueError(f"sync interval must be between {MIN_SYNC_INTERVAL_SECONDS} and {MAX_SYNC_INTERVAL_SECONDS} seconds")
    set_setting(db, "sync_interval_seconds", str(seconds))
    return seconds


def get_sync_enabled(db: Session) -> bool:
    raw = get_setting(db, "sync_enabled", "1")
    return str(raw or "1").strip().casefold() not in {"0", "false", "no", "off", "disabled"}


def set_sync_enabled(db: Session, enabled: bool) -> bool:
    set_setting(db, "sync_enabled", "1" if enabled else "0")
    return enabled


def get_captcha_enabled(db: Session) -> bool:
    raw = get_setting(db, "captcha_enabled", "1")
    return str(raw or "1").strip().casefold() not in {"0", "false", "no", "off", "disabled"}


def set_captcha_enabled(db: Session, enabled: bool) -> bool:
    set_setting(db, "captcha_enabled", "1" if enabled else "0")
    return enabled


def get_enabled_folder_names(db: Session) -> list[str]:
    raw = get_setting(db, "sync_folder_names", "Inbox,Junk Email,Archive") or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


def set_enabled_folder_names(db: Session, folder_names: list[str]) -> None:
    cleaned = [name.strip() for name in folder_names if name.strip()]
    set_setting(db, "sync_folder_names", ",".join(cleaned))
