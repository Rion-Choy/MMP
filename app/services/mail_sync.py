from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
from urllib.parse import quote
from email.parser import BytesParser
from email.policy import default
from typing import Any, Mapping

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import MailFolder, MailMessage, MailRecipient, MotherMailbox, SyncRun
from app.services.email_normalization import parse_recipient_list


SYNC_BATCH_SIZE = 50


@dataclass(frozen=True)
class ParsedMessage:
    immutable_message_id: str
    internet_message_id: str | None
    received_at: datetime
    body_text: str
    recipients: tuple[tuple[str, str], ...]
    folder_id: str | None = None
    folder_name: str | None = None
    mother_mailbox_id: int | None = None


def parse_received_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _format_graph_datetime(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat(timespec="milliseconds") + "Z"


def html_to_text(value: str) -> str:
    value = re.sub(r"(?is)<(script|style|iframe|object|form)\b.*?</\1\s*>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(p|div|li|tr|h[1-6])\s*>", "\n", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    return html.unescape(re.sub(r"[ \t]+", " ", value)).strip()


def parse_graph_message(payload: Mapping[str, Any]) -> ParsedMessage:
    message_id = payload.get("id")
    received = payload.get("receivedDateTime")
    if not isinstance(message_id, str) or not message_id:
        raise ValueError("message has no immutable id")
    if not isinstance(received, str):
        raise ValueError("message has no receivedDateTime")
    body = payload.get("body") or {}
    content = body.get("content", "") if isinstance(body, Mapping) else ""
    content_type = body.get("contentType", "text") if isinstance(body, Mapping) else "text"
    if not isinstance(content, str):
        content = ""
    body_text = html_to_text(content) if str(content_type).casefold() == "html" else content
    recipients: list[tuple[str, str]] = []
    for recipient_type, field in (("to", "toRecipients"), ("cc", "ccRecipients")):
        for address in parse_recipient_list(payload.get(field)):
            recipients.append((address, recipient_type))
    return ParsedMessage(
        immutable_message_id=message_id,
        internet_message_id=payload.get("internetMessageId") if isinstance(payload.get("internetMessageId"), str) else None,
        received_at=parse_received_datetime(received),
        body_text=body_text,
        recipients=tuple(dict.fromkeys(recipients)),
        folder_id=payload.get("_folder_id") if isinstance(payload.get("_folder_id"), str) else None,
        folder_name=payload.get("_folder_name") if isinstance(payload.get("_folder_name"), str) else None,
        mother_mailbox_id=payload.get("_mother_mailbox_id") if isinstance(payload.get("_mother_mailbox_id"), int) else None,
    )


def _received(value: Mapping[str, Any]) -> datetime:
    received = value.get("receivedDateTime")
    if not isinstance(received, str):
        return datetime.min
    try:
        return parse_received_datetime(received)
    except ValueError:
        return datetime.min


def _message_key(value: Mapping[str, Any]) -> tuple[datetime, str]:
    return _received(value), str(value.get("id") or "")


def merge_latest_messages(folder_values: list[list[Mapping[str, Any]]], *, limit: int = 20) -> list[dict[str, Any]]:
    """Compatibility helper for the former newest-window behavior."""
    merged: dict[str, Mapping[str, Any]] = {}
    for values in folder_values:
        for value in values:
            message_id = value.get("id")
            if isinstance(message_id, str) and message_id:
                current = merged.get(message_id)
                if current is None or _received(value) > _received(current):
                    merged[message_id] = value
    return [dict(value) for value in sorted(merged.values(), key=_received, reverse=True)[:limit]]


def merge_pending_messages(folder_values: list[list[Mapping[str, Any]]], *, limit: int = SYNC_BATCH_SIZE) -> list[dict[str, Any]]:
    """Deduplicate pending messages and return the oldest batch first."""
    merged: dict[str, Mapping[str, Any]] = {}
    for values in folder_values:
        for value in values:
            message_id = value.get("id")
            if not isinstance(message_id, str) or not message_id:
                continue
            current = merged.get(message_id)
            if current is None or _message_key(value) < _message_key(current):
                merged[message_id] = value
    return [dict(value) for value in sorted(merged.values(), key=_message_key)[:limit]]


_FOLDER_ALIASES: dict[str, set[str]] = {
    "inbox": {"inbox", "收件箱"},
    "junk email": {"junk email", "junk", "spam", "垃圾邮件", "垃圾箱"},
    "archive": {"archive", "存档", "归档"},
    "sent items": {"sent items", "sent", "已发送", "发件箱"},
    "deleted items": {"deleted items", "deleted", "trash", "已删除", "回收站"},
    "drafts": {"drafts", "草稿"},
}


def _folder_names_match(configured_name: str, display_name: str) -> bool:
    configured = configured_name.strip().casefold()
    display = display_name.strip().casefold()
    if not configured or not display:
        return False
    if configured == display:
        return True
    for aliases in _FOLDER_ALIASES.values():
        if configured in aliases and display in aliases:
            return True
    return False


def _initial_folder_cursor(
    db: Session,
    folder_name: str,
    mother_mailbox_id: int | None = None,
) -> tuple[datetime, str]:
    aliases = next(
        (aliases for aliases in _FOLDER_ALIASES.values() if folder_name.strip().casefold() in aliases),
        {folder_name.strip().casefold()},
    )
    message_query = (
        select(MailMessage)
        .where(func.lower(MailMessage.folder_name).in_(aliases))
        .order_by(MailMessage.received_at.desc(), MailMessage.id.desc())
        .limit(1)
    )
    if mother_mailbox_id is not None:
        message_query = message_query.where(MailMessage.mother_mailbox_id == mother_mailbox_id)
    message = db.scalar(message_query)
    if message is not None:
        return message.received_at, message.immutable_message_id

    if mother_mailbox_id is not None:
        latest = db.scalar(
            select(func.max(MailMessage.received_at)).where(
                MailMessage.mother_mailbox_id == mother_mailbox_id
            )
        )
    else:
        latest = db.scalar(select(func.max(MailMessage.received_at)))
    # A newly selected folder must not cause an unexpected historical backfill.
    # A mailbox with no local archive starts at the minimum cursor so its first
    # synchronization can archive current provider mail.
    return latest or datetime.min, ""


def _get_or_create_folder(
    db: Session,
    provider_folder: Mapping[str, Any],
    mother_mailbox_id: int | None = None,
) -> MailFolder:
    provider_folder_id = provider_folder.get("id")
    if not isinstance(provider_folder_id, str) or not provider_folder_id:
        raise ValueError("mail folder has no provider id")
    display_name = str(provider_folder.get("displayName") or provider_folder_id)
    now = datetime.utcnow()
    folder_query = select(MailFolder).where(MailFolder.provider_folder_id == provider_folder_id)
    if mother_mailbox_id is not None:
        folder_query = folder_query.where(MailFolder.mother_mailbox_id == mother_mailbox_id)
    folder = db.scalar(folder_query)
    if folder is None:
        cursor_at, cursor_id = _initial_folder_cursor(db, display_name, mother_mailbox_id)
        folder = MailFolder(
            mother_mailbox_id=mother_mailbox_id,
            provider_folder_id=provider_folder_id,
            folder_name=display_name,
            parent_folder_id=None,
            is_enabled=True,
            last_seen_at=now,
            last_message_received_at=cursor_at,
            last_message_id=cursor_id,
        )
        db.add(folder)
        db.flush()
    else:
        folder.folder_name = display_name
        folder.is_enabled = True
        folder.last_seen_at = now
        if folder.last_message_received_at is None:
            folder.last_message_received_at, folder.last_message_id = _initial_folder_cursor(
                db, display_name, mother_mailbox_id
            )
    return folder


def _pending_message_path(provider_folder_id: str, cursor_at: datetime, limit: int) -> str:
    timestamp = _format_graph_datetime(cursor_at)
    # Use an inclusive time boundary and filter the exact cursor key locally so
    # messages sharing a timestamp are not lost between rounds.
    # Graph's receivedDateTime is Edm.DateTimeOffset, so the RHS must be an
    # unquoted ISO-8601 datetime literal rather than an OData string literal.
    filter_expression = f"receivedDateTime ge {timestamp}"
    encoded_filter = quote(filter_expression, safe="'()")
    return (
        f"/me/mailFolders/{provider_folder_id}/messages"
        f"?$top={limit}&$orderby=receivedDateTime%20asc&$filter={encoded_filter}"
    )


def _is_after_cursor(value: Mapping[str, Any], folder: MailFolder) -> bool:
    if folder.last_message_received_at is None:
        return True
    return _message_key(value) > (
        folder.last_message_received_at,
        str(folder.last_message_id or ""),
    )


def upsert_parsed_message(
    db: Session,
    parsed: ParsedMessage,
    *,
    local_folder_id: int | None = None,
) -> tuple[MailMessage, bool]:
    now = datetime.utcnow()
    message = db.scalar(
        select(MailMessage).where(
            MailMessage.immutable_message_id == parsed.immutable_message_id,
            MailMessage.mother_mailbox_id == parsed.mother_mailbox_id,
        )
    )
    inserted = message is None
    if message is None:
        message = MailMessage(
            mother_mailbox_id=parsed.mother_mailbox_id,
            immutable_message_id=parsed.immutable_message_id,
            first_archived_at=now,
            last_seen_at=now,
            received_at=parsed.received_at,
            body_text=parsed.body_text,
        )
        db.add(message)
        db.flush()
    message.internet_message_id = parsed.internet_message_id
    message.received_at = parsed.received_at
    message.body_text = parsed.body_text
    message.last_seen_at = now
    message.body_fetch_error = None
    message.folder_name = parsed.folder_name
    if local_folder_id is not None:
        message.folder_id = local_folder_id
    message.recipients.clear()
    db.flush()
    for address, recipient_type in parsed.recipients:
        message.recipients.append(MailRecipient(normalized_email=address, recipient_type=recipient_type))
    return message, inserted


def _select_provider_folders(
    folders: list[Mapping[str, Any]], folder_names: list[str] | None,
) -> list[Mapping[str, Any]]:
    if not folder_names:
        return folders
    return [
        folder
        for folder in folders
        if any(
            _folder_names_match(str(configured), str(folder.get("displayName") or ""))
            for configured in folder_names
        )
    ]


def sync_once(
    db: Session,
    graph: Any,
    *,
    folder_names: list[str] | None = None,
    limit: int = SYNC_BATCH_SIZE,
    mother_mailbox_id: int | None = None,
    cycle_id: int | None = None,
):
    """Fetch the oldest pending messages after each folder's cursor."""
    if mother_mailbox_id is not None:
        mailbox = db.get(MotherMailbox, mother_mailbox_id)
        if mailbox is None:
            raise ValueError("mother mailbox does not exist")
    started = datetime.utcnow()
    run = SyncRun(
        started_at=started,
        status="running",
        mother_mailbox_id=mother_mailbox_id,
        cycle_id=cycle_id,
        cycle_started_at=started if cycle_id is not None else None,
    )
    db.add(run)
    db.flush()
    try:
        folders = list(graph.iter_collection("/me/mailFolders"))
        selected = _select_provider_folders(folders, folder_names)
        folder_entries: list[tuple[MailFolder, list[dict[str, Any]]]] = []
        provider_folders: dict[str, MailFolder] = {}
        for provider_folder in selected:
            folder = _get_or_create_folder(db, provider_folder, mother_mailbox_id)
            provider_folders[folder.provider_folder_id] = folder
            candidates: list[dict[str, Any]] = []
            for value in islice(
                graph.iter_collection(
                    _pending_message_path(folder.provider_folder_id, folder.last_message_received_at, limit)
                ),
                limit,
            ):
                candidate = dict(value)
                candidate["_folder_id"] = folder.provider_folder_id
                candidate["_folder_name"] = folder.folder_name
                candidate["_mother_mailbox_id"] = mother_mailbox_id
                if _is_after_cursor(candidate, folder):
                    candidates.append(candidate)
            folder_entries.append((folder, candidates))

        run.fetched_count = sum(len(values) for _, values in folder_entries)
        merged = merge_pending_messages([values for _, values in folder_entries], limit=limit)
        run.unique_count = len(merged)
        processed_ids = {str(value.get("id")) for value in merged}
        for raw in merged:
            parsed = parse_graph_message(raw)
            parsed = ParsedMessage(
                immutable_message_id=parsed.immutable_message_id,
                internet_message_id=parsed.internet_message_id,
                received_at=parsed.received_at,
                body_text=parsed.body_text,
                recipients=parsed.recipients,
                folder_id=parsed.folder_id,
                folder_name=parsed.folder_name,
                mother_mailbox_id=mother_mailbox_id,
            )
            folder = provider_folders.get(parsed.folder_id or "")
            _, inserted = upsert_parsed_message(
                db,
                parsed,
                local_folder_id=folder.id if folder is not None else None,
            )
            if inserted:
                run.inserted_count += 1
            else:
                run.updated_count += 1
                run.duplicate_count += 1

        for folder, candidates in folder_entries:
            processed = [candidate for candidate in candidates if str(candidate.get("id")) in processed_ids]
            if not processed:
                continue
            newest = max(processed, key=_message_key)
            folder.last_message_received_at = _received(newest)
            folder.last_message_id = str(newest.get("id") or "")
            folder.last_seen_at = datetime.utcnow()

        run.status = "success"
        run.finished_at = datetime.utcnow()
        db.commit()
        return run
    except Exception as exc:
        db.rollback()
        failed = db.get(SyncRun, run.id)
        if failed is not None:
            failed.status = "failed"
            failed.finished_at = datetime.utcnow()
            failed.error_message = str(exc)[:2000]
            db.commit()
        raise
