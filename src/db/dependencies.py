from typing import Annotated, AsyncGenerator

from fastapi import Depends, Query
from pydantic import BaseModel

from src.db.database import async_session_maker, async_session_maker_null_pool
from src.utils.db_manager import DBManager


class PaginationParams(BaseModel):
    page: Annotated[int | None, Query(1, ge=1)] = 1
    per_page: Annotated[int | None, Query(None, ge=1, lt=30)] = None


PaginationDep = Annotated[PaginationParams, Depends()]


def get_db_manager() -> type[DBManager]:
    return DBManager


async def get_db() -> AsyncGenerator[DBManager, None]:
    async with DBManager(session_factory=async_session_maker) as db:
        yield db


async def get_db_null_pool() -> AsyncGenerator[DBManager, None]:
    async with DBManager(session_factory=async_session_maker_null_pool) as db:
        yield db


DBDep = Annotated[DBManager, Depends(get_db)]
DBDepNullPool = Annotated[DBManager, Depends(get_db_null_pool, use_cache=False)]


async def get_atomic_db() -> AsyncGenerator[DBManager, None]:
    async with DBManager(session_factory=async_session_maker) as db:
        async with db.transaction():
            yield db


async def get_atomic_db_null_pool() -> AsyncGenerator[DBManager, None]:
    async with DBManager(session_factory=async_session_maker_null_pool) as db:
        async with db.transaction():
            yield db


AtomicDBDep = Annotated[DBManager, Depends(get_atomic_db)]
AtomicDBDepNullPool = Annotated[DBManager, Depends(get_atomic_db_null_pool, use_cache=False)]
