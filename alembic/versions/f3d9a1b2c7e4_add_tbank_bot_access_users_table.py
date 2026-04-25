"""add tbank bot access users table

Revision ID: f3d9a1b2c7e4
Revises: c9b2d4e8f1a7
Create Date: 2026-04-25 21:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f3d9a1b2c7e4"
down_revision: Union[str, Sequence[str], None] = "c9b2d4e8f1a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tbank_bot_access_users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=True),
        sa.Column("first_name", sa.String(length=256), nullable=True),
        sa.Column("last_name", sa.String(length=256), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.BigInteger(), nullable=True),
        sa.Column("revoked_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_user_id", name="uq_tbank_bot_access_users_telegram_user_id"),
    )
    op.create_index(
        op.f("ix_tbank_bot_access_users_telegram_user_id"),
        "tbank_bot_access_users",
        ["telegram_user_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_tbank_bot_access_users_status"),
        "tbank_bot_access_users",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_tbank_bot_access_users_status"), table_name="tbank_bot_access_users")
    op.drop_index(op.f("ix_tbank_bot_access_users_telegram_user_id"), table_name="tbank_bot_access_users")
    op.drop_table("tbank_bot_access_users")

