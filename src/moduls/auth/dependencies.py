from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials

from src.moduls.auth.access_dependencies import (
    AccessContextDep,
    CurrentUserDep,
    RefreshAccessContextDep,
    bearer_scheme,
    get_access_context,
    get_current_user,
    get_refresh_access_context,
    get_refresh_user,
    require_bearer_token,
    require_permission,
    require_refresh_permission,
    require_refresh_token,
)
from src.moduls.auth.auth_user import AuthUser


async def require_active_user(access: AccessContextDep) -> AuthUser:
    return access.user


async def require_admin(access: AccessContextDep) -> AuthUser:
    if "root" not in access.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Root role is required",
        )
    return access.user


__all__ = [
    "AccessContextDep",
    "CurrentUserDep",
    "HTTPAuthorizationCredentials",
    "RefreshAccessContextDep",
    "bearer_scheme",
    "get_access_context",
    "get_current_user",
    "get_refresh_access_context",
    "get_refresh_user",
    "require_active_user",
    "require_admin",
    "require_bearer_token",
    "require_permission",
    "require_refresh_permission",
    "require_refresh_token",
]
