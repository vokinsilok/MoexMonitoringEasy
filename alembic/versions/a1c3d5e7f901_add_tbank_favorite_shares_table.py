"""add tbank favorite shares table

Revision ID: a1c3d5e7f901
Revises: e4b2a7c1d9f0
Create Date: 2026-04-19 21:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1c3d5e7f901"
down_revision: Union[str, Sequence[str], None] = "e4b2a7c1d9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tbank_favorite_shares",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("figi", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_user_id", "figi", name="uq_tbank_favorite_shares_user_figi"),
    )
    op.create_index(op.f("ix_tbank_favorite_shares_telegram_user_id"), "tbank_favorite_shares", ["telegram_user_id"], unique=False)
    op.create_index(op.f("ix_tbank_favorite_shares_figi"), "tbank_favorite_shares", ["figi"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_tbank_favorite_shares_figi"), table_name="tbank_favorite_shares")
    op.drop_index(op.f("ix_tbank_favorite_shares_telegram_user_id"), table_name="tbank_favorite_shares")
    op.drop_table("tbank_favorite_shares")
