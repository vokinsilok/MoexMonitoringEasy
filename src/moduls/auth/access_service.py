from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, status
from fastapi.routing import APIRoute

from src.moduls.auth.access_policy import AccessMode, get_route_access_policy
from src.moduls.auth.auth_user import AuthUser
from src.utils.db_manager import DBManager


@dataclass(slots=True)
class AccessContext:
    user: AuthUser
    roles: set[str]
    permissions: set[str]


class AccessService:
    ROOT_ROLE = "root"
    USER_ROLE = "user"
    USER_PERMISSION_CODES = {
        "auth.refresh",
        "auth.logout",
        "auth.me",
    }

    def __init__(self, db: DBManager) -> None:
        self.db = db

    async def sync_permissions_from_app(self, app: FastAPI) -> None:
        repo = self.db.access
        discovered_permissions: dict[str, str] = {}

        for route in app.routes:
            if not isinstance(route, APIRoute):
                continue
            policy = get_route_access_policy(route)
            if policy.mode is AccessMode.PUBLIC:
                continue

            description = (
                policy.permission_description
                or route.summary
                or f"{','.join(sorted(route.methods or []))} {route.path}"
            )
            discovered_permissions[policy.permission_code] = description

        role_entities = await self._ensure_base_roles()
        permission_entities: dict[str, int] = {}
        for code, description in discovered_permissions.items():
            permission = await repo.get_permission_by_code(code)
            if permission is None:
                permission = await repo.create_permission(code=code, description=description)
            elif permission.description != description:
                permission.description = description
            permission_entities[code] = permission.id

        await repo.replace_role_permissions(
            role_entities[self.ROOT_ROLE], set(permission_entities.values())
        )
        user_permission_ids = {
            permission_entities[code]
            for code in self.USER_PERMISSION_CODES
            if code in permission_entities
        }
        await repo.replace_role_permissions(role_entities[self.USER_ROLE], user_permission_ids)

        users = await self.db.auth_user.get_all()
        for user in users:
            role_names = await repo.get_role_names_for_user(user.id)
            normalized_role = self._normalize_user_role(user.role, set(role_names))
            if normalized_role not in role_names:
                await repo.assign_role_to_user(user.id, role_entities[normalized_role])
            if user.role != normalized_role:
                user.role = normalized_role

    async def assign_role_to_user(self, user_id: int, role_name: str) -> None:
        role = await self.db.access.get_role_by_name(role_name)
        if role is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Role '{role_name}' is not configured",
            )
        await self.db.access.assign_role_to_user(user_id, role.id)

    async def get_access_context(self, user: AuthUser) -> AccessContext:
        roles = set(await self.db.access.get_role_names_for_user(user.id))
        permissions = await self.db.access.get_permission_codes_for_user(user.id)
        return AccessContext(user=user, roles=roles, permissions=permissions)

    async def _ensure_base_roles(self) -> dict[str, int]:
        role_entities: dict[str, int] = {}
        role_specs = {
            self.ROOT_ROLE: "Full access to every protected endpoint",
            self.USER_ROLE: "Base authenticated user role",
        }

        for role_name, description in role_specs.items():
            role = await self.db.access.get_role_by_name(role_name)
            if role is None:
                role = await self.db.access.create_role(name=role_name, description=description)
            elif role.description != description:
                role.description = description
            role_entities[role_name] = role.id

        return role_entities

    def _normalize_user_role(self, legacy_role: str, assigned_roles: set[str]) -> str:
        if self.ROOT_ROLE in assigned_roles or "admin" in assigned_roles:
            return self.ROOT_ROLE
        if legacy_role in {self.ROOT_ROLE, "admin"}:
            return self.ROOT_ROLE
        return self.USER_ROLE
