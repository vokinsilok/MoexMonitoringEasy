"""add alert_active to tbank price monitors

Revision ID: 1b4e9d2c7f31
Revises: f3d9a1b2c7e4
Create Date: 2026-05-24 23:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "1b4e9d2c7f31"
down_revision: Union[str, Sequence[str], None] = "f3d9a1b2c7e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tbank_price_monitors", schema=None) as batch_op:
        batch_op.add_column(sa.Column("alert_active", sa.Boolean(), server_default="false", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("tbank_price_monitors", schema=None) as batch_op:
        batch_op.drop_column("alert_active")

