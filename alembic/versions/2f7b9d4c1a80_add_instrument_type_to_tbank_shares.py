"""add instrument_type to tbank_shares

Revision ID: 2f7b9d4c1a80
Revises: 1b4e9d2c7f31
Create Date: 2026-06-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2f7b9d4c1a80"
down_revision: Union[str, Sequence[str], None] = "1b4e9d2c7f31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tbank_shares",
        sa.Column(
            "instrument_type",
            sa.String(length=16),
            nullable=False,
            server_default="share",
        ),
    )
    op.create_index(
        op.f("ix_tbank_shares_instrument_type"),
        "tbank_shares",
        ["instrument_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_tbank_shares_instrument_type"), table_name="tbank_shares")
    op.drop_column("tbank_shares", "instrument_type")
