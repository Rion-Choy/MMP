from __future__ import annotations

import json
import re
from pathlib import Path

from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import select

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


def _csrf(client: TestClient) -> str:
    page = client.get("/admin/targets")
    return re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)


def _seed_target(client: TestClient) -> int:
    db = client.app.state.session_factory()
    target = PrivateTarget(
        email_address="notes@example.com",
        normalized_email="notes@example.com",
        access_token="11111111-1111-4111-8111-111111111111",
    )
    db.add(target)
    db.commit()
    target_id = target.id
    db.close()
    return target_id


def test_target_page_renders_compact_note_input_with_five_char_limit(client: TestClient) -> None:
    _login(client)
    target_id = _seed_target(client)

    page = client.get("/admin/targets")

    assert page.status_code == 200
    assert 'name="note"' in page.text
    assert 'maxlength="5"' in page.text
    assert '<input class="input target-note"' in page.text
    assert '<textarea' not in page.text
    assert f'data-note-url="/admin/targets/{target_id}/note"' in page.text
    assert "/admin/tags" not in page.text
    assert "Target tags" not in page.text


def test_admin_can_save_and_clear_target_note(client: TestClient) -> None:
    _login(client)
    target_id = _seed_target(client)
    csrf = _csrf(client)

    saved = client.post(
        f"/admin/targets/{target_id}/note",
        data={"note": "内部备注", "csrf_token": csrf},
    )

    assert saved.status_code == 200
    assert saved.json() == {"status": "ok", "note": "内部备注"}

    db = client.app.state.session_factory()
    try:
        assert db.get(PrivateTarget, target_id).note == "内部备注"
    finally:
        db.close()

    cleared = client.post(
        f"/admin/targets/{target_id}/note",
        data={"note": "   ", "csrf_token": csrf},
    )
    assert cleared.status_code == 200
    assert cleared.json() == {"status": "ok", "note": ""}

    db = client.app.state.session_factory()
    try:
        assert db.get(PrivateTarget, target_id).note is None
    finally:
        db.close()


def test_admin_rejects_note_longer_than_five_characters_without_overwriting(client: TestClient) -> None:
    _login(client)
    target_id = _seed_target(client)
    csrf = _csrf(client)
    client.post(
        f"/admin/targets/{target_id}/note",
        data={"note": "原备注", "csrf_token": csrf},
    )

    rejected = client.post(
        f"/admin/targets/{target_id}/note",
        data={"note": "x" * 6, "csrf_token": csrf},
    )

    assert rejected.status_code == 400
    assert rejected.json()["status"] == "error"
    db = client.app.state.session_factory()
    try:
        assert db.get(PrivateTarget, target_id).note == "原备注"
    finally:
        db.close()


def test_note_mutation_requires_csrf(client: TestClient) -> None:
    _login(client)
    target_id = _seed_target(client)

    rejected = client.post(
        f"/admin/targets/{target_id}/note",
        data={"note": "未授权", "csrf_token": "bad"},
    )

    assert rejected.status_code == 403


def test_note_is_not_rendered_on_public_mail_page(client: TestClient) -> None:
    target_id = _seed_target(client)
    db = client.app.state.session_factory()
    db.get(PrivateTarget, target_id).note = "只有后台可见"
    db.commit()
    token = db.get(PrivateTarget, target_id).access_token
    db.close()

    response = client.get(f"/m/{token}")

    assert response.status_code == 200
    assert "只有后台可见" not in response.text


def test_old_tag_management_url_redirects_to_targets(client: TestClient) -> None:
    _login(client)

    response = client.get("/admin/tags", follow_redirects=False)

    assert response.status_code == 301
    assert response.headers["location"] == "/admin/targets"


def test_note_frontend_saves_on_blur_without_full_reload() -> None:
    template = Path("app/templates/admin/targets.html").read_text(encoding="utf-8")
    script = Path("app/static/admin-targets.js").read_text(encoding="utf-8")

    assert "blur" in script
    assert "fetch(" in script
    assert "window.location.reload" not in script
    assert "maxlength=\"5\"" in template
    assert "resize: none" in Path("app/static/style.css").read_text(encoding="utf-8")
