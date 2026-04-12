import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query

from src.connectors.moex_iss_connector import MoexISSConnector, MoexISSRequestError
from src.db.dependencies import AtomicDBDep, DBDep
from src.moduls.auth.access_dependencies import require_permission
from src.moduls.auth.access_policy import access_permission_meta
from src.moduls.auth.access_service import AccessContext
from src.moduls.moex.moex_service import MoexSharesService
from src.moduls.moex.schemas import MoexSharesResponse, MoexStoredSharesResponse, MoexSyncResponse

moex_router = APIRouter(prefix="/moex", tags=["MOEX"])


@moex_router.get(
    "/shares",
    response_model=MoexSharesResponse,
    summary="Получить все акции MOEX и доступную полную информацию по каждой бумаге",
    openapi_extra=access_permission_meta("moex.shares.read", "Read all MOEX shares from ISS"),
)
async def get_all_shares(
    with_details: bool = Query(
        default=False,
        description="Если True, для каждой акции дополнительно запрашивается полный payload /iss/securities/{SECID}.json",
    ),
    details_concurrency: int = Query(
        default=10,
        ge=1,
        le=50,
        description="Максимальное число параллельных запросов details к MOEX ISS.",
    ),
    request_timeout_seconds: int = Query(
        default=20,
        ge=3,
        le=60,
        description="Максимальное время ожидания ответа от сервиса до возврата 504.",
    ),
    _: AccessContext = Depends(require_permission("moex.shares.read")),
) -> MoexSharesResponse:
    service = MoexSharesService(connector=MoexISSConnector())
    try:
        return await asyncio.wait_for(
            service.get_all_shares_with_info(
                with_details=with_details,
                details_concurrency=details_concurrency,
            ),
            timeout=request_timeout_seconds,
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail=f"MOEX request exceeded {request_timeout_seconds}s timeout",
        ) from exc
    except MoexISSRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@moex_router.post(
    "/shares/sync",
    response_model=MoexSyncResponse,
    summary="Загрузить акции с MOEX и сохранить их в базу данных",
    openapi_extra=access_permission_meta("moex.shares.sync", "Sync MOEX shares into database"),
)
async def sync_shares_to_db(
    db: AtomicDBDep,
    with_details: bool = Query(
        default=False,
        description="Если True, перед сохранением будет загружен подробный payload по каждой акции.",
    ),
    details_concurrency: int = Query(
        default=5,
        ge=1,
        le=20,
        description="Параллелизм при загрузке details. Для большого sync лучше держать небольшим.",
    ),
    request_timeout_seconds: int = Query(
        default=40,
        ge=5,
        le=180,
        description="Жесткий таймаут на полный sync.",
    ),
    _: AccessContext = Depends(require_permission("moex.shares.sync")),
) -> MoexSyncResponse:
    service = MoexSharesService(
        connector=MoexISSConnector(),
        db=db,
    )
    try:
        return await asyncio.wait_for(
            service.sync_shares_to_db(
                with_details=with_details,
                details_concurrency=details_concurrency,
            ),
            timeout=request_timeout_seconds,
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail=f"MOEX sync exceeded {request_timeout_seconds}s timeout",
        ) from exc
    except MoexISSRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@moex_router.get(
    "/shares/stored",
    response_model=MoexStoredSharesResponse,
    summary="Получить сохраненные в БД акции MOEX",
    openapi_extra=access_permission_meta("moex.shares.stored.read", "Read stored MOEX shares"),
)
async def get_stored_shares(
    db: DBDep,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    _: AccessContext = Depends(require_permission("moex.shares.stored.read")),
) -> MoexStoredSharesResponse:
    service = MoexSharesService(
        connector=MoexISSConnector(),
        db=db,
    )
    return await service.get_stored_shares(limit=limit, offset=offset)
