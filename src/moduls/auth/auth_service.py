from fastapi import HTTPException, status

from src.init import redis_manager
from src.moduls.auth.access_service import AccessService
from src.moduls.auth.auth_user import AuthUser
from src.moduls.auth.schemas import AuthTokensResponse, UserResponse
from src.moduls.auth.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    seconds_until,
    verify_password,
)
from src.utils.db_manager import DBManager


class AuthService:
    REFRESH_PREFIX = "auth:refresh"
    ACCESS_BLACKLIST_PREFIX = "auth:access:blacklist"

    def __init__(self, db: DBManager) -> None:
        self.db = db

    async def register_user(self, username: str, password: str) -> UserResponse:
        normalized_username = username.strip()
        if not normalized_username:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Username cannot be empty"
            )

        repo = self.db.auth_user
        existing_user = await repo.get_by_username(normalized_username)
        if existing_user is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User with this username already exists",
            )

        role = "root" if await repo.count_users() == 0 else "user"
        user = AuthUser(
            username=normalized_username,
            password_hash=hash_password(password),
            role=role,
            is_active=True,
        )
        self.db.session.add(user)
        await self.db.session.flush()
        await self.db.session.refresh(user)
        await AccessService(self.db).assign_role_to_user(user.id, role)

        return UserResponse.model_validate(user)

    async def login(self, username: str, password: str) -> AuthTokensResponse:
        user = await self.db.auth_user.get_by_username(username.strip())

        if user is None or not verify_password(password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
            )

        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is inactive",
            )

        return await self._issue_tokens(user)

    async def refresh_tokens(self, refresh_token: str) -> AuthTokensResponse:
        user, payload = await self._get_refresh_user_and_payload(refresh_token)
        jti = str(payload["jti"])
        await redis_manager.delete(self._refresh_key(jti))
        return await self._issue_tokens(user)

    async def logout(self, access_token: str, refresh_token: str | None = None) -> None:
        access_payload = self._decode_with_http_error(access_token, expected_type="access")
        access_jti = str(access_payload["jti"])
        access_exp = int(access_payload["exp"])

        await redis_manager.set(
            self._access_blacklist_key(access_jti),
            "1",
            expire=max(access_exp - self._unix_now(), 1),
        )

        if refresh_token:
            refresh_payload = self._decode_with_http_error(refresh_token, expected_type="refresh")
            await redis_manager.delete(self._refresh_key(str(refresh_payload["jti"])))

    async def get_user_from_access_token(self, token: str) -> AuthUser:
        payload = self._decode_with_http_error(token, expected_type="access")
        if await self._redis_get(self._access_blacklist_key(str(payload["jti"]))) is not None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Access token is revoked",
            )

        user_id = int(payload["sub"])
        user = await self.db.auth_user.get(user_id)

        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )
        return user

    async def get_user_from_refresh_token(self, refresh_token: str) -> AuthUser:
        user, _ = await self._get_refresh_user_and_payload(refresh_token)
        return user

    async def _issue_tokens(self, user: AuthUser) -> AuthTokensResponse:
        access_token, _, _ = create_access_token(user.id, user.username, user.role)
        refresh_token, refresh_jti, refresh_expiration = create_refresh_token(
            user.id, user.username, user.role
        )
        await redis_manager.set(
            self._refresh_key(refresh_jti),
            str(user.id),
            expire=seconds_until(refresh_expiration),
        )
        return AuthTokensResponse(access_token=access_token, refresh_token=refresh_token)

    @staticmethod
    def _decode_with_http_error(token: str, expected_type: str) -> dict:
        try:
            return decode_token(token, expected_type=expected_type)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
            ) from exc

    @staticmethod
    async def _redis_get(key: str) -> str | None:
        value = await redis_manager.get(key)
        if value is None:
            return None
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)

    @classmethod
    def _refresh_key(cls, jti: str) -> str:
        return f"{cls.REFRESH_PREFIX}:{jti}"

    @classmethod
    def _access_blacklist_key(cls, jti: str) -> str:
        return f"{cls.ACCESS_BLACKLIST_PREFIX}:{jti}"

    async def _get_refresh_user_and_payload(self, refresh_token: str) -> tuple[AuthUser, dict]:
        payload = self._decode_with_http_error(refresh_token, expected_type="refresh")
        user_id = int(payload["sub"])
        jti = str(payload["jti"])

        stored_user_id = await self._redis_get(self._refresh_key(jti))
        if stored_user_id != str(user_id):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token is expired or revoked",
            )

        user = await self.db.auth_user.get(user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is inactive",
            )
        return user, payload

    @staticmethod
    def _unix_now() -> int:
        from time import time

        return int(time())
