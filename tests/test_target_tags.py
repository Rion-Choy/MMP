from __future__ import annotations

import json
import re

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from app.models import PrivateTarget, TargetTag


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


def _csrf(client: TestClient, path: str = "/admin/targets") -> str:
    page = client.get(path)
    return re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)


def test_targets_page_hides_tag_controls_but_keeps_target_list(client: TestClient) -> None:
    _login(client)
    db = client.app.state.session_factory()
    target = PrivateTarget(
        email_address="tagged@example.com",
        normalized_email="tagged@example.com",
        access_token="88888888-8888-4888-8888-888888888888",
    )
    db.add(target)
    db.commit()
    target_id = target.id
    db.close()

    page = client.get("/admin/targets")

    assert page.status_code == 200
    assert "tagged@example.com" in page.text
    assert "标签管理" not in page.text
    assert "/admin/tags" not in page.text
    assert 'name="tag_id"' not in page.text
    assert f'action="/admin/targets/{target_id}/tag"' not in page.text
    assert "tag-quick-actions" not in page.text


def test_tag_management_page_can_create_rename_and_delete_tags(client: TestClient) -> None:
    _login(client)

    page = client.get("/admin/tags")
    assert page.status_code == 200
    csrf = _csrf(client, "/admin/tags")

    created = client.post(
        "/admin/tags",
        data={"name": "客户", "color": "#2563eb", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert created.status_code == 303

    page = client.get("/admin/tags")
    assert "客户" in page.text
    tag_id = re.search(r'action="/admin/tags/(\d+)"', page.text)
    assert tag_id is not None
    tag_id = int(tag_id.group(1))

    renamed = client.post(
        f"/admin/tags/{tag_id}",
        data={"name": "客户-新", "color": "#059669", "csrf_token": _csrf(client, "/admin/tags")},
        follow_redirects=False,
    )
    assert renamed.status_code == 303
    assert "客户-新" in client.get("/admin/tags").text

    deleted = client.post(
        f"/admin/tags/{tag_id}/delete",
        data={"csrf_token": _csrf(client, "/admin/tags")},
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    assert "客户-新" not in client.get("/admin/tags").text


def test_targets_page_filter_query_still_works_without_tag_controls(client: TestClient) -> None:
    _login(client)
    db = client.app.state.session_factory()
    tag = TargetTag(name="筛选", normalized_name="筛选", color="#2563eb")
    tagged = PrivateTarget(
        email_address="filtered-tagged@example.com",
        normalized_email="filtered-tagged@example.com",
        access_token="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        tag=tag,
    )
    untagged = PrivateTarget(
        email_address="filtered-untagged@example.com",
        normalized_email="filtered-untagged@example.com",
        access_token="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
    )
    db.add(tag)
    db.add_all([tagged, untagged])
    db.commit()
    tag_id = tag.id
    db.close()

    page = client.get(f"/admin/targets?tag_id={tag_id}")

    assert page.status_code == 200
    assert "filtered-tagged@example.com" in page.text
    assert "filtered-untagged@example.com" not in page.text
    assert 'name="tag_id"' not in page.text
    assert "标签筛选" not in page.text


def test_target_can_be_assigned_one_tag_and_filtered(client: TestClient) -> None:
    _login(client)
    db = client.app.state.session_factory()
    tag = TargetTag(name="客户", normalized_name="客户", color="#2563eb")
    tagged = PrivateTarget(
        email_address="tagged@example.com",
        normalized_email="tagged@example.com",
        access_token="99999999-9999-4999-8999-999999999999",
    )
    untagged = PrivateTarget(
        email_address="untagged@example.com",
        normalized_email="untagged@example.com",
        access_token="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    db.add(tag)
    db.add_all([tagged, untagged])
    db.commit()
    tag_id = tag.id
    target_id = tagged.id
    db.close()

    assigned = client.post(
        f"/admin/targets/{target_id}/tag",
        data={"tag_id": str(tag_id), "csrf_token": _csrf(client)},
        follow_redirects=False,
    )
    assert assigned.status_code == 303

    filtered = client.get(f"/admin/targets?tag_id={tag_id}")
    assert "tagged@example.com" in filtered.text
    assert "untagged@example.com" not in filtered.text


def test_targets_page_does_not_render_one_click_tag_actions(client: TestClient) -> None:
    _login(client)
    db = client.app.state.session_factory()
    tag = TargetTag(name="客户", normalized_name="客户", color="#2563eb")
    target = PrivateTarget(
        email_address="quick@example.com",
        normalized_email="quick@example.com",
        access_token="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    )
    db.add(tag)
    db.add(target)
    db.commit()
    target_id = target.id
    db.close()

    page = client.get("/admin/targets")

    assert page.status_code == 200
    assert 'class="tag-quick-actions"' not in page.text
    assert f'action="/admin/targets/{target_id}/tag"' not in page.text
    assert 'class="tag-quick-button' not in page.text
    assert 'placeholder="新标签"' not in page.text
    assert 'class="target-tag-form"' not in page.text


def test_inline_create_tag_assigns_it_to_the_target_in_one_request(client: TestClient) -> None:
    _login(client)
    db = client.app.state.session_factory()
    target = PrivateTarget(
        email_address="inline@example.com",
        normalized_email="inline@example.com",
        access_token="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    )
    db.add(target)
    db.commit()
    target_id = target.id
    db.close()

    response = client.post(
        f"/admin/targets/{target_id}/tag/create",
        data={"name": "新客户", "csrf_token": _csrf(client)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    db = client.app.state.session_factory()
    stored = db.get(PrivateTarget, target_id)
    assert stored is not None
    assert stored.tag is not None
    assert stored.tag.name == "新客户"
    db.close()
