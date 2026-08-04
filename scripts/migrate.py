from __future__ import annotations

import argparse
from pathlib import Path

from app.config import database_url
from app.database import ensure_database_parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    if args.root:
        import os
        os.environ["MAIL_PORTAL_DATA_DIR"] = str(args.root)
    url = database_url()
    ensure_database_parent(url)
    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    command.upgrade(config, "head")
    print(f"Database migrated: {url}")


if __name__ == "__main__":
    main()
