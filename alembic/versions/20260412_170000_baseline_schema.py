"""baseline schema

Revision ID: 20260412_170000
Revises:
Create Date: 2026-04-12 17:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260412_170000"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "auth_permissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_auth_permissions_code"), "auth_permissions", ["code"], unique=True)

    op.create_table(
        "auth_roles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_auth_roles_name"), "auth_roles", ["name"], unique=True)

    op.create_table(
        "auth_users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("role", sa.String(length=32), server_default="user", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_auth_users_username"), "auth_users", ["username"], unique=True)

    op.create_table(
        "moex_shares",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("secid", sa.String(length=32), nullable=False),
        sa.Column("shortname", sa.String(length=255), nullable=True),
        sa.Column("security_name", sa.String(length=512), nullable=True),
        sa.Column("isin", sa.String(length=32), nullable=True),
        sa.Column("regnumber", sa.String(length=64), nullable=True),
        sa.Column("boardid", sa.String(length=32), nullable=True),
        sa.Column("primary_boardid", sa.String(length=32), nullable=True),
        sa.Column("sectype", sa.String(length=64), nullable=True),
        sa.Column("group_name", sa.String(length=128), nullable=True),
        sa.Column("is_traded", sa.Boolean(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("security_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("details_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("details_error", sa.Text(), nullable=True),
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_moex_shares_isin"), "moex_shares", ["isin"], unique=False)
    op.create_index(op.f("ix_moex_shares_secid"), "moex_shares", ["secid"], unique=True)

    op.create_table(
        "auth_role_permissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("permission_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["permission_id"], ["auth_permissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["auth_roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "role_id", "permission_id", name="uq_auth_role_permissions_role_permission"
        ),
    )
    op.create_index(
        op.f("ix_auth_role_permissions_permission_id"),
        "auth_role_permissions",
        ["permission_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_auth_role_permissions_role_id"), "auth_role_permissions", ["role_id"], unique=False
    )

    op.create_table(
        "auth_user_roles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["role_id"], ["auth_roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["auth_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "role_id", name="uq_auth_user_roles_user_role"),
    )
    op.create_index(
        op.f("ix_auth_user_roles_role_id"), "auth_user_roles", ["role_id"], unique=False
    )
    op.create_index(
        op.f("ix_auth_user_roles_user_id"), "auth_user_roles", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_auth_user_roles_user_id"), table_name="auth_user_roles")
    op.drop_index(op.f("ix_auth_user_roles_role_id"), table_name="auth_user_roles")
    op.drop_table("auth_user_roles")

    op.drop_index(op.f("ix_auth_role_permissions_role_id"), table_name="auth_role_permissions")
    op.drop_index(
        op.f("ix_auth_role_permissions_permission_id"), table_name="auth_role_permissions"
    )
    op.drop_table("auth_role_permissions")

    op.drop_index(op.f("ix_moex_shares_secid"), table_name="moex_shares")
    op.drop_index(op.f("ix_moex_shares_isin"), table_name="moex_shares")
    op.drop_table("moex_shares")

    op.drop_index(op.f("ix_auth_users_username"), table_name="auth_users")
    op.drop_table("auth_users")

    op.drop_index(op.f("ix_auth_roles_name"), table_name="auth_roles")
    op.drop_table("auth_roles")

    op.drop_index(op.f("ix_auth_permissions_code"), table_name="auth_permissions")
    op.drop_table("auth_permissions")
