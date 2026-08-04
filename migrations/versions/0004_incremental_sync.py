"""add per-folder incremental sync cursors

Revision ID: 0004_incremental_sync
Revises: 0003_target_tags
Create Date: 2026-08-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0004_incremental_sync"
down_revision: Union[str, None] = "0003_target_tags"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("mail_folders")}
    if "last_message_received_at" not in columns:
        op.add_column(
            "mail_folders",
            sa.Column("last_message_received_at", sa.DateTime(), nullable=True),
        )
    if "last_message_id" not in columns:
        op.add_column(
            "mail_folders",
            sa.Column("last_message_id", sa.String(length=512), nullable=True),
        )

    index_names = {index["name"] for index in inspect(bind).get_indexes("mail_folders")}
    if "ix_mail_folders_last_message_received_at" not in index_names:
        op.create_index(
            "ix_mail_folders_last_message_received_at",
            "mail_folders",
            ["last_message_received_at"],
        )


def downgrade() -> None:
    op.drop_index("ix_mail_folders_last_message_received_at", table_name="mail_folders")
    op.drop_column("mail_folders", "last_message_id")
    op.drop_column("mail_folders", "last_message_received_at")
