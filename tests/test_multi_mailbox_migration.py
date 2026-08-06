from __future__ import annotations

from datetime import datetime

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _upgrade_to(tmp_path, monkeypatch, revision: str) -> str:
    db_path = tmp_path / "migration.sqlite3"
    url = f"sqlite+pysqlite:///{db_path}"
    monkeypatch.setenv("MAIL_PORTAL_DATABASE_URL", url)
    monkeypatch.setenv("MAIL_PORTAL_DATA_DIR", str(tmp_path / "runtime"))
    config = Config("alembic.ini")
    command.upgrade(config, revision)
    return url


def test_multi_mailbox_migration_preserves_legacy_rows_and_scopes_ids(tmp_path, monkeypatch) -> None:
    url = _upgrade_to(tmp_path, monkeypatch, "0005_target_notes")
    engine = create_engine(url)
    now = datetime(2026, 1, 1, 12, 0, 0)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO private_targets "
                "(email_address, normalized_email, access_token, enabled, created_at, removed_at, note) "
                "VALUES (:email, :normalized, :token, 1, :now, NULL, NULL)"
            ),
            {"email": "target@example.com", "normalized": "target@example.com", "token": "11111111-1111-4111-8111-111111111111", "now": now},
        )
        conn.execute(
            text(
                "INSERT INTO mail_folders "
                "(provider_folder_id, folder_name, parent_folder_id, is_enabled, last_seen_at, last_message_received_at, last_message_id) "
                "VALUES ('folder-1', 'Inbox', NULL, 1, :now, :now, 'message-1')"
            ),
            {"now": now},
        )
        conn.execute(
            text(
                "INSERT INTO mail_messages "
                "(immutable_message_id, internet_message_id, received_at, body_text, folder_id, folder_name, first_archived_at, last_seen_at, body_fetch_error) "
                "VALUES ('message-1', NULL, :now, 'body', 1, 'Inbox', :now, :now, NULL)"
            ),
            {"now": now},
        )
        conn.execute(
            text(
                "INSERT INTO mail_recipients (message_id, normalized_email, recipient_type) "
                "VALUES (1, 'target@example.com', 'to')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO sync_runs "
                "(started_at, finished_at, status, fetched_count, unique_count, inserted_count, updated_count, duplicate_count, error_message) "
                "VALUES (:now, :now, 'success', 1, 1, 1, 0, 0, NULL)"
            ),
            {"now": now},
        )
        conn.execute(
            text(
                "INSERT INTO sync_runs "
                "(started_at, finished_at, status, fetched_count, unique_count, inserted_count, updated_count, duplicate_count, error_message) "
                "VALUES (:started, NULL, 'running', 2, 2, 2, 0, 0, NULL)"
            ),
            {"started": datetime(2026, 1, 1, 12, 5, 0)},
        )
        conn.execute(
            text(
                "INSERT INTO oauth_transactions "
                "(transaction_id, state_hash, flow_type, payload_encrypted, created_at, expires_at, used_at) "
                "VALUES ('tx-1', NULL, 'web', 'encrypted', :now, :expires, NULL)"
            ),
            {"now": now, "expires": datetime(2026, 1, 1, 13, 0, 0)},
        )

    command.upgrade(Config("alembic.ini"), "head")
    inspector = inspect(engine)
    assert {"mother_mailboxes", "sync_triggers", "sync_cycles"} <= set(inspector.get_table_names())
    for table, columns in {
        "mail_folders": {"mother_mailbox_id"},
        "mail_messages": {"mother_mailbox_id"},
        "sync_runs": {"mother_mailbox_id", "cycle_id", "cycle_started_at"},
        "oauth_transactions": {"mother_mailbox_id"},
    }.items():
        assert columns <= {column["name"] for column in inspector.get_columns(table)}

    with engine.connect() as conn:
        counts = {
            table: conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            for table in ("private_targets", "mail_folders", "mail_messages", "mail_recipients", "sync_runs", "oauth_transactions")
        }
        assert counts == {
            "private_targets": 1,
            "mail_folders": 1,
            "mail_messages": 1,
            "mail_recipients": 1,
            "sync_runs": 2,
            "oauth_transactions": 1,
        }
        cycle_ids = [row[0] for row in conn.execute(text("SELECT cycle_id FROM sync_runs ORDER BY id")).all()]
        assert len(set(cycle_ids)) == 2
        assert conn.execute(text("SELECT COUNT(*) FROM sync_cycles")).scalar_one() == 2
        assert conn.execute(text("SELECT cycle_id FROM sync_runs WHERE id = 1")).scalar_one() == 1
        assert conn.execute(text("SELECT cycle_id FROM sync_runs WHERE id = 2")).scalar_one() == 2
        assert conn.execute(text("SELECT mother_mailbox_id FROM oauth_transactions WHERE id = 1")).scalar_one() is not None
        conn.execute(
            text(
                "INSERT INTO mother_mailboxes "
                "(email_address, normalized_email, client_id, authority, auth_method, enabled, created_at, updated_at) "
                "VALUES ('second@example.com', 'second@example.com', 'client-2', 'consumers', 'manual', 1, :now, :now)"
            ),
            {"now": now},
        )
        second_id = conn.execute(text("SELECT id FROM mother_mailboxes WHERE normalized_email = 'second@example.com'")).scalar_one()
        conn.execute(
            text(
                "INSERT INTO mail_folders "
                "(mother_mailbox_id, provider_folder_id, folder_name, is_enabled, last_seen_at) "
                "VALUES (:id, 'folder-1', 'Inbox', 1, :now)"
            ),
            {"id": second_id, "now": now},
        )
        conn.execute(
            text(
                "INSERT INTO mail_messages "
                "(mother_mailbox_id, immutable_message_id, received_at, body_text, first_archived_at, last_seen_at) "
                "VALUES (:id, 'message-1', :now, 'second body', :now, :now)"
            ),
            {"id": second_id, "now": now},
        )
        assert conn.execute(text("PRAGMA integrity_check")).scalar_one() == "ok"
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
        foreign_keys = inspect(engine).get_foreign_keys("mail_recipients")
        assert any(fk["referred_table"] == "mail_messages" for fk in foreign_keys)
