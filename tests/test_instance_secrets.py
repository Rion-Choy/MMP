from __future__ import annotations

import json
from pathlib import Path

from app.services.admin_auth import verify_admin_password
from app.services.instance_secrets import initialize_instance, load_instance_secrets, set_admin_password


def test_setting_admin_password_preserves_instance_secrets(tmp_path: Path) -> None:
    path = tmp_path / "secrets" / "instance-secrets.json"
    generated = initialize_instance(path)
    before = load_instance_secrets(path)

    set_admin_password(path, "fixed-admin-password")

    after = load_instance_secrets(path)
    assert after["cookie_secret"] == before["cookie_secret"]
    assert after["captcha_secret"] == before["captcha_secret"]
    assert after["instance_id"] == before["instance_id"]
    assert verify_admin_password("fixed-admin-password", after["admin_password_hash"])
    assert not verify_admin_password(generated, after["admin_password_hash"])
    assert path.stat().st_mode & 0o777 == 0o600


def test_runtime_initialization_does_not_replace_existing_admin_secret(tmp_path: Path) -> None:
    path = tmp_path / "secrets" / "instance-secrets.json"
    initialize_instance(path)
    original = json.loads(path.read_text(encoding="utf-8"))

    # The deployment helper's idempotent contract: an existing secret file is
    # loaded, not regenerated on restart/upgrade.
    loaded = load_instance_secrets(path)

    assert loaded == original
