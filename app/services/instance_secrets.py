from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.config import instance_secrets_path
from app.services.admin_auth import generate_admin_password, hash_admin_password


def _write_json_0600(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def load_instance_secrets(path: Path | None = None) -> dict[str, Any]:
    target = path or instance_secrets_path()
    if not target.exists():
        raise FileNotFoundError(f"instance secrets not initialized: {target}")
    return json.loads(target.read_text(encoding="utf-8"))


def initialize_instance(path: Path | None = None) -> str:
    target = path or instance_secrets_path()
    password = generate_admin_password()
    payload = {
        "admin_password_hash": hash_admin_password(password),
        "cookie_secret": secrets.token_urlsafe(48),
        "captcha_secret": secrets.token_urlsafe(48),
        "instance_id": secrets.token_hex(16),
    }
    _write_json_0600(target, payload)
    return password


def set_admin_password(path: Path | None, password: str) -> None:
    """Set a stable admin password without rotating other instance secrets."""
    if not isinstance(password, str) or len(password) < 16:
        raise ValueError("admin password must be at least 16 characters")
    target = path or instance_secrets_path()
    payload = load_instance_secrets(target)
    payload["admin_password_hash"] = hash_admin_password(password)
    _write_json_0600(target, payload)


def reset_admin_password(path: Path | None = None) -> str:
    target = path or instance_secrets_path()
    payload = load_instance_secrets(target)
    password = generate_admin_password()
    payload["admin_password_hash"] = hash_admin_password(password)
    _write_json_0600(target, payload)
    return password


def session_id_hash(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def secret_mac(secret: str, value: str) -> str:
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def _fernet_for_secret(secret: str) -> Fernet:
    key = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(__import__("base64").urlsafe_b64encode(key))


def encrypt_secret_text(secret: str, value: str) -> str:
    return _fernet_for_secret(secret).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret_text(secret: str, value: str) -> str:
    try:
        return _fernet_for_secret(secret).decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("invalid encrypted secret payload") from exc
