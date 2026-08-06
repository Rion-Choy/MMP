"""add source-scoped mailboxes and fixed-trigger sync records

Revision ID: 0006_multi_mother_mailboxes
Revises: 0005_target_notes
Create Date: 2026-08-06
"""
from __future__ import annotations

from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision: str = "0006_multi_mother_mailboxes"
down_revision: Union[str, None] = "0005_target_notes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in inspect(bind).get_columns(table)}


def _tables(bind) -> set[str]:
    return set(inspect(bind).get_table_names())


def _indexes(bind, table: str) -> set[str]:
    return {index["name"] for index in inspect(bind).get_indexes(table)}


def _add_column_if_missing(bind, table: str, column: sa.Column) -> None:
    if column.name not in _columns(bind, table):
        op.add_column(table, column)


def _add_index_if_missing(bind, name: str, table: str, columns: list[str], *, unique: bool = False) -> None:
    if name not in _indexes(bind, table):
        op.create_index(name, table, columns, unique=unique)


def _create_source_tables(bind) -> None:
    if "mother_mailboxes" not in _tables(bind):
        op.create_table(
            "mother_mailboxes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("email_address", sa.String(length=320), nullable=False),
            sa.Column("normalized_email", sa.String(length=320), nullable=False),
            sa.Column("client_id", sa.String(length=512), nullable=False, server_default=""),
            sa.Column("authority", sa.String(length=255), nullable=False, server_default="consumers"),
            sa.Column("auth_method", sa.String(length=16), nullable=False, server_default="manual"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("last_sync_at", sa.DateTime(), nullable=True),
            sa.Column("last_sync_status", sa.String(length=32), nullable=True),
            sa.Column("last_sync_error", sa.Text(), nullable=True),
            sa.UniqueConstraint("normalized_email", name="uq_mother_mailboxes_normalized_email"),
        )
        _add_index_if_missing(bind, "ix_mother_mailboxes_normalized_email", "mother_mailboxes", ["normalized_email"])

    if "sync_triggers" not in _tables(bind):
        op.create_table(
            "sync_triggers",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("triggered_at", sa.DateTime(), nullable=False),
            sa.Column("observed_at", sa.DateTime(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("skip_reason", sa.String(length=64), nullable=True),
            sa.Column("trigger_source", sa.String(length=16), nullable=False, server_default="scheduled"),
        )
        _add_index_if_missing(bind, "ix_sync_triggers_triggered_at", "sync_triggers", ["triggered_at"])

    if "sync_cycles" not in _tables(bind):
        op.create_table(
            "sync_cycles",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("trigger_id", sa.Integer(), sa.ForeignKey("sync_triggers.id"), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("mailbox_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("completed_mailbox_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed_mailbox_count", sa.Integer(), nullable=False, server_default="0"),
            sa.UniqueConstraint("trigger_id", name="uq_sync_cycles_trigger_id"),
        )


def _copy_source_table(bind, table: str, source_column: str, new_unique: str) -> None:
    inspector = inspect(bind)
    if any(c.get("name") == new_unique for c in inspector.get_unique_constraints(table)):
        return

    existing_columns = [column["name"] for column in inspector.get_columns(table)]
    temp = f"_{table}_0006_old"
    op.rename_table(table, temp)

    if table == "mail_folders":
        op.create_table(
            table,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(source_column, sa.Integer(), nullable=True),
            sa.Column("provider_folder_id", sa.String(length=512), nullable=False),
            sa.Column("folder_name", sa.String(length=255), nullable=False),
            sa.Column("parent_folder_id", sa.Integer(), nullable=True),
            sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("last_seen_at", sa.DateTime(), nullable=True),
            sa.Column("last_message_received_at", sa.DateTime(), nullable=True),
            sa.Column("last_message_id", sa.String(length=512), nullable=True),
            sa.ForeignKeyConstraint([source_column], ["mother_mailboxes.id"]),
            sa.ForeignKeyConstraint(["parent_folder_id"], ["mail_folders.id"]),
            sa.UniqueConstraint(source_column, "provider_folder_id", name=new_unique),
        )
        preserved_indexes = [("ix_mail_folders_last_message_received_at", ["last_message_received_at"])]
    else:
        op.create_table(
            table,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(source_column, sa.Integer(), nullable=True),
            sa.Column("immutable_message_id", sa.String(length=512), nullable=False),
            sa.Column("internet_message_id", sa.String(length=998), nullable=True),
            sa.Column("received_at", sa.DateTime(), nullable=False),
            sa.Column("body_text", sa.Text(), nullable=False, server_default=""),
            sa.Column("folder_id", sa.Integer(), nullable=True),
            sa.Column("folder_name", sa.String(length=255), nullable=True),
            sa.Column("first_archived_at", sa.DateTime(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False),
            sa.Column("body_fetch_error", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint([source_column], ["mother_mailboxes.id"]),
            sa.ForeignKeyConstraint(["folder_id"], ["mail_folders.id"]),
            sa.UniqueConstraint(source_column, "immutable_message_id", name=new_unique),
        )
        preserved_indexes = [("ix_mail_messages_received_at", ["received_at"])]

    legacy_columns = [column for column in existing_columns if column != source_column]
    source_select = f'"{source_column}"' if source_column in existing_columns else "NULL"
    target_columns = [source_column] + legacy_columns
    select_columns = [source_select] + [f'"{column}"' for column in legacy_columns]
    bind.execute(
        text(
            f'INSERT INTO "{table}" ({", ".join(chr(34) + c + chr(34) for c in target_columns)}) '
            f'SELECT {", ".join(select_columns)} FROM "{temp}"'
        )
    )
    op.drop_table(temp)
    for name, columns in preserved_indexes:
        _add_index_if_missing(bind, name, table, columns)


def _rebuild_mail_recipients(bind) -> None:
    inspector = inspect(bind)
    foreign_keys = inspector.get_foreign_keys("mail_recipients")
    if any(fk.get("referred_table") == "mail_messages" and fk.get("referred_table") != "_mail_messages_0006_old" for fk in foreign_keys):
        return
    for index_name in ("ix_mail_recipients_normalized_email", "ix_mail_recipients_email_message"):
        bind.execute(text(f'DROP INDEX IF EXISTS "{index_name}"'))
    existing_columns = [column["name"] for column in inspector.get_columns("mail_recipients")]
    temp = "_mail_recipients_0006_old"
    op.rename_table("mail_recipients", temp)
    op.create_table(
        "mail_recipients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("normalized_email", sa.String(length=320), nullable=False),
        sa.Column("recipient_type", sa.String(length=3), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["mail_messages.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("message_id", "normalized_email", "recipient_type"),
    )
    columns = ["id", "message_id", "normalized_email", "recipient_type"]
    columns = [column for column in columns if column in existing_columns]
    quoted = ", ".join(f'"{column}"' for column in columns)
    bind.execute(text(f'INSERT INTO "mail_recipients" ({quoted}) SELECT {quoted} FROM "{temp}"'))
    op.drop_table(temp)
    _add_index_if_missing(bind, "ix_mail_recipients_normalized_email", "mail_recipients", ["normalized_email"])
    _add_index_if_missing(bind, "ix_mail_recipients_email_message", "mail_recipients", ["normalized_email", "message_id"])


def _rebuild_sync_runs(bind) -> None:
    inspector = inspect(bind)
    foreign_keys = inspector.get_foreign_keys("sync_runs")
    targets = {fk.get("referred_table") for fk in foreign_keys}
    if any(fk.get("referred_table") == "mother_mailboxes" for fk in foreign_keys) and any(fk.get("referred_table") == "sync_cycles" for fk in foreign_keys):
        return
    for index_name in (
        "ix_sync_runs_cycle_id",
        "ix_sync_runs_mailbox_cycle_started",
        "uq_sync_runs_cycle_mailbox",
    ):
        bind.execute(text(f'DROP INDEX IF EXISTS "{index_name}"'))
    existing_columns = [column["name"] for column in inspector.get_columns("sync_runs")]
    temp = "_sync_runs_0006_old"
    op.rename_table("sync_runs", temp)
    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mother_mailbox_id", sa.Integer(), nullable=True),
        sa.Column("cycle_id", sa.Integer(), nullable=True),
        sa.Column("cycle_started_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("fetched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unique_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("inserted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["mother_mailbox_id"], ["mother_mailboxes.id"]),
        sa.ForeignKeyConstraint(["cycle_id"], ["sync_cycles.id"]),
    )
    columns = [
        "id", "mother_mailbox_id", "cycle_id", "cycle_started_at", "started_at", "finished_at",
        "status", "fetched_count", "unique_count", "inserted_count", "updated_count", "duplicate_count", "error_message",
    ]
    columns = [column for column in columns if column in existing_columns]
    quoted = ", ".join(f'"{column}"' for column in columns)
    bind.execute(text(f'INSERT INTO "sync_runs" ({quoted}) SELECT {quoted} FROM "{temp}"'))
    op.drop_table(temp)


def _rebuild_oauth_transactions(bind) -> None:
    inspector = inspect(bind)
    foreign_keys = inspector.get_foreign_keys("oauth_transactions")
    if any(fk.get("referred_table") == "mother_mailboxes" for fk in foreign_keys):
        return
    # The existing transaction table may have been created by 0002 with a
    # valid global transaction_id unique; rebuild only when its FK is absent.
    for index_name in (
        "ix_oauth_transactions_transaction_id",
        "ix_oauth_transactions_state_hash",
        "ix_oauth_transactions_expires_at",
        "ix_oauth_transactions_mother_mailbox_id",
    ):
        bind.execute(text(f'DROP INDEX IF EXISTS "{index_name}"'))
    existing_columns = [column["name"] for column in inspector.get_columns("oauth_transactions")]
    temp = "_oauth_transactions_0006_old"
    op.rename_table("oauth_transactions", temp)
    op.create_table(
        "oauth_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mother_mailbox_id", sa.Integer(), nullable=True),
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=True),
        sa.Column("flow_type", sa.String(length=16), nullable=False),
        sa.Column("payload_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["mother_mailbox_id"], ["mother_mailboxes.id"]),
        sa.UniqueConstraint("transaction_id"),
    )
    columns = [
        "id", "mother_mailbox_id", "transaction_id", "state_hash", "flow_type", "payload_encrypted",
        "created_at", "expires_at", "used_at",
    ]
    columns = [column for column in columns if column in existing_columns]
    quoted = ", ".join(f'"{column}"' for column in columns)
    bind.execute(text(f'INSERT INTO "oauth_transactions" ({quoted}) SELECT {quoted} FROM "{temp}"'))
    op.drop_table(temp)


def _historical_cycles(bind, legacy_id: int) -> None:
    rows = bind.execute(
        text("SELECT id, started_at, finished_at, status FROM sync_runs ORDER BY id")
    ).mappings().all()
    for row in rows:
        existing = bind.execute(
            text("SELECT cycle_id FROM sync_runs WHERE id = :run_id"), {"run_id": row["id"]}
        ).scalar()
        if existing is not None:
            continue
        trigger_id = bind.execute(
            text(
                "INSERT INTO sync_triggers "
                "(triggered_at, observed_at, status, skip_reason, trigger_source) "
                "VALUES (:triggered, :observed, 'started', NULL, 'legacy')"
            ),
            {
                "triggered": row["started_at"],
                "observed": row["started_at"],
            },
        ).lastrowid
        cycle_status = row["status"] if row["status"] in {"success", "partial", "failed"} else "failed"
        finished = row["finished_at"] or row["started_at"]
        cycle_id = bind.execute(
            text(
                "INSERT INTO sync_cycles "
                "(trigger_id, started_at, finished_at, status, mailbox_count, completed_mailbox_count, failed_mailbox_count) "
                "VALUES (:trigger, :started, :finished, :status, 1, :completed, :failed)"
            ),
            {
                "trigger": trigger_id,
                "started": row["started_at"],
                "finished": finished,
                "status": cycle_status,
                "completed": 0 if cycle_status == "failed" else 1,
                "failed": 1 if cycle_status == "failed" else 0,
            },
        ).lastrowid
        bind.execute(
            text(
                "UPDATE sync_runs SET mother_mailbox_id = :mailbox, cycle_id = :cycle, "
                "cycle_started_at = started_at WHERE id = :run_id"
            ),
            {"mailbox": legacy_id, "cycle": cycle_id, "run_id": row["id"]},
        )


def upgrade() -> None:
    bind = op.get_bind()
    _create_source_tables(bind)

    _add_column_if_missing(bind, "mail_folders", sa.Column("mother_mailbox_id", sa.Integer(), nullable=True))
    _add_column_if_missing(bind, "mail_messages", sa.Column("mother_mailbox_id", sa.Integer(), nullable=True))
    _add_column_if_missing(bind, "sync_runs", sa.Column("mother_mailbox_id", sa.Integer(), nullable=True))
    _add_column_if_missing(bind, "sync_runs", sa.Column("cycle_id", sa.Integer(), nullable=True))
    _add_column_if_missing(bind, "sync_runs", sa.Column("cycle_started_at", sa.DateTime(), nullable=True))
    _add_column_if_missing(bind, "oauth_transactions", sa.Column("mother_mailbox_id", sa.Integer(), nullable=True))

    _copy_source_table(bind, "mail_folders", "mother_mailbox_id", "uq_mail_folders_mailbox_provider")
    _copy_source_table(bind, "mail_messages", "mother_mailbox_id", "uq_mail_messages_mailbox_immutable")
    _rebuild_mail_recipients(bind)
    _rebuild_sync_runs(bind)
    _rebuild_oauth_transactions(bind)

    _add_index_if_missing(bind, "ix_sync_runs_mailbox_cycle_started", "sync_runs", ["mother_mailbox_id", "cycle_started_at", "started_at"])
    _add_index_if_missing(bind, "ix_sync_runs_cycle_id", "sync_runs", ["cycle_id"])
    _add_index_if_missing(bind, "ix_mail_folders_mother_mailbox_id", "mail_folders", ["mother_mailbox_id"])
    _add_index_if_missing(bind, "ix_mail_messages_mother_mailbox_id", "mail_messages", ["mother_mailbox_id"])
    _add_index_if_missing(bind, "ix_oauth_transactions_transaction_id", "oauth_transactions", ["transaction_id"])
    _add_index_if_missing(bind, "ix_oauth_transactions_state_hash", "oauth_transactions", ["state_hash"])
    _add_index_if_missing(bind, "ix_oauth_transactions_expires_at", "oauth_transactions", ["expires_at"])
    _add_index_if_missing(bind, "ix_oauth_transactions_mother_mailbox_id", "oauth_transactions", ["mother_mailbox_id"])
    _add_index_if_missing(bind, "uq_sync_runs_cycle_mailbox", "sync_runs", ["cycle_id", "mother_mailbox_id"], unique=True)

    now = datetime.utcnow()
    legacy_id = bind.execute(text("SELECT id FROM mother_mailboxes ORDER BY id LIMIT 1")).scalar()
    if legacy_id is None:
        legacy_id = bind.execute(
            text(
                "INSERT INTO mother_mailboxes "
                "(email_address, normalized_email, client_id, authority, auth_method, enabled, created_at, updated_at) "
                "VALUES ('legacy-unconfigured', 'legacy-unconfigured', '', 'consumers', 'manual', 1, :now, :now)"
            ),
            {"now": now},
        ).lastrowid

    for table in ("mail_folders", "mail_messages", "oauth_transactions"):
        bind.execute(
            text(f"UPDATE {table} SET mother_mailbox_id = :id WHERE mother_mailbox_id IS NULL"),
            {"id": legacy_id},
        )
    _historical_cycles(bind, legacy_id)


def downgrade() -> None:
    raise RuntimeError("0006_multi_mother_mailboxes is data-preserving but not automatically reversible; restore the pre-migration backup")
