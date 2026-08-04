"""migrate target tags to bounded notes

Revision ID: 0005_target_notes
Revises: 0004_incremental_sync
Create Date: 2026-08-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision: str = "0005_target_notes"
down_revision: Union[str, None] = "0004_incremental_sync"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    private_columns = {column["name"] for column in inspector.get_columns("private_targets")}
    if "note" not in private_columns:
        op.add_column("private_targets", sa.Column("note", sa.Text(), nullable=True))

    tables = set(inspector.get_table_names())
    if "target_tags" in tables and "tag_id" in private_columns:
        bind.execute(
            text(
                "UPDATE private_targets "
                "SET note = (SELECT name FROM target_tags WHERE target_tags.id = private_targets.tag_id) "
                "WHERE note IS NULL AND tag_id IS NOT NULL"
            )
        )

    inspector = inspect(bind)
    private_indexes = {index["name"] for index in inspector.get_indexes("private_targets")}
    if "ix_private_targets_tag_id" in private_indexes:
        op.drop_index("ix_private_targets_tag_id", table_name="private_targets")

    private_columns = {column["name"] for column in inspect(bind).get_columns("private_targets")}
    if "tag_id" in private_columns:
        with op.batch_alter_table("private_targets", recreate="always") as batch_op:
            batch_op.drop_column("tag_id")

    if "target_tags" in set(inspect(bind).get_table_names()):
        op.drop_table("target_tags")


def downgrade() -> None:
    raise RuntimeError("0005_target_notes is data-migrating and is restored from the pre-migration SQLite backup")
