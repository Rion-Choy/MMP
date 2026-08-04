"""create initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "private_targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email_address", sa.String(length=320), nullable=False),
        sa.Column("normalized_email", sa.String(length=320), nullable=False),
        sa.Column("access_token", sa.String(length=36), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("removed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("access_token"),
    )
    op.create_index("ix_private_targets_normalized_email", "private_targets", ["normalized_email"])

    op.create_table(
        "mail_folders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider_folder_id", sa.String(length=512), nullable=False),
        sa.Column("folder_name", sa.String(length=255), nullable=False),
        sa.Column("parent_folder_id", sa.Integer(), sa.ForeignKey("mail_folders.id"), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("provider_folder_id"),
    )

    op.create_table(
        "mail_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("immutable_message_id", sa.String(length=512), nullable=False),
        sa.Column("internet_message_id", sa.String(length=998), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("folder_id", sa.Integer(), sa.ForeignKey("mail_folders.id"), nullable=True),
        sa.Column("folder_name", sa.String(length=255), nullable=True),
        sa.Column("first_archived_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("body_fetch_error", sa.Text(), nullable=True),
        sa.UniqueConstraint("immutable_message_id"),
    )
    op.create_index("ix_mail_messages_received_at", "mail_messages", ["received_at"])

    op.create_table(
        "mail_recipients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("mail_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("normalized_email", sa.String(length=320), nullable=False),
        sa.Column("recipient_type", sa.String(length=3), nullable=False),
        sa.UniqueConstraint("message_id", "normalized_email", "recipient_type"),
    )
    op.create_index("ix_mail_recipients_normalized_email", "mail_recipients", ["normalized_email"])
    op.create_index("ix_mail_recipients_email_message", "mail_recipients", ["normalized_email", "message_id"])

    op.create_table(
        "public_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id_hash", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("private_targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("captcha_answer_mac", sa.String(length=64), nullable=False),
        sa.Column("captcha_payload", sa.Text(), nullable=False, server_default=""),
        sa.Column("captcha_expires_at", sa.DateTime(), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("session_id_hash"),
    )
    op.create_index("ix_public_sessions_expires_at", "public_sessions", ["expires_at"])

    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("fetched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unique_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("inserted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
    )

    op.create_table(
        "app_settings",
        sa.Column("setting_key", sa.String(length=128), primary_key=True),
        sa.Column("setting_value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_table("sync_runs")
    op.drop_index("ix_public_sessions_expires_at", table_name="public_sessions")
    op.drop_table("public_sessions")
    op.drop_index("ix_mail_recipients_email_message", table_name="mail_recipients")
    op.drop_index("ix_mail_recipients_normalized_email", table_name="mail_recipients")
    op.drop_table("mail_recipients")
    op.drop_index("ix_mail_messages_received_at", table_name="mail_messages")
    op.drop_table("mail_messages")
    op.drop_table("mail_folders")
    op.drop_index("ix_private_targets_normalized_email", table_name="private_targets")
    op.drop_table("private_targets")
