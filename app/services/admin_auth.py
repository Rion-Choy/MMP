from __future__ import annotations

import secrets
import string

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError


_PASSWORD_ALPHABET = string.ascii_letters + string.digits
_PASSWORD_HASHER = PasswordHasher()


def generate_admin_password() -> str:
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(32))


def hash_admin_password(password: str) -> str:
    return _PASSWORD_HASHER.hash(password)


def verify_admin_password(password: str, password_hash: str) -> bool:
    try:
        return _PASSWORD_HASHER.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False
