"""add tbank price session columns

Revision ID: 8e7d1b5c9a2f
Revises: fcc872d62fd2
Create Date: 2026-04-18 16:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8e7d1b5c9a2f"
down_revision: Union[str, Sequence[str], None] = "fcc872d62fd2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tbank_share_prices",
        sa.Column("session_opened_at_msk", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "tbank_share_prices",
        sa.Column("session_closed_at_msk", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "tbank_share_prices",
        sa.Column("next_session_opened_at_msk", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "tbank_share_prices",
        sa.Column("next_session_closed_at_msk", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tbank_share_prices", "next_session_closed_at_msk")
    op.drop_column("tbank_share_prices", "next_session_opened_at_msk")
    op.drop_column("tbank_share_prices", "session_closed_at_msk")
    op.drop_column("tbank_share_prices", "session_opened_at_msk")
