from __future__ import annotations

import json
import re

from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import MailMessage, MailRecipient, PrivateTarget


def _set_test_admin_password(client: TestClient, password: str = "A" * 32) -> str:
    path = client.app.state.instance_secrets_path
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["admin_password_hash"] = PasswordHasher().hash(password)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return password


def _login(client: TestClient) -> None:
    password = _set_test_admin_password(client)
    login_page = client.get("/admin/login")
    csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)
    response = client.post("/admin/login", data={"password": password, "csrf_token": csrf_token}, follow_redirects=False)
    assert response.status_code == 303


def test_admin_mutation_requires_csrf_token(client: TestClient) -> None:
    _login(client)
    page = client.get("/admin/targets")
    assert page.status_code == 200
    token_match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert token_match
    csrf_token = token_match.group(1)
    assert client.cookies.get("mail_portal_admin_csrf") == csrf_token

    rejected = client.post("/admin/targets", data={"email_address": "x@example.com", "csrf_token": "bad"}, follow_redirects=False)
    assert rejected.status_code == 403

    created = client.post(
        "/admin/targets",
        data={"email_address": "x@example.com", "csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert created.status_code == 303


def test_admin_can_save_sync_settings_separately(client: TestClient) -> None:
    _login(client)
    page = client.get("/admin/mailbox")
    csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)

    response = client.post(
        "/admin/mailbox/sync-settings",
        data={
            "csrf_token": csrf_token,
            "sync_interval_seconds": "45",
            "folder_names": "Inbox,Junk Email",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    db = client.app.state.session_factory()
    try:
        from app.services.settings_service import get_enabled_folder_names, get_sync_interval

        assert get_sync_interval(db) == 45
        assert get_enabled_folder_names(db) == ["Inbox", "Junk Email"]
    finally:
        db.close()


def test_admin_rejects_sync_interval_below_ten_seconds(client: TestClient) -> None:
    _login(client)
    page = client.get("/admin/mailbox")
    csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)

    response = client.post(
        "/admin/mailbox/sync-settings",
        data={
            "csrf_token": csrf_token,
            "sync_interval_seconds": "9",
            "folder_names": "Inbox",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/mailbox?sync_error=invalid"

    db = client.app.state.session_factory()
    try:
        from app.services.settings_service import get_sync_interval

        assert get_sync_interval(db) == 30
    finally:
        db.close()


def test_admin_can_enable_and_disable_scheduled_sync(client: TestClient) -> None:
    _login(client)
    page = client.get("/admin/mailbox")
    csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)

    disabled = client.post("/admin/mailbox/sync-toggle", data={"csrf_token": csrf_token}, follow_redirects=False)
    assert disabled.status_code == 303

    db = client.app.state.session_factory()
    try:
        from app.services.settings_service import get_sync_enabled

        assert get_sync_enabled(db) is False
    finally:
        db.close()

    enabled = client.post("/admin/mailbox/sync-toggle", data={"csrf_token": csrf_token}, follow_redirects=False)
    assert enabled.status_code == 303

    db = client.app.state.session_factory()
    try:
        assert get_sync_enabled(db) is True
    finally:
        db.close()


def test_verified_public_session_can_view_matching_body(client: TestClient) -> None:
    token = "44444444-4444-4444-8444-444444444444"
    db = client.app.state.session_factory()
    target = PrivateTarget(
        email_address="private@example.com",
        normalized_email="private@example.com",
        access_token=token,
    )
    db.add(target)
    db.flush()
    message = MailMessage(
        immutable_message_id="message-1",
        received_at=__import__("datetime").datetime.utcnow(),
        body_text="matching body",
        first_archived_at=__import__("datetime").datetime.utcnow(),
        last_seen_at=__import__("datetime").datetime.utcnow(),
    )
    message.recipients.append(MailRecipient(normalized_email="private@example.com", recipient_type="to"))
    db.add(message)
    db.commit()
    db.close()

    first = client.get(f"/m/{token}")
    assert first.status_code == 200
    answer = re.search(r">([A-Za-z0-9]{4})</text>", client.get(f"/m/{token}/captcha.svg").text).group(1)
    verified = client.post(f"/m/{token}/verify", data={"answer": answer}, follow_redirects=False)
    assert verified.status_code == 303
    viewed = client.get(f"/m/{token}/view?page=1")
    assert viewed.status_code == 200
    assert "匹配邮箱" in viewed.text
    assert "private@example.com" in viewed.text
    assert "matching body" in viewed.text
