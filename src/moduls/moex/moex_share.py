from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.database import Base


class MoexShare(Base):
    __tablename__ = "moex_shares"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    secid: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    shortname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    security_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    isin: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    regnumber: Mapped[str | None] = mapped_column(String(64), nullable=True)
    boardid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    primary_boardid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sectype: Mapped[str | None] = mapped_column(String(64), nullable=True)
    group_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_traded: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    security_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    details_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    details_error: Mapped[str | None] = mapped_column(Text, nullable=True)
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
