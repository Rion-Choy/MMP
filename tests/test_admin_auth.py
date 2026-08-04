from __future__ import annotations

import re

from app.services.admin_auth import (
    generate_admin_password,
    hash_admin_password,
    verify_admin_password,
)


def test_admin_password_is_32_alphanumeric_characters() -> None:
    password = generate_admin_password()

    assert len(password) == 32
    assert re.fullmatch(r"[A-Za-z0-9]{32}", password)


def test_admin_password_hash_verifies_only_original_password() -> None:
    password = generate_admin_password()
    password_hash = hash_admin_password(password)

    assert verify_admin_password(password, password_hash)
    assert not verify_admin_password(password + "x", password_hash)
