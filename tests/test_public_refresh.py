from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from fastapi.testclient import TestClient

from app.models import MailMessage, MailRecipient, PrivateTarget, PublicSession
from app.services.instance_secrets import secret_mac, session_id_hash


TOKEN = "55555555-5555-4555-8555-555555555555"


def _seed_target(client: TestClient) -> None:
    db = client.app.state.session_factory()
    target = PrivateTarget(
        email_address="private@example.com",
        normalized_email="private@example.com",
        access_token=TOKEN,
    )
    db.add(target)
    db.flush()
    now = datetime.utcnow()
    message = MailMessage(
        immutable_message_id="refresh-message-1",
        received_at=now,
        body_text="current body",
        first_archived_at=now,
        last_seen_at=now,
    )
    message.recipients.append(
        MailRecipient(normalized_email="private@example.com", recipient_type="to")
    )
    db.add(message)
    db.commit()
    db.close()


def _set_verified_session(client: TestClient, *, expired: bool = False) -> str:
    db = client.app.state.session_factory()
    target = db.query(PrivateTarget).filter_by(access_token=TOKEN).one()
    now = datetime.utcnow()
    raw_id = "refresh-session-id"
    db.add(
        PublicSession(
            session_id_hash=session_id_hash(raw_id),
            target_id=target.id,
            captcha_answer_mac=secret_mac("captcha-secret", "Ab3d"),
            captcha_payload="",
            captcha_expires_at=now + timedelta(minutes=5),
            verified_at=now,
            expires_at=now - timedelta(seconds=1) if expired else now + timedelta(minutes=30),
            created_at=now,
            last_seen_at=now,
        )
    )
    db.commit()
    db.close()
    client.cookies.set("mail_portal_session", raw_id)
    return raw_id


def test_refresh_returns_database_mail_fragment_without_running_mailbox_sync(client: TestClient) -> None:
    _seed_target(client)
    _set_verified_session(client)

    response = client.post(f"/m/{TOKEN}/refresh?page=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["source"] == "database"
    assert "current body" in payload["html"]
    assert "<!doctype html>" not in payload["html"].lower()


def test_refresh_recreates_captcha_when_public_session_is_expired(client: TestClient) -> None:
    _seed_target(client)
    _set_verified_session(client, expired=True)

    response = client.post(f"/m/{TOKEN}/refresh?page=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "captcha_required"
    assert "验证码" in payload["html"]
    assert "captcha.svg" in payload["html"]
    cookie_values = [cookie.value for cookie in client.cookies.jar if cookie.name == "mail_portal_session"]
    assert len(set(cookie_values)) >= 2


def test_public_messages_template_declares_ajax_refresh_contract() -> None:
    template = Path("app/templates/public/messages.html").read_text(encoding="utf-8")
    script = Path("app/static/public-messages.js").read_text(encoding="utf-8")

    assert "刷新" in template
    assert "refresh-messages" in template
    assert "/static/public-messages.js" in template
    assert "fetch(" in script
    assert "data-refresh-url" in template
    assert "window.location.reload" not in script
    assert "读取中" in script
    assert "同步" not in script


def test_refresh_route_does_not_import_or_call_mailbox_sync_components() -> None:
    route = Path("app/routes/public.py").read_text(encoding="utf-8")

    assert "SyncWorker" not in route
    assert "build_graph_client" not in route
    assert "run_manual_sync" not in route


def test_public_refresh_button_is_allowed_by_deployed_content_security_policy() -> None:
    caddy = Path("deploy/caddy-mail-portal.caddy").read_text(encoding="utf-8")
    route = Path("deploy/caddy-mail-portal-route.json").read_text(encoding="utf-8")

    assert "script-src 'self'" in caddy
    assert "script-src 'self'" in route
    assert "script-src 'none'" not in caddy
    assert "script-src 'none'" not in route
