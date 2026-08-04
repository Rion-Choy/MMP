from __future__ import annotations

import json
import re
from datetime import datetime

from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import MailMessage, MailRecipient, PrivateTarget, PublicSession, SyncRun
from app.routes.public import public_session_cookie_name
from app.services.instance_secrets import load_instance_secrets


def _set_test_admin_password(client: TestClient, password: str = "A" * 32) -> str:
    path = client.app.state.instance_secrets_path
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["admin_password_hash"] = PasswordHasher().hash(password)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return password


def _seed_target(client: TestClient, token: str = "22222222-2222-4222-8222-222222222222") -> str:
    db = client.app.state.session_factory()
    db.add(PrivateTarget(email_address="private@example.com", normalized_email="private@example.com", access_token=token))
    db.commit()
    db.close()
    return token


def _login_admin(client: TestClient) -> None:
    _set_test_admin_password(client)
    login_page = client.get("/admin/login")
    csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)
    login = client.post(
        "/admin/login",
        data={"password": "A" * 32, "csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert login.status_code == 303


def _seed_archived_message(
    client: TestClient,
    *,
    immutable_id: str,
    received_at: datetime,
    body: str,
    recipient: str,
    cc: tuple[str, ...] = (),
    folder_name: str = "收件箱",
) -> None:
    db = client.app.state.session_factory()
    message = MailMessage(
        immutable_message_id=immutable_id,
        received_at=received_at,
        body_text=body,
        folder_name=folder_name,
        first_archived_at=received_at,
        last_seen_at=received_at,
    )
    message.recipients.append(MailRecipient(normalized_email=recipient, recipient_type="to"))
    message.recipients.extend(
        MailRecipient(normalized_email=address, recipient_type="cc") for address in cc
    )
    db.add(message)
    db.commit()
    db.close()


def test_admin_login_and_target_creation(client: TestClient) -> None:
    instance = load_instance_secrets(client.app.state.instance_secrets_path)
    from argon2 import PasswordHasher

    # Testing app initializes a real random password hash; retrieve the test-only
    # password by replacing it with a deterministic hash for this HTTP test.
    password = "A" * 32
    instance["admin_password_hash"] = PasswordHasher().hash(password)
    client.app.state.instance_secrets_path.write_text(__import__("json").dumps(instance), encoding="utf-8")

    csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/admin/login").text).group(1)
    login = client.post("/admin/login", data={"password": password, "csrf_token": csrf_token}, follow_redirects=False)
    assert login.status_code == 303
    assert "mail_portal_admin" in login.headers["set-cookie"]

    page = client.get("/admin/targets")
    assert page.status_code == 200

    csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    created = client.post("/admin/targets", data={"email_address": "new@example.com", "csrf_token": csrf_token}, follow_redirects=False)
    assert created.status_code == 303
    db = client.app.state.session_factory()
    target = db.scalar(select(PrivateTarget).where(PrivateTarget.normalized_email == "new@example.com"))
    db.close()
    assert target is not None
    assert re.fullmatch(r"[0-9a-f-]{36}", target.access_token)


def test_targets_page_shows_number_creation_time_and_newest_first(client: TestClient) -> None:
    _set_test_admin_password(client)
    login_page = client.get("/admin/login")
    csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)
    login = client.post(
        "/admin/login",
        data={"password": "A" * 32, "csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert login.status_code == 303

    db = client.app.state.session_factory()
    newer = PrivateTarget(
        email_address="newer@example.com",
        normalized_email="newer@example.com",
        access_token="66666666-6666-4666-8666-666666666666",
        created_at=datetime(2026, 8, 3, 12, 0, 0),
    )
    older = PrivateTarget(
        email_address="older@example.com",
        normalized_email="older@example.com",
        access_token="77777777-7777-4777-8777-777777777777",
        created_at=datetime(2026, 8, 2, 12, 0, 0),
    )
    db.add(newer)
    db.flush()
    db.add(older)
    db.commit()
    db.close()

    page = client.get("/admin/targets")

    assert page.status_code == 200
    assert "编号" in page.text
    assert "创建时间" in page.text
    assert str(newer.id) in page.text
    assert str(older.id) in page.text
    assert "2026-08-03 20:00:00" in page.text
    assert "2026-08-02 20:00:00" in page.text
    assert "2026-08-03 12:00:00" not in page.text
    assert "2026-08-02 12:00:00" not in page.text
    assert page.text.index("newer@example.com") < page.text.index("older@example.com")




def test_all_mail_page_lists_archived_messages_newest_first_with_mail_view_style(client: TestClient) -> None:
    _login_admin(client)
    _seed_archived_message(
        client,
        immutable_id="archive-new",
        received_at=datetime(2026, 8, 3, 12, 0, 0),
        body="new archived body",
        recipient="new@example.com",
        cc=("copy@example.com",),
        folder_name="收件箱",
    )
    _seed_archived_message(
        client,
        immutable_id="archive-old",
        received_at=datetime(2026, 8, 2, 12, 0, 0),
        body="old archived body",
        recipient="old@example.com",
        folder_name="存档",
    )

    page = client.get("/admin/messages")

    assert page.status_code == 200
    assert "全部邮件" in page.text
    assert "new archived body" in page.text
    assert "old archived body" in page.text
    assert page.text.index("new archived body") < page.text.index("old archived body")
    assert "2026-08-03 20:00:00" in page.text
    assert "2026-08-02 20:00:00" in page.text
    assert page.text.count("收件人：new@example.com") == 1
    assert page.text.count("收件人：old@example.com") == 1
    assert "抄送：copy@example.com" in page.text
    assert "mail-card" in page.text
    assert 'class="mail-body"' in page.text
    assert 'class="public-content"' in page.text


def test_all_mail_page_displays_to_recipients_in_header_and_cc_as_context(client: TestClient) -> None:
    _login_admin(client)
    _seed_archived_message(
        client,
        immutable_id="archive-recipient-header",
        received_at=datetime(2026, 8, 3, 13, 0, 0),
        body="recipient header body",
        recipient="to@example.com",
        cc=("cc@example.com",),
    )

    page = client.get("/admin/messages")

    assert page.status_code == 200
    assert 'class="mail-recipient">收件人：to@example.com</span>' in page.text
    assert "抄送：cc@example.com" in page.text


def test_all_mail_page_supports_query_filters_and_pagination(client: TestClient) -> None:
    _login_admin(client)
    for index in range(3):
        _seed_archived_message(
            client,
            immutable_id=f"archive-page-{index}",
            received_at=datetime(2026, 8, 3, 12, index, 0),
            body=f"page body {index}",
            recipient="filter@example.com" if index < 2 else "other@example.com",
            folder_name="收件箱" if index < 2 else "垃圾邮件",
        )

    filtered = client.get(
        "/admin/messages",
        params={"recipient": "filter@example.com", "folder": "收件箱", "page_size": 1},
    )

    assert filtered.status_code == 200
    assert "page body 1" in filtered.text
    assert "page body 0" not in filtered.text
    assert "page body 2" not in filtered.text
    assert "recipient=filter%40example.com" in filtered.text
    assert "folder=%E6%94%B6%E4%BB%B6%E7%AE%B1" in filtered.text
    assert "page_size=1" in filtered.text
    assert "下一页" in filtered.text

    second_page = client.get(
        "/admin/messages",
        params={"recipient": "filter@example.com", "folder": "收件箱", "page_size": 1, "page": 2},
    )
    assert second_page.status_code == 200
    assert "page body 0" in second_page.text
    assert "page body 1" not in second_page.text


def test_all_mail_page_does_not_expose_private_target_access_tokens(client: TestClient) -> None:
    _login_admin(client)
    token = _seed_target(client, "88888888-8888-4888-8888-888888888888")
    _seed_archived_message(
        client,
        immutable_id="archive-private",
        received_at=datetime(2026, 8, 3, 12, 0, 0),
        body="private archive body",
        recipient="private@example.com",
    )

    page = client.get("/admin/messages")

    assert page.status_code == 200
    assert "private archive body" in page.text
    assert token not in page.text


def test_dashboard_shows_latest_sync_time_on_the_sync_card_header(client: TestClient) -> None:
    _set_test_admin_password(client)
    login_page = client.get("/admin/login")
    csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)
    login = client.post(
        "/admin/login",
        data={"password": "A" * 32, "csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert login.status_code == 303

    db = client.app.state.session_factory()
    db.add(
        SyncRun(
            started_at=datetime(2026, 8, 3, 12, 0, 0),
            finished_at=datetime(2026, 8, 3, 12, 1, 23),
            status="success",
        )
    )
    db.commit()
    db.close()

    page = client.get("/admin")

    assert page.status_code == 200
    assert "最近一次同步时间" in page.text
    assert 'href="/admin/messages"' in page.text
    assert "全部邮件" in page.text
    assert "2026-08-03 20:01:23" in page.text
    assert "2026-08-03 12:01:23" not in page.text


def test_admin_pages_hide_tag_ui_but_keep_targets_page(client: TestClient) -> None:
    _set_test_admin_password(client)
    login_page = client.get("/admin/login")
    csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)
    login = client.post(
        "/admin/login",
        data={"password": "A" * 32, "csrf_token": csrf_token},
        follow_redirects=False,
    )
    assert login.status_code == 303

    targets_page = client.get("/admin/targets")
    dashboard_page = client.get("/admin")

    assert targets_page.status_code == 200
    assert dashboard_page.status_code == 200
    assert "/admin/tags" not in targets_page.text
    assert "/admin/tags" not in dashboard_page.text
    assert "标签管理" not in targets_page.text
    assert "标签管理" not in dashboard_page.text
    assert "tag-quick-actions" not in targets_page.text



def test_public_session_cookie_cannot_access_other_target(client: TestClient) -> None:
    first = _seed_target(client)
    second = _seed_target(client, "33333333-3333-4333-8333-333333333333")
    response = client.get(f"/m/{first}")
    assert response.status_code == 200
    cookie = response.cookies.get(public_session_cookie_name(first))
    assert cookie
    other = client.get(
        f"/m/{second}/view?page=1",
        cookies={public_session_cookie_name(first): cookie},
        follow_redirects=False,
    )
    assert other.status_code == 303
    assert other.headers["location"] == f"/m/{second}"
