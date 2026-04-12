import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from src.core.config import settings


SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    password_hash = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    salt_b64 = base64.b64encode(salt).decode("ascii")
    hash_b64 = base64.b64encode(password_hash).decode("ascii")
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt_b64}${hash_b64}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, n_value, r_value, p_value, salt_b64, hash_b64 = stored_hash.split(
            "$", maxsplit=5
        )
    except ValueError:
        return False

    if algorithm != "scrypt":
        return False

    salt = base64.b64decode(salt_b64.encode("ascii"))
    expected_hash = base64.b64decode(hash_b64.encode("ascii"))
    candidate_hash = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=int(n_value),
        r=int(r_value),
        p=int(p_value),
        dklen=len(expected_hash),
    )
    return hmac.compare_digest(candidate_hash, expected_hash)


def create_access_token(user_id: int, username: str, role: str) -> tuple[str, str, datetime]:
    return _create_token(
        token_type="access",
        user_id=user_id,
        username=username,
        role=role,
        expires_delta=timedelta(minutes=settings.AUTH_ACCESS_TOKEN_MINUTES),
    )


def create_refresh_token(user_id: int, username: str, role: str) -> tuple[str, str, datetime]:
    return _create_token(
        token_type="refresh",
        user_id=user_id,
        username=username,
        role=role,
        expires_delta=timedelta(days=settings.AUTH_REFRESH_TOKEN_DAYS),
    )


def decode_token(token: str, expected_type: str | None = None) -> dict[str, Any]:
    payload = jwt.decode(
        token,
        settings.AUTH_JWT_SECRET,
        algorithms=[settings.AUTH_JWT_ALGORITHM],
    )
    if expected_type is not None and payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(f"Unexpected token type: {payload.get('type')}")
    return payload


def seconds_until(expiration: datetime) -> int:
    now = datetime.now(UTC)
    delta = expiration - now
    return max(int(delta.total_seconds()), 1)


def _create_token(
    token_type: str,
    user_id: int,
    username: str,
    role: str,
    expires_delta: timedelta,
) -> tuple[str, str, datetime]:
    issued_at = datetime.now(UTC)
    expires_at = issued_at + expires_delta
    jti = uuid.uuid4().hex
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "type": token_type,
        "jti": jti,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(
        payload,
        settings.AUTH_JWT_SECRET,
        algorithm=settings.AUTH_JWT_ALGORITHM,
    )
    return token, jti, expires_at
