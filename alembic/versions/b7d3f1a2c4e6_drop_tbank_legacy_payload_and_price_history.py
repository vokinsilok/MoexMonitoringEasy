"""drop legacy tbank payload and price history

Revision ID: b7d3f1a2c4e6
Revises: a1c3d5e7f901
Create Date: 2026-04-19 23:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "b7d3f1a2c4e6"
down_revision: Union[str, Sequence[str], None] = "a1c3d5e7f901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    table_names = set(inspector.get_table_names())
    if "tbank_share_prices" in table_names:
        op.drop_index(op.f("ix_tbank_share_prices_share_id"), table_name="tbank_share_prices")
        op.drop_index(op.f("ix_tbank_share_prices_captured_at_msk"), table_name="tbank_share_prices")
        op.drop_table("tbank_share_prices")

    share_columns = {column["name"] for column in inspector.get_columns("tbank_shares")}
    if "instrument_payload" in share_columns:
        op.drop_column("tbank_shares", "instrument_payload")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    share_columns = {column["name"] for column in inspector.get_columns("tbank_shares")}
    if "instrument_payload" not in share_columns:
        op.add_column(
            "tbank_shares",
            sa.Column(
                "instrument_payload",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        )
        op.alter_column("tbank_shares", "instrument_payload", server_default=None)

    table_names = set(inspector.get_table_names())
    if "tbank_share_prices" not in table_names:
        op.create_table(
            "tbank_share_prices",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("share_id", sa.Integer(), nullable=False),
            sa.Column("price", sa.Numeric(precision=20, scale=9), nullable=False),
            sa.Column("trading_open", sa.Boolean(), server_default="true", nullable=False),
            sa.Column("session_opened_at_msk", sa.DateTime(), nullable=True),
            sa.Column("session_closed_at_msk", sa.DateTime(), nullable=True),
            sa.Column("next_session_opened_at_msk", sa.DateTime(), nullable=True),
            sa.Column("next_session_closed_at_msk", sa.DateTime(), nullable=True),
            sa.Column("day_open_price", sa.Numeric(precision=20, scale=9), nullable=True),
            sa.Column("day_close_price", sa.Numeric(precision=20, scale=9), nullable=True),
            sa.Column("day_change_percent", sa.Numeric(precision=10, scale=4), nullable=True),
            sa.Column("year_change_percent", sa.Numeric(precision=10, scale=4), nullable=True),
            sa.Column("captured_at_msk", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["share_id"], ["tbank_shares.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_tbank_share_prices_captured_at_msk"), "tbank_share_prices", ["captured_at_msk"], unique=False)
        op.create_index(op.f("ix_tbank_share_prices_share_id"), "tbank_share_prices", ["share_id"], unique=False)
