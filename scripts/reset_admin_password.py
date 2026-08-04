from __future__ import annotations

from app.config import instance_secrets_path
from app.services.instance_secrets import set_admin_password


if __name__ == "__main__":
    import os

    path = instance_secrets_path()
    import getpass

    password = getpass.getpass("New admin password (minimum 16 characters): ")
    confirm = getpass.getpass("Confirm new admin password: ")
    if password != confirm:
        raise SystemExit("Passwords do not match")
    set_admin_password(path, password)
    print("Admin password updated; instance secrets were preserved.")
