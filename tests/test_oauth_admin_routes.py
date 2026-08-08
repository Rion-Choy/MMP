from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import MotherMailbox, OAuthTransaction
from app.services.instance_secrets import load_instance_secrets
from app.services.microsoft_oauth import DeviceAuthorizationPending, save_oauth_config, validate_access_token_for_mailbox


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
    page = client.get("/admin/mailbox")
    return re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)


def _common_form(csrf: str) -> dict[str, str]:
    return {
        "csrf_token": csrf,
        "mailbox_address": "mother@example.com",
        "client_id": "client-id",
        "authority": "consumers",
    }


def test_access_token_validation_confirms_the_microsoft_account() -> None:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1.0/me")
        assert request.headers["authorization"] == "Bearer access-token"
        return httpx.Response(200, json={"mail": "mother@example.com"})

    result = validate_access_token_for_mailbox(
        access_token="access-token",
        mailbox_address="Mother@example.com",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert result == {"mailbox_address": "mother@example.com", "account_address": "mother@example.com"}


def test_web_oauth_callback_saves_internal_refresh_config_and_consumes_transaction(client: TestClient, monkeypatch) -> None:
    _login(client)
    monkeypatch.setattr(
        "app.routes.admin.build_authorization_url",
        lambda **kwargs: "https://login.example/authorize?state=" + kwargs["state"],
    )
    start = client.post(
        "/admin/oauth/web/start",
        data=_common_form(_csrf(client)),
        follow_redirects=False,
    )
    assert start.status_code == 200
    assert "https://login.example/authorize?state=" in start.text
    assert "location" not in start.headers
    state = client.cookies.get("mail_portal_oauth_state")
    assert state

    monkeypatch.setattr(
        "app.routes.admin.exchange_authorization_code",
        lambda **kwargs: {
            "access_token": "access-token",
            "refresh_token": "web-refresh-token",
            "expires_in": 3600,
        },
    )
    monkeypatch.setattr(
        "app.routes.admin.validate_access_token_for_mailbox",
        lambda **kwargs: {
            "mailbox_address": "mother@example.com",
            "account_address": "mother@example.com",
        },
    )

    callback = client.get(
        f"/admin/oauth/callback?code=authorization-code&state={state}",
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == "/admin/mailbox"

    config = json.loads(client.app.state.microsoft_oauth_path.read_text(encoding="utf-8"))
    assert config["mailbox_address"] == "mother@example.com"
    assert config["refresh_token"] == "web-refresh-token"
    assert config["auth_method"] == "web"

    db = client.app.state.session_factory()
    try:
        transaction = db.scalar(select(OAuthTransaction).order_by(OAuthTransaction.id.desc()))
        assert transaction is not None
        assert transaction.flow_type == "web"
        assert transaction.used_at is not None
    finally:
        db.close()


def test_web_start_renders_copyable_authorization_url_without_redirecting(client: TestClient, monkeypatch) -> None:
    _login(client)
    monkeypatch.setattr(
        "app.routes.admin.build_authorization_url",
        lambda **kwargs: "https://login.example/authorize?state=" + kwargs["state"],
    )

    response = client.post("/admin/oauth/web/start", data=_common_form(_csrf(client)), follow_redirects=False)

    assert response.status_code == 200
    assert 'id="authorization_url"' in response.text
    assert "https://login.example/authorize?state=" in response.text
    assert "location" not in response.headers


def test_admin_session_cookie_allows_cross_site_oauth_callback(client: TestClient) -> None:
    _set_test_admin_password(client)
    login_page = client.get("/admin/login")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)

    response = client.post(
        "/admin/login",
        data={"password": "A" * 32, "csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "mail_portal_admin=" in response.headers["set-cookie"]
    assert "samesite=lax" in response.headers["set-cookie"].lower()


def test_device_code_start_displays_user_code_without_exposing_device_secret(client: TestClient, monkeypatch) -> None:
    _login(client)
    monkeypatch.setattr(
        "app.routes.admin.request_device_code",
        lambda **kwargs: {
            "device_code": "device-secret",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://microsoft.com/devicelogin",
            "expires_in": 900,
            "interval": 5,
            "message": "Use the code",
        },
    )

    response = client.post(
        "/admin/oauth/device/start",
        data=_common_form(_csrf(client)),
    )
    assert response.status_code == 200
    assert "ABCD-EFGH" in response.text
    assert "https://microsoft.com/devicelogin" in response.text
    assert 'name="device_verification_uri"' in response.text
    assert "device-secret" not in response.text
    transaction_id = re.search(r'name="transaction_id" value="([^"]+)"', response.text).group(1)

    db = client.app.state.session_factory()
    try:
        transaction = db.scalar(
            select(OAuthTransaction).where(OAuthTransaction.transaction_id == transaction_id)
        )
        assert transaction is not None
        assert transaction.flow_type == "device"
        assert "device-secret" not in transaction.payload_encrypted
    finally:
        db.close()


def test_device_code_start_renders_current_sync_settings_in_the_separate_panel(client: TestClient, monkeypatch) -> None:
    _login(client)
    from app.services.settings_service import set_enabled_folder_names, set_sync_interval

    db = client.app.state.session_factory()
    set_sync_interval(db, 600)
    set_enabled_folder_names(db, ["Inbox", "Archive"])
    db.commit()
    db.close()
    monkeypatch.setattr(
        "app.routes.admin.request_device_code",
        lambda **kwargs: {
            "device_code": "device-secret",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://microsoft.com/devicelogin",
            "expires_in": 900,
            "interval": 5,
            "message": "Use the code",
        },
    )

    response = client.post("/admin/oauth/device/start", data=_common_form(_csrf(client)))

    assert response.status_code == 200
    assert 'value="600"' in response.text
    assert 'value="Inbox,Archive"' in response.text


def test_pending_device_confirmation_preserves_current_sync_settings(client: TestClient, monkeypatch) -> None:
    _login(client)
    from app.services.settings_service import set_enabled_folder_names, set_sync_interval

    db = client.app.state.session_factory()
    set_sync_interval(db, 900)
    set_enabled_folder_names(db, ["Junk Email"])
    db.commit()
    db.close()
    monkeypatch.setattr(
        "app.routes.admin.request_device_code",
        lambda **kwargs: {
            "device_code": "device-secret",
            "user_code": "WXYZ-1234",
            "verification_uri": "https://microsoft.com/devicelogin",
            "expires_in": 900,
            "interval": 5,
        },
    )
    start = client.post("/admin/oauth/device/start", data=_common_form(_csrf(client)))
    transaction_id = re.search(r'name="transaction_id" value="([^"]+)"', start.text).group(1)
    monkeypatch.setattr("app.routes.admin.poll_device_code", lambda **kwargs: (_ for _ in ()).throw(DeviceAuthorizationPending()))

    response = client.post(
        "/admin/oauth/device/confirm",
        data={"csrf_token": _csrf(client), "transaction_id": transaction_id},
    )

    assert response.status_code == 409
    assert "WXYZ-1234" in response.text
    assert 'value="900"' in response.text
    assert 'value="Junk Email"' in response.text


def test_device_code_confirmation_saves_config_after_authorization(client: TestClient, monkeypatch) -> None:
    _login(client)
    monkeypatch.setattr(
        "app.routes.admin.request_device_code",
        lambda **kwargs: {
            "device_code": "device-secret",
            "user_code": "WXYZ-1234",
            "verification_uri": "https://microsoft.com/devicelogin",
            "expires_in": 900,
            "interval": 5,
        },
    )
    start = client.post(
        "/admin/oauth/device/start",
        data=_common_form(_csrf(client)),
    )
    transaction_id = re.search(r'name="transaction_id" value="([^"]+)"', start.text).group(1)

    monkeypatch.setattr(
        "app.routes.admin.poll_device_code",
        lambda **kwargs: {
            "access_token": "access-token",
            "refresh_token": "device-refresh-token",
            "expires_in": 3600,
        },
    )
    monkeypatch.setattr(
        "app.routes.admin.validate_access_token_for_mailbox",
        lambda **kwargs: {
            "mailbox_address": "mother@example.com",
            "account_address": "mother@example.com",
        },
    )
    confirm = client.post(
        "/admin/oauth/device/confirm",
        data={"csrf_token": _csrf(client), "transaction_id": transaction_id},
        follow_redirects=False,
    )

    assert confirm.status_code == 303
    assert confirm.headers["location"] == "/admin/mailbox"
    config = json.loads(client.app.state.microsoft_oauth_path.read_text(encoding="utf-8"))
    assert config["refresh_token"] == "device-refresh-token"
    assert config["auth_method"] == "device"
def test_manual_refresh_token_remains_supported(client: TestClient, monkeypatch) -> None:
    _login(client)
    monkeypatch.setattr(
        "app.routes.admin.validate_oauth_config",
        lambda config: {
            "mailbox_address": config["mailbox_address"],
            "account_address": config["mailbox_address"],
        },
    )
    form = _common_form(_csrf(client))
    form["mailbox_address"] = "manual@example.com"
    form["client_id"] = "manual-client"
    form.update({"oauth_mode": "manual", "refresh_token": "manual-refresh-token"})
    response = client.post("/admin/mailbox", data=form, follow_redirects=False)

    assert response.status_code == 303
    config = json.loads(client.app.state.microsoft_oauth_path.read_text(encoding="utf-8"))
    assert config["refresh_token"] == "manual-refresh-token"
    assert config["auth_method"] == "manual"


def test_mailbox_page_exposes_three_mutually_exclusive_authorization_methods(client: TestClient) -> None:
    _login(client)
    page = client.get("/admin/mailbox")

    assert page.status_code == 200
    assert 'value="web"' in page.text
    assert 'value="device"' in page.text
    assert 'value="manual"' in page.text
    assert len(re.findall(r'<input[^>]+name="oauth_mode_choice"', page.text)) == 3


def test_connected_web_page_hides_authorization_choices_until_reconfigured(client: TestClient) -> None:
    _login(client)
    save_oauth_config(
        client.app.state.microsoft_oauth_path,
        {
            "mailbox_address": "mother@example.com",
            "client_id": "client-id",
            "authority": "consumers",
            "refresh_token": "refresh-token",
            "auth_method": "web",
        },
    )

    page = client.get("/admin/mailbox")

    assert len(re.findall(r'<input[^>]+name="oauth_mode_choice"', page.text)) == 0
    assert "当前授权方式" in page.text


def test_mailbox_page_separates_sync_settings_from_oauth_configuration(client: TestClient) -> None:
    _login(client)

    page = client.get("/admin/mailbox")

    assert page.status_code == 200
    assert 'id="sync-settings-form"' in page.text
    assert 'action="/admin/mailbox/sync-settings"' in page.text
    assert 'action="/admin/mailbox/sync-toggle"' in page.text
    assert 'name="sync_interval_seconds"' in page.text
    assert 'id="sync_interval_seconds"' in page.text
    assert 'min="10"' in page.text
    assert 'value="30"' in page.text
    assert 'name="folder_names"' in page.text
    assert 'name="sync_interval_seconds"' not in page.text.split('id="oauthForm"', 1)[-1]


def test_admin_settings_page_separates_captcha_sync_and_oauth_cards(client: TestClient) -> None:
    _login(client)

    page = client.get("/admin/settings")

    assert page.status_code == 200
    assert "管理员设置" in page.text
    assert "母邮箱与同步" not in page.text
    assert "验证码设置" in page.text
    assert 'action="/admin/mailbox/captcha-toggle"' in page.text
    assert 'id="sync-settings-form"' in page.text
    assert "维护母邮箱" in page.text
    assert 'id="oauthForm"' not in page.text
    assert page.text.count("settings-card") >= 3


def test_dashboard_header_does_not_expose_mother_mailbox_maintenance_link(client: TestClient) -> None:
    _login(client)

    page = client.get("/admin")

    assert page.status_code == 200
    assert '<a href="/admin/mailboxes">母邮箱</a>' not in page.text


def test_admin_settings_shows_mother_mailbox_list_with_add_and_edit_entries(client: TestClient) -> None:
    _login(client)
    db = client.app.state.session_factory()
    mailbox = MotherMailbox(
        email_address="mother@example.com",
        normalized_email="mother@example.com",
        client_id="client-id",
        authority="consumers",
        auth_method="manual",
        enabled=True,
    )
    db.add(mailbox)
    db.commit()
    db.refresh(mailbox)
    mailbox_id = mailbox.id
    db.close()

    page = client.get("/admin/settings")

    assert page.status_code == 200
    assert "维护母邮箱" in page.text
    assert 'href="/admin/mailbox?new=1"' in page.text
    assert "mother@example.com" in page.text
    assert f'href="/admin/mailbox?mailbox_id={mailbox_id}&edit=1"' in page.text
    assert 'id="oauthForm"' not in page.text


def test_unknown_mother_mailbox_id_does_not_fall_back_to_legacy_configuration(client: TestClient) -> None:
    _login(client)
    save_oauth_config(
        client.app.state.microsoft_oauth_path,
        {
            "mailbox_address": "legacy@example.com",
            "client_id": "legacy-client",
            "authority": "consumers",
            "refresh_token": "legacy-refresh-token",
            "auth_method": "manual",
        },
    )

    page = client.get("/admin/mailbox?mailbox_id=999&edit=1")

    assert page.status_code == 404


def test_new_mother_mailbox_enters_the_existing_maintenance_form(client: TestClient) -> None:
    _login(client)

    page = client.get("/admin/mailbox?new=1")

    assert page.status_code == 200
    assert "新增母邮箱" in page.text
    assert 'id="oauthForm"' in page.text
    assert 'action="/admin/mailbox?new=1"' in page.text
    assert 'id="mailbox_address"' in page.text


def test_new_mother_mailbox_manual_authorization_persists_identity_and_id_scoped_config(
    client: TestClient, monkeypatch
) -> None:
    _login(client)
    monkeypatch.setattr(
        "app.routes.admin.validate_oauth_config",
        lambda config: {
            "mailbox_address": config["mailbox_address"],
            "account_address": config["mailbox_address"],
        },
    )

    form = _common_form(_csrf(client))
    form.update(
        {
            "mailbox_address": "new-mother@example.com",
            "client_id": "new-client",
            "authority": "consumers",
            "oauth_mode": "manual",
            "refresh_token": "new-refresh-token",
        }
    )
    response = client.post("/admin/mailbox?new=1", data=form, follow_redirects=False)

    assert response.status_code == 303
    assert "mailbox_id=" in response.headers["location"]
    mailbox_id = int(response.headers["location"].split("mailbox_id=", 1)[1])

    db = client.app.state.session_factory()
    try:
        mailbox = db.get(MotherMailbox, mailbox_id)
        assert mailbox is not None
        assert mailbox.email_address == "new-mother@example.com"
        assert mailbox.client_id == "new-client"
        assert mailbox.enabled is True
    finally:
        db.close()

    from app.config import oauth_config_path

    config = json.loads(oauth_config_path(mailbox_id).read_text(encoding="utf-8"))
    assert config["refresh_token"] == "new-refresh-token"
    assert config["auth_method"] == "manual"


def test_new_mother_mailbox_web_authorization_uses_new_mailbox_flow_without_legacy_file(
    client: TestClient, monkeypatch
) -> None:
    _login(client)
    monkeypatch.setattr(
        "app.routes.admin.build_authorization_url",
        lambda **kwargs: "https://login.example/authorize?state=" + kwargs["state"],
    )

    form = _common_form(_csrf(client))
    form.update(
        {
            "mailbox_address": "web-new@example.com",
            "client_id": "web-client",
            "authority": "consumers",
        }
    )
    response = client.post("/admin/oauth/web/start?new=1", data=form, follow_redirects=False)

    assert response.status_code == 200
    assert "https://login.example/authorize?state=" in response.text
    assert "web-new@example.com" in response.text

    db = client.app.state.session_factory()
    try:
        mailbox = db.scalar(select(MotherMailbox).where(MotherMailbox.normalized_email == "web-new@example.com"))
        assert mailbox is not None
        assert mailbox.enabled is False
    finally:
        db.close()
    assert not client.app.state.microsoft_oauth_path.exists()


def test_legacy_oauth_start_can_reuse_the_migrated_first_mailbox_without_duplicate_error(
    client: TestClient, monkeypatch
) -> None:
    _login(client)
    db = client.app.state.session_factory()
    db.add(
        MotherMailbox(
            email_address="mother@example.com",
            normalized_email="mother@example.com",
            client_id="client-id",
            authority="consumers",
            auth_method="manual",
            enabled=False,
        )
    )
    db.commit()
    db.close()
    monkeypatch.setattr(
        "app.routes.admin.build_authorization_url",
        lambda **kwargs: "https://login.example/authorize?state=" + kwargs["state"],
    )

    response = client.post("/admin/oauth/web/start", data=_common_form(_csrf(client)))

    assert response.status_code == 200
    assert "https://login.example/authorize?state=" in response.text


def test_edit_mother_mailbox_updates_identity_from_the_maintenance_form(
    client: TestClient, monkeypatch
) -> None:
    _login(client)
    db = client.app.state.session_factory()
    mailbox = MotherMailbox(
        email_address="old-mother@example.com",
        normalized_email="old-mother@example.com",
        client_id="old-client",
        authority="consumers",
        auth_method="manual",
        enabled=True,
    )
    db.add(mailbox)
    db.commit()
    db.refresh(mailbox)
    mailbox_id = mailbox.id
    db.close()

    from app.config import oauth_config_path
    from app.services.microsoft_oauth import save_oauth_config

    save_oauth_config(
        oauth_config_path(mailbox_id),
        {
            "mailbox_address": "old-mother@example.com",
            "client_id": "old-client",
            "authority": "consumers",
            "refresh_token": "old-refresh-token",
            "auth_method": "manual",
        },
    )
    monkeypatch.setattr(
        "app.routes.admin.validate_oauth_config",
        lambda config: {
            "mailbox_address": config["mailbox_address"],
            "account_address": config["mailbox_address"],
        },
    )

    form = _common_form(_csrf(client))
    form.update(
        {
            "mailbox_id": str(mailbox_id),
            "mailbox_address": "edited-mother@example.com",
            "client_id": "edited-client",
            "authority": "consumers",
            "oauth_mode": "manual",
            "refresh_token": "edited-refresh-token",
        }
    )
    response = client.post("/admin/mailbox?edit=1", data=form, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/admin/mailbox?mailbox_id={mailbox_id}"
    db = client.app.state.session_factory()
    try:
        mailbox = db.get(MotherMailbox, mailbox_id)
        assert mailbox is not None
        assert mailbox.email_address == "edited-mother@example.com"
        assert mailbox.client_id == "edited-client"
    finally:
        db.close()


def test_admin_can_toggle_public_captcha_from_settings_page(client: TestClient) -> None:
    _login(client)
    csrf = _csrf(client)

    response = client.post(
        "/admin/mailbox/captcha-toggle",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/settings?captcha=toggled"
    from app.services.settings_service import get_captcha_enabled

    db = client.app.state.session_factory()
    try:
        assert get_captcha_enabled(db) is False
    finally:
        db.close()


def test_web_authorization_keeps_connected_mailbox_fields_read_only_until_reconfigured(client: TestClient) -> None:
    _login(client)
    save_oauth_config(
        client.app.state.microsoft_oauth_path,
        {
            "mailbox_address": "mother@example.com",
            "client_id": "client-id",
            "authority": "consumers",
            "refresh_token": "refresh-token",
            "auth_method": "web",
        },
    )

    page = client.get("/admin/mailbox")
    for field_id in ("mailbox_address", "client_id", "authority"):
        field = re.search(rf'<input[^>]+id="{field_id}"[^>]*>', page.text).group(0)
        assert "readonly" in field
    assert "/admin/mailbox?edit=1" in page.text

    edit_page = client.get("/admin/mailbox?edit=1")
    for field_id in ("mailbox_address", "client_id", "authority"):
        field = re.search(rf'<input[^>]+id="{field_id}"[^>]*>', edit_page.text).group(0)
        assert "readonly" not in field


def test_manual_authorization_keeps_mailbox_fields_editable(client: TestClient) -> None:
    _login(client)
    save_oauth_config(
        client.app.state.microsoft_oauth_path,
        {
            "mailbox_address": "mother@example.com",
            "client_id": "client-id",
            "authority": "consumers",
            "refresh_token": "refresh-token",
            "auth_method": "manual",
        },
    )

    page = client.get("/admin/mailbox")
    for field_id in ("mailbox_address", "client_id", "authority"):
        field = re.search(rf'<input[^>]+id="{field_id}"[^>]*>', page.text).group(0)
        assert "readonly" not in field


def test_web_authorization_rejects_changed_identity_without_reconfigure(client: TestClient, monkeypatch) -> None:
    _login(client)
    save_oauth_config(
        client.app.state.microsoft_oauth_path,
        {
            "mailbox_address": "mother@example.com",
            "client_id": "client-id",
            "authority": "consumers",
            "refresh_token": "refresh-token",
            "auth_method": "web",
        },
    )
    monkeypatch.setattr(
        "app.routes.admin.build_authorization_url",
        lambda **kwargs: "https://login.example/authorize?state=" + kwargs["state"],
    )

    form = _common_form(_csrf(client))
    form.update({"sync_interval_seconds": "600", "folder_names": "Inbox,Junk Email"})
    form["mailbox_address"] = "other@example.com"
    response = client.post("/admin/oauth/web/start", data=form, follow_redirects=False)

    assert response.status_code == 400
    assert "只读" in response.text


def test_web_authorization_allows_changed_identity_after_reconfigure(client: TestClient, monkeypatch) -> None:
    _login(client)
    save_oauth_config(
        client.app.state.microsoft_oauth_path,
        {
            "mailbox_address": "mother@example.com",
            "client_id": "client-id",
            "authority": "consumers",
            "refresh_token": "refresh-token",
            "auth_method": "web",
        },
    )
    monkeypatch.setattr(
        "app.routes.admin.build_authorization_url",
        lambda **kwargs: "https://login.example/authorize?state=" + kwargs["state"],
    )

    form = _common_form(_csrf(client))
    form.update({"sync_interval_seconds": "600", "folder_names": "Inbox,Junk Email"})
    form["mailbox_address"] = "other@example.com"
    response = client.post("/admin/oauth/web/start?edit=1", data=form, follow_redirects=False)

    assert response.status_code == 200
    assert "https://login.example/authorize?state=" in response.text


def test_manual_authorization_rejects_changed_identity_without_reconfigure(client: TestClient, monkeypatch) -> None:
    _login(client)
    save_oauth_config(
        client.app.state.microsoft_oauth_path,
        {
            "mailbox_address": "mother@example.com",
            "client_id": "client-id",
            "authority": "consumers",
            "refresh_token": "refresh-token",
            "auth_method": "manual",
        },
    )
    monkeypatch.setattr(
        "app.routes.admin.validate_oauth_config",
        lambda config: {"mailbox_address": config["mailbox_address"], "account_address": config["mailbox_address"]},
    )
    form = _common_form(_csrf(client))
    form["mailbox_address"] = "other@example.com"
    form["refresh_token"] = "new-refresh-token"

    response = client.post("/admin/mailbox", data=form, follow_redirects=False)

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    from urllib.parse import parse_qs, urlparse

    assert "只读" in parse_qs(urlparse(response.headers["location"]).query)["error"][0]
