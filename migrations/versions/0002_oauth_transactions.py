"""add encrypted temporary Microsoft OAuth transaction state

Revision ID: 0002_oauth_transactions
Revises: 0001_initial
Create Date: 2026-08-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_oauth_transactions"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "oauth_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=True),
        sa.Column("flow_type", sa.String(length=16), nullable=False),
        sa.Column("payload_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("transaction_id"),
    )
    op.create_index("ix_oauth_transactions_transaction_id", "oauth_transactions", ["transaction_id"])
    op.create_index("ix_oauth_transactions_state_hash", "oauth_transactions", ["state_hash"])
    op.create_index("ix_oauth_transactions_expires_at", "oauth_transactions", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_oauth_transactions_expires_at", table_name="oauth_transactions")
    op.drop_index("ix_oauth_transactions_state_hash", table_name="oauth_transactions")
    op.drop_index("ix_oauth_transactions_transaction_id", table_name="oauth_transactions")
    op.drop_table("oauth_transactions")