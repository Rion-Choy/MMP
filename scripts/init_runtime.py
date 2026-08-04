from __future__ import annotations

import argparse
import os
from pathlib import Path

from app.config import data_dir
from app.services.instance_secrets import initialize_instance


def ensure_runtime_layout(root: Path) -> None:
    for path in (root, root / "data", root / "secrets", root / "backups"):
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)


def initialize_runtime(root: Path) -> str | None:
    """Create runtime secrets only once; never rotate them during upgrades."""
    ensure_runtime_layout(root)
    secrets_path = root / "secrets" / "instance-secrets.json"
    if secrets_path.exists():
        return None
    password = initialize_instance(secrets_path)
    os.chmod(secrets_path, 0o600)
    return password


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize mail-portal runtime data")
    parser.add_argument("--root", type=Path, default=data_dir())
    parser.add_argument("--migrate", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    password = initialize_runtime(args.root)
    if password:
        print(f"Admin password (save it now; it will not be displayed again): {password}")
    else:
        print(f"Runtime already initialized: {args.root}")
    if args.migrate:
        import os
        os.environ["MAIL_PORTAL_DATA_DIR"] = str(args.root)
        from alembic import command
        from alembic.config import Config
        command.upgrade(Config("alembic.ini"), "head")

