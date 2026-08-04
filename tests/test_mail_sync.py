from __future__ import annotations

from datetime import datetime, timedelta

from app.models import MailFolder, MailMessage, MailRecipient
from app.services.mail_sync import merge_latest_messages, parse_graph_message


def _message(message_id: str, received: datetime, folder: str) -> dict:
    return {
        "id": message_id,
        "receivedDateTime": received.isoformat() + "Z",
        "body": {"contentType": "text", "content": f"body-{message_id}"},
        "toRecipients": [{"emailAddress": {"address": "private@example.com"}}],
        "ccRecipients": [],
        "_folder_id": folder,
        "_folder_name": folder,
    }


def test_merge_selects_global_latest_twenty_and_deduplicates() -> None:
    base = datetime(2026, 1, 1)
    folders = [
        [_message(f"a-{index}", base + timedelta(minutes=index), "inbox") for index in range(15)],
        [_message(f"b-{index}", base + timedelta(minutes=15 + index), "junk") for index in range(15)],
        [_message("a-14", base + timedelta(minutes=14), "junk")],
    ]

    merged = merge_latest_messages(folders, limit=20)

    assert len(merged) == 20
    assert len({item["id"] for item in merged}) == 20
    assert merged[0]["id"] == "b-14"
    assert merged[-1]["id"] == "a-10"


def test_parse_graph_message_converts_html_to_text_and_recipients() -> None:
    parsed = parse_graph_message(
        {
            "id": "m1",
            "internetMessageId": "<m1@example.com>",
            "receivedDateTime": "2026-01-01T12:00:00Z",
            "body": {"contentType": "html", "content": "<p>Hello <b>world</b></p><script>x()</script>"},
            "toRecipients": [{"emailAddress": {"address": "PRIVATE@example.com"}}],
            "ccRecipients": [{"emailAddress": {"address": "copy@example.com"}}],
        }
    )

    assert parsed.immutable_message_id == "m1"
    assert "Hello world" in parsed.body_text
    assert "script" not in parsed.body_text.lower()
    assert {("private@example.com", "to"), ("copy@example.com", "cc")} == set(parsed.recipients)
