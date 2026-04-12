from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.database import Base


class AuthUserRole(Base):
    __tablename__ = "auth_user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uq_auth_user_roles_user_role"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("auth_users.id", ondelete="CASCADE"), index=True
    )
    role_id: Mapped[int] = mapped_column(
        ForeignKey("auth_roles.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
