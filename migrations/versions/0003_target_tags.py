"""add single target tags

Revision ID: 0003_target_tags
Revises: 0002_oauth_transactions
Create Date: 2026-08-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0003_target_tags"
down_revision: Union[str, None] = "0002_oauth_transactions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "target_tags" not in tables:
        op.create_table(
            "target_tags",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=64), nullable=False),
            sa.Column("normalized_name", sa.String(length=64), nullable=False),
            sa.Column("color", sa.String(length=7), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("normalized_name"),
        )

    private_columns = {column["name"] for column in inspector.get_columns("private_targets")}
    if "tag_id" not in private_columns:
        op.add_column("private_targets", sa.Column("tag_id", sa.Integer(), nullable=True))

    index_names = {index["name"] for index in inspect(bind).get_indexes("private_targets")}
    if "ix_private_targets_tag_id" not in index_names:
        op.create_index("ix_private_targets_tag_id", "private_targets", ["tag_id"])


def downgrade() -> None:
    op.drop_index("ix_private_targets_tag_id", table_name="private_targets")
    op.drop_column("private_targets", "tag_id")
    op.drop_table("target_tags")
