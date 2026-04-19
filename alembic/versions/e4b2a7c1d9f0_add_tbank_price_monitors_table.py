"""add tbank price monitors table

Revision ID: e4b2a7c1d9f0
Revises: d9a1e0f4c3b2
Create Date: 2026-04-19 17:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e4b2a7c1d9f0"
down_revision: Union[str, Sequence[str], None] = "d9a1e0f4c3b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tbank_price_monitors",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("figi", sa.String(length=64), nullable=False),
        sa.Column("ticker", sa.String(length=32), nullable=True),
        sa.Column("instrument_name", sa.String(length=512), nullable=True),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("threshold_percent", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("threshold_rub", sa.Numeric(precision=20, scale=9), nullable=False),
        sa.Column("base_price", sa.Numeric(precision=20, scale=9), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("last_checked_at_msk", sa.DateTime(), nullable=True),
        sa.Column("last_notified_at_msk", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tbank_price_monitors_telegram_user_id"), "tbank_price_monitors", ["telegram_user_id"], unique=False)
    op.create_index(op.f("ix_tbank_price_monitors_figi"), "tbank_price_monitors", ["figi"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_tbank_price_monitors_figi"), table_name="tbank_price_monitors")
    op.drop_index(op.f("ix_tbank_price_monitors_telegram_user_id"), table_name="tbank_price_monitors")
    op.drop_table("tbank_price_monitors")
