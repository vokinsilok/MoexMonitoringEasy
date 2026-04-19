"""add tbank user credentials table

Revision ID: d9a1e0f4c3b2
Revises: c2a57c6e4a10
Create Date: 2026-04-19 16:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d9a1e0f4c3b2"
down_revision: Union[str, Sequence[str], None] = "c2a57c6e4a10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tbank_user_credentials",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("tbank_token", sa.Text(), nullable=False),
        sa.Column("tbank_account_id", sa.String(length=128), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_tbank_user_credentials_telegram_user_id"),
        "tbank_user_credentials",
        ["telegram_user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_tbank_user_credentials_telegram_user_id"), table_name="tbank_user_credentials")
    op.drop_table("tbank_user_credentials")
