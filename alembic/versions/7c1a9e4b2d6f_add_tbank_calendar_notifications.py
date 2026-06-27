"""add tbank calendar notifications

Revision ID: 7c1a9e4b2d6f
Revises: 2f7b9d4c1a80
Create Date: 2026-06-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7c1a9e4b2d6f"
down_revision: Union[str, Sequence[str], None] = "2f7b9d4c1a80"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tbank_calendar_notification_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("days_before", sa.Integer(), server_default="3", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_tbank_calendar_notification_settings_telegram_user_id"),
        "tbank_calendar_notification_settings",
        ["telegram_user_id"],
        unique=True,
    )

    op.create_table(
        "tbank_calendar_notification_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("figi", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("days_before", sa.Integer(), nullable=False),
        sa.Column("notified_at_msk", sa.DateTime(timezone=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "telegram_user_id",
            "figi",
            "event_type",
            "event_date",
            "days_before",
            name="uq_tbank_calendar_notification_once",
        ),
    )
    op.create_index(
        op.f("ix_tbank_calendar_notification_logs_telegram_user_id"),
        "tbank_calendar_notification_logs",
        ["telegram_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_tbank_calendar_notification_logs_figi"),
        "tbank_calendar_notification_logs",
        ["figi"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_tbank_calendar_notification_logs_figi"), table_name="tbank_calendar_notification_logs")
    op.drop_index(
        op.f("ix_tbank_calendar_notification_logs_telegram_user_id"),
        table_name="tbank_calendar_notification_logs",
    )
    op.drop_table("tbank_calendar_notification_logs")
    op.drop_index(
        op.f("ix_tbank_calendar_notification_settings_telegram_user_id"),
        table_name="tbank_calendar_notification_settings",
    )
    op.drop_table("tbank_calendar_notification_settings")
