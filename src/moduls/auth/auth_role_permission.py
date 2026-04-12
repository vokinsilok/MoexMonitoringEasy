from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.database import Base


class AuthRolePermission(Base):
    __tablename__ = "auth_role_permissions"
    __table_args__ = (
        UniqueConstraint(
            "role_id", "permission_id", name="uq_auth_role_permissions_role_permission"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    role_id: Mapped[int] = mapped_column(
        ForeignKey("auth_roles.id", ondelete="CASCADE"), index=True
    )
    permission_id: Mapped[int] = mapped_column(
        ForeignKey("auth_permissions.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
