from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def format_beijing_time(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["beijing_time"] = format_beijing_time
