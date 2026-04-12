from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from src.moduls.auth.auth_permission import AuthPermission
from src.moduls.auth.auth_role import AuthRole
from src.moduls.auth.auth_role_permission import AuthRolePermission
from src.moduls.auth.auth_user_role import AuthUserRole


class AccessRepository:
    def __init__(self, session) -> None:
        self.session = session

    async def get_role_by_name(self, name: str) -> AuthRole | None:
        stmt = select(AuthRole).where(AuthRole.name == name)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_permission_by_code(self, code: str) -> AuthPermission | None:
        stmt = select(AuthPermission).where(AuthPermission.code == code)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_role(self, name: str, description: str | None = None) -> AuthRole:
        role = AuthRole(name=name, description=description)
        self.session.add(role)
        await self.session.flush()
        return role

    async def create_permission(self, code: str, description: str | None = None) -> AuthPermission:
        permission = AuthPermission(code=code, description=description)
        self.session.add(permission)
        await self.session.flush()
        return permission

    async def assign_role_to_user(self, user_id: int, role_id: int) -> None:
        stmt = (
            insert(AuthUserRole).values(user_id=user_id, role_id=role_id).on_conflict_do_nothing()
        )
        await self.session.execute(stmt)

    async def assign_permission_to_role(self, role_id: int, permission_id: int) -> None:
        stmt = (
            insert(AuthRolePermission)
            .values(role_id=role_id, permission_id=permission_id)
            .on_conflict_do_nothing()
        )
        await self.session.execute(stmt)

    async def replace_role_permissions(self, role_id: int, permission_ids: set[int]) -> None:
        for permission_id in permission_ids:
            await self.assign_permission_to_role(role_id, permission_id)

        stmt = delete(AuthRolePermission).where(AuthRolePermission.role_id == role_id)
        if permission_ids:
            stmt = stmt.where(~AuthRolePermission.permission_id.in_(permission_ids))
        await self.session.execute(stmt)

    async def get_role_names_for_user(self, user_id: int) -> list[str]:
        stmt = (
            select(AuthRole.name)
            .join(AuthUserRole, AuthUserRole.role_id == AuthRole.id)
            .where(AuthUserRole.user_id == user_id)
            .order_by(AuthRole.name.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_permission_codes_for_user(self, user_id: int) -> set[str]:
        stmt = (
            select(AuthPermission.code)
            .join(AuthRolePermission, AuthRolePermission.permission_id == AuthPermission.id)
            .join(AuthUserRole, AuthUserRole.role_id == AuthRolePermission.role_id)
            .where(AuthUserRole.user_id == user_id)
        )
        result = await self.session.execute(stmt)
        return set(result.scalars().all())
