from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.database import Base


class TBankShare(Base):
    __tablename__ = "tbank_shares"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    figi: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    instrument_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="share",
        server_default="share",
        index=True,
    )
    ticker: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    class_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    isin: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    instrument_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(64), nullable=True)
    country_of_risk: Mapped[str | None] = mapped_column(String(16), nullable=True)
    buy_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sell_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    api_trade_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    short_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    real_exchange: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class TBankUserCredential(Base):
    __tablename__ = "tbank_user_credentials"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    tbank_token: Mapped[str] = mapped_column(Text, nullable=False)
    tbank_account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class TBankFavoriteShare(Base):
    __tablename__ = "tbank_favorite_shares"
    __table_args__ = (UniqueConstraint("telegram_user_id", "figi", name="uq_tbank_favorite_shares_user_figi"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    figi: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class TBankBotAccessUser(Base):
    __tablename__ = "tbank_bot_access_users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    revoked_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class TBankPriceMonitor(Base):
    __tablename__ = "tbank_price_monitors"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    figi: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    ticker: Mapped[str | None] = mapped_column(String(32), nullable=True)
    instrument_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    interval_seconds: Mapped[int] = mapped_column(nullable=False, default=300)
    threshold_percent: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    threshold_rub: Mapped[Decimal] = mapped_column(Numeric(20, 9), nullable=False)
    base_price: Mapped[Decimal] = mapped_column(Numeric(20, 9), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    alert_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    last_checked_at_msk: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    last_notified_at_msk: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class TBankCalendarNotificationSetting(Base):
    __tablename__ = "tbank_calendar_notification_settings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    days_before: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default="3")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class TBankCalendarNotificationLog(Base):
    __tablename__ = "tbank_calendar_notification_logs"
    __table_args__ = (
        UniqueConstraint(
            "telegram_user_id",
            "figi",
            "event_type",
            "event_date",
            "days_before",
            name="uq_tbank_calendar_notification_once",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    figi: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    days_before: Mapped[int] = mapped_column(Integer, nullable=False)
    notified_at_msk: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
