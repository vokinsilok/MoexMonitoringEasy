"""switch monitor interval to seconds

Revision ID: c9b2d4e8f1a7
Revises: b7d3f1a2c4e6
Create Date: 2026-04-22 20:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c9b2d4e8f1a7"
down_revision: Union[str, Sequence[str], None] = "b7d3f1a2c4e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tbank_price_monitors", schema=None) as batch_op:
        batch_op.add_column(sa.Column("interval_seconds", sa.Integer(), nullable=True))

    op.execute(
        sa.text(
            """
            UPDATE tbank_price_monitors
            SET interval_seconds = CASE
                WHEN interval_minutes IS NULL OR interval_minutes <= 0 THEN 300
                ELSE interval_minutes * 60
            END
            """
        )
    )

    with op.batch_alter_table("tbank_price_monitors", schema=None) as batch_op:
        batch_op.alter_column("interval_seconds", existing_type=sa.Integer(), nullable=False)
        batch_op.drop_column("interval_minutes")


def downgrade() -> None:
    with op.batch_alter_table("tbank_price_monitors", schema=None) as batch_op:
        batch_op.add_column(sa.Column("interval_minutes", sa.Integer(), nullable=True))

    op.execute(
        sa.text(
            """
            UPDATE tbank_price_monitors
            SET interval_minutes = CASE
                WHEN interval_seconds IS NULL OR interval_seconds <= 0 THEN 5
                ELSE (interval_seconds + 59) / 60
            END
            """
        )
    )

    with op.batch_alter_table("tbank_price_monitors", schema=None) as batch_op:
        batch_op.alter_column("interval_minutes", existing_type=sa.Integer(), nullable=False)
        batch_op.drop_column("interval_seconds")

