"""add day/year metrics to tbank share prices

Revision ID: c2a57c6e4a10
Revises: 8e7d1b5c9a2f
Create Date: 2026-04-18 16:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c2a57c6e4a10"
down_revision: Union[str, Sequence[str], None] = "8e7d1b5c9a2f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tbank_share_prices",
        sa.Column("day_open_price", sa.Numeric(precision=20, scale=9), nullable=True),
    )
    op.add_column(
        "tbank_share_prices",
        sa.Column("day_close_price", sa.Numeric(precision=20, scale=9), nullable=True),
    )
    op.add_column(
        "tbank_share_prices",
        sa.Column("day_change_percent", sa.Numeric(precision=10, scale=4), nullable=True),
    )
    op.add_column(
        "tbank_share_prices",
        sa.Column("year_change_percent", sa.Numeric(precision=10, scale=4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tbank_share_prices", "year_change_percent")
    op.drop_column("tbank_share_prices", "day_change_percent")
    op.drop_column("tbank_share_prices", "day_close_price")
    op.drop_column("tbank_share_prices", "day_open_price")
