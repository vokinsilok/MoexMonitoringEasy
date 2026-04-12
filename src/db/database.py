from sqlalchemy import NullPool
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from src.core.config import settings

DATABASE_URL = settings.db_url
engine = create_async_engine(
    DATABASE_URL,
    pool_size=20,
    max_overflow=20,
    pool_timeout=60,
    pool_pre_ping=True,
    connect_args={"server_settings": {"client_encoding": "utf8"}},
)
engine_null_pool = create_async_engine(
    DATABASE_URL, poolclass=NullPool, connect_args={"server_settings": {"client_encoding": "utf8"}}
)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False)
async_session_maker_null_pool = async_sessionmaker(bind=engine_null_pool, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def load_db_models() -> None:
    from src.moduls.auth.auth_permission import AuthPermission  # noqa: F401
    from src.moduls.auth.auth_role import AuthRole  # noqa: F401
    from src.moduls.auth.auth_role_permission import AuthRolePermission  # noqa: F401
    from src.moduls.auth.auth_user import AuthUser  # noqa: F401
    from src.moduls.auth.auth_user_role import AuthUserRole  # noqa: F401
    from src.moduls.moex.moex_share import MoexShare  # noqa: F401
