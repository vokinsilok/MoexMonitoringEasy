from json import JSONDecodeError
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.moduls.auth.access_service import AccessContext, AccessService
from src.moduls.auth.auth_service import AuthService
from src.moduls.auth.auth_user import AuthUser
from src.db.dependencies import DBDep

bearer_scheme = HTTPBearer(auto_error=False)


def require_bearer_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> HTTPAuthorizationCredentials:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization bearer token is required",
        )

    if credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must use Bearer scheme",
        )

    token = credentials.credentials.strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization bearer token is empty",
        )

    return credentials


async def get_current_user(
    db: DBDep,
    credentials: HTTPAuthorizationCredentials = Depends(require_bearer_token),
) -> AuthUser:
    auth_service = AuthService(db=db)
    return await auth_service.get_user_from_access_token(credentials.credentials)


async def get_access_context(
    db: DBDep,
    user: AuthUser = Depends(get_current_user),
) -> AccessContext:
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is inactive",
        )

    access_service = AccessService(db)
    return await access_service.get_access_context(user)


async def require_refresh_token(request: Request) -> str:
    try:
        payload = await request.json()
    except JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body must be a JSON object",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body must be a JSON object",
        )

    raw_refresh_token = payload.get("refresh_token")
    if not isinstance(raw_refresh_token, str) or not raw_refresh_token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token is required",
        )

    return raw_refresh_token.strip()


async def get_refresh_user(
    db: DBDep,
    refresh_token: str = Depends(require_refresh_token),
) -> AuthUser:
    auth_service = AuthService(db=db)
    return await auth_service.get_user_from_refresh_token(refresh_token)


async def get_refresh_access_context(
    db: DBDep,
    user: AuthUser = Depends(get_refresh_user),
) -> AccessContext:
    access_service = AccessService(db)
    return await access_service.get_access_context(user)


AccessContextDep = Annotated[AccessContext, Depends(get_access_context)]
CurrentUserDep = Annotated[AuthUser, Depends(get_current_user)]
RefreshAccessContextDep = Annotated[AccessContext, Depends(get_refresh_access_context)]


def require_permission(permission_code: str):
    async def dependency(access: AccessContextDep) -> AccessContext:
        if permission_code not in access.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{permission_code}' is required",
            )
        return access

    return dependency


def require_refresh_permission(permission_code: str):
    async def dependency(access: RefreshAccessContextDep) -> AccessContext:
        if permission_code not in access.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{permission_code}' is required",
            )
        return access

    return dependency
