from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials

from src.db.dependencies import AtomicDBDep, DBDep
from src.moduls.auth.access_dependencies import (
    require_bearer_token,
    require_permission,
    require_refresh_permission,
)
from src.moduls.auth.access_policy import (
    access_permission_meta,
    public_access_meta,
    refresh_permission_meta,
)
from src.moduls.auth.access_service import AccessContext
from src.moduls.auth.auth_service import AuthService
from src.moduls.auth.schemas import (
    AuthTokensResponse,
    LoginRequest,
    LogoutRequest,
    RefreshTokenRequest,
    RegisterUserRequest,
    UserResponse,
)

auth_router = APIRouter(prefix="/auth", tags=["Auth"])


@auth_router.post(
    "/register",
    response_model=UserResponse,
    summary="Создать нового пользователя",
    openapi_extra=public_access_meta(),
)
async def register_user(
    payload: RegisterUserRequest,
    db: AtomicDBDep,
) -> UserResponse:
    return await AuthService(db).register_user(payload.username, payload.password)


@auth_router.post(
    "/login",
    response_model=AuthTokensResponse,
    summary="Войти и получить access/refresh токены",
    openapi_extra=public_access_meta(),
)
async def login(
    payload: LoginRequest,
    db: DBDep,
) -> AuthTokensResponse:
    return await AuthService(db).login(payload.username, payload.password)


@auth_router.post(
    "/refresh",
    response_model=AuthTokensResponse,
    summary="Обновить пару токенов",
    openapi_extra=refresh_permission_meta("auth.refresh", "Refresh current session tokens"),
)
async def refresh(
    payload: RefreshTokenRequest,
    db: DBDep,
    _: AccessContext = Depends(require_refresh_permission("auth.refresh")),
) -> AuthTokensResponse:
    return await AuthService(db).refresh_tokens(payload.refresh_token)


@auth_router.post(
    "/logout",
    summary="Выйти и отозвать токены",
    openapi_extra=access_permission_meta("auth.logout", "Logout current session"),
)
async def logout(
    payload: LogoutRequest,
    db: DBDep,
    credentials: HTTPAuthorizationCredentials = Depends(require_bearer_token),
    _: AccessContext = Depends(require_permission("auth.logout")),
) -> dict[str, str]:
    await AuthService(db).logout(
        access_token=credentials.credentials,
        refresh_token=payload.refresh_token,
    )
    return {"status": "ok"}


@auth_router.get(
    "/me",
    response_model=UserResponse,
    summary="Получить текущего пользователя",
    openapi_extra=access_permission_meta("auth.me", "Read current user profile"),
)
async def me(
    access: AccessContext = Depends(require_permission("auth.me")),
) -> UserResponse:
    return UserResponse.model_validate(access.user)
