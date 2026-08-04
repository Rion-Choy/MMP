from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from app.models import PrivateTarget


def _set_test_admin_password(client: TestClient, password: str = "A" * 32) -> str:
    path = client.app.state.instance_secrets_path
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["admin_password_hash"] = PasswordHasher().hash(password)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return password


def _login(client: TestClient) -> None:
    password = _set_test_admin_password(client)
    page = client.get("/admin/login")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    response = client.post(
        "/admin/login",
        data={"password": password, "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303


def _seed_target(client: TestClient, token: str) -> None:
    db = client.app.state.session_factory()
    db.add(
        PrivateTarget(
            email_address=f"{token[:8]}@example.com",
            normalized_email=f"{token[:8]}@example.com",
            access_token=token,
        )
    )
    db.commit()
    db.close()


def _captcha_answer(client: TestClient, token: str) -> str:
    import re

    svg = client.get(f"/m/{token}/captcha.svg").text
    return re.search(r">([A-Za-z0-9]{4})</text>", svg).group(1)


def test_admin_session_default_is_fixed_24_hours() -> None:
    from app.config import ADMIN_SESSION_TTL_SECONDS

    assert ADMIN_SESSION_TTL_SECONDS == 86400


def test_admin_cookie_uses_24_hour_max_age(client: TestClient) -> None:
    _set_test_admin_password(client)
    page = client.get("/admin/login")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)

    response = client.post(
        "/admin/login",
        data={"password": "A" * 32, "csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "Max-Age=86400" in response.headers["set-cookie"]


def test_admin_cookie_and_public_session_coexist(client: TestClient) -> None:
    _login(client)
    admin_cookie = client.cookies.get("mail_portal_admin")
    token = "11111111-1111-4111-8111-111111111111"
    _seed_target(client, token)

    entry = client.get(f"/m/{token}")

    assert entry.status_code == 200
    assert client.cookies.get("mail_portal_admin") == admin_cookie
    cookie_names = {cookie.name for cookie in client.cookies.jar}
    assert "mail_portal_admin" in cookie_names
    assert any(name.startswith("mail_portal_session_") for name in cookie_names)

    assert client.get("/admin/targets").status_code == 200


def test_two_public_mailbox_sessions_do_not_overwrite_each_other(client: TestClient) -> None:
    _login(client)
    token_a = "11111111-1111-4111-8111-111111111111"
    token_b = "22222222-2222-4222-8222-222222222222"
    _seed_target(client, token_a)
    _seed_target(client, token_b)

    assert client.get(f"/m/{token_a}").status_code == 200
    answer_a = _captcha_answer(client, token_a)
    assert client.post(f"/m/{token_a}/verify", data={"answer": answer_a}, follow_redirects=False).status_code == 303

    assert client.get(f"/m/{token_b}").status_code == 200
    answer_b = _captcha_answer(client, token_b)
    assert client.post(f"/m/{token_b}/verify", data={"answer": answer_b}, follow_redirects=False).status_code == 303

    refreshed_a = client.post(f"/m/{token_a}/refresh?page=1")
    refreshed_b = client.post(f"/m/{token_b}/refresh?page=1")

    assert refreshed_a.json()["status"] == "ok"
    assert refreshed_b.json()["status"] == "ok"
    names = [cookie.name for cookie in client.cookies.jar]
    assert len([name for name in names if name.startswith("mail_portal_session_")]) == 2


def test_admin_verification_does_not_slide_or_issue_a_new_cookie(client: TestClient) -> None:
    _login(client)
    admin_cookie = client.cookies.get("mail_portal_admin")

    response = client.get("/admin")

    assert response.status_code == 200
    assert "mail_portal_admin=" not in response.headers.get("set-cookie", "")
    assert client.cookies.get("mail_portal_admin") == admin_cookie
