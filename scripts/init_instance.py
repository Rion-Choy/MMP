from __future__ import annotations

from app.config import instance_secrets_path
from app.services.instance_secrets import initialize_instance


if __name__ == "__main__":
    path = instance_secrets_path()
    password = initialize_instance(path)
    print(f"Admin password (save it now; it will not be displayed again): {password}")
