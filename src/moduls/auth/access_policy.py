from dataclasses import dataclass
from enum import Enum

from fastapi.routing import APIRoute

ACCESS_CONTROL_OPENAPI_KEY = "x-access-control"


class AccessMode(str, Enum):
    PUBLIC = "public"
    ACCESS = "access"
    REFRESH = "refresh"


@dataclass(slots=True)
class RouteAccessPolicy:
    mode: AccessMode
    permission_code: str | None = None
    permission_description: str | None = None


def public_access_meta() -> dict[str, dict[str, str]]:
    return {ACCESS_CONTROL_OPENAPI_KEY: {"mode": AccessMode.PUBLIC.value}}


def access_permission_meta(
    permission_code: str, description: str | None = None
) -> dict[str, dict[str, str]]:
    meta = {
        "mode": AccessMode.ACCESS.value,
        "permission_code": permission_code,
    }
    if description:
        meta["permission_description"] = description
    return {ACCESS_CONTROL_OPENAPI_KEY: meta}


def refresh_permission_meta(
    permission_code: str, description: str | None = None
) -> dict[str, dict[str, str]]:
    meta = {
        "mode": AccessMode.REFRESH.value,
        "permission_code": permission_code,
    }
    if description:
        meta["permission_description"] = description
    return {ACCESS_CONTROL_OPENAPI_KEY: meta}


def get_route_access_policy(route: APIRoute) -> RouteAccessPolicy:
    openapi_extra = route.openapi_extra or {}
    raw_meta = openapi_extra.get(ACCESS_CONTROL_OPENAPI_KEY)
    if raw_meta is None:
        raise RuntimeError(f"Route '{route.path}' must define access metadata")

    try:
        mode = AccessMode(raw_meta["mode"])
    except Exception as exc:
        raise RuntimeError(f"Route '{route.path}' has invalid access mode") from exc

    permission_code = raw_meta.get("permission_code")
    permission_description = raw_meta.get("permission_description")
    if mode is not AccessMode.PUBLIC and not permission_code:
        raise RuntimeError(f"Route '{route.path}' must define a permission code")

    return RouteAccessPolicy(
        mode=mode,
        permission_code=permission_code,
        permission_description=permission_description,
    )
