import asyncio
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query

from src.connectors.tbank_invest_connector import TBankInvestConnector, TBankInvestRequestError
from src.core.config import settings
from src.moduls.tbank.schemas import (
    TBankAllowedSectorsResponse,
    TBankFavoriteShareRequest,
    TBankFavoriteSharesResponse,
    TBankMonitorCheckResponse,
    TBankMonitorGlobalCheckResponse,
    TBankMonitorCreateRequest,
    TBankMonitorDeleteRequest,
    TBankMonitorRebaseRequest,
    TBankMonitorUpdateThresholdsRequest,
    TBankMonitorListResponse,
    TBankMonitorToggleRequest,
    TBankOrderCancelRequest,
    TBankOrderCreateRequest,
    TBankOperationsRequest,
    TBankPortfolioRequest,
    TBankSharesResponse,
    TBankStopOrderCancelRequest,
    TBankStopOrderCreateRequest,
    TBankStoredSharesResponse,
    TBankSyncResponse,
    TBankTaskEnqueueResponse,
    TBankTradingActionResponse,
    TBankUserCredentialsStatusResponse,
    TBankUserCredentialsUpsertRequest,
)
from src.moduls.tbank.service import (
    ALLOWED_SECTORS,
    TBankMonitorService,
    TBankSharesService,
    TBankTradingService,
)
from src.moduls.tbank.tasks import sync_russian_shares_task
from src.utils.dependencies import AtomicDBDep, DBDep

tbank_router = APIRouter(prefix="/tbank", tags=["TBANK"])


def _build_global_connector() -> TBankInvestConnector:
    return TBankInvestConnector(
        token=settings.TBANK_INVEST_TOKEN,
        base_url=settings.TBANK_INVEST_BASE_URL,
        timeout=settings.TBANK_INVEST_TIMEOUT_SECONDS,
        ssl_verify=settings.TBANK_INVEST_SSL_VERIFY,
        ca_bundle_path=settings.TBANK_INVEST_CA_BUNDLE_PATH,
    )


async def _build_user_trading_service(db: DBDep, telegram_user_id: int) -> TBankTradingService:
    creds = await db.tbank_user_credential.get_active_by_telegram_user_id(telegram_user_id)
    if creds is None:
        raise HTTPException(
            status_code=403,
            detail="User T-Bank credentials are not configured",
        )
    connector = TBankInvestConnector(
        token=creds.tbank_token,
        base_url=settings.TBANK_INVEST_BASE_URL,
        timeout=settings.TBANK_INVEST_TIMEOUT_SECONDS,
        ssl_verify=settings.TBANK_INVEST_SSL_VERIFY,
        ca_bundle_path=settings.TBANK_INVEST_CA_BUNDLE_PATH,
    )
    return TBankTradingService(connector=connector, account_id=creds.tbank_account_id)


@tbank_router.get(
    "/shares",
    response_model=TBankSharesResponse,
    summary="Получить список акций из T-Bank Invest API",
)
async def get_tbank_shares(
    instrument_status: str = Query(
        default="INSTRUMENT_STATUS_BASE",
        description="INSTRUMENT_STATUS_UNSPECIFIED | INSTRUMENT_STATUS_BASE | INSTRUMENT_STATUS_ALL",
    ),
    instrument_exchange: str = Query(
        default="INSTRUMENT_EXCHANGE_UNSPECIFIED",
        description="INSTRUMENT_EXCHANGE_UNSPECIFIED | INSTRUMENT_EXCHANGE_DEALER",
    ),
    request_timeout_seconds: int = Query(default=20, ge=3, le=60),
    russian_only: bool = Query(default=True),
    include_dealer: bool = Query(default=True),
) -> TBankSharesResponse:
    connector = _build_global_connector()
    service = TBankSharesService(connector=connector)
    try:
        return await asyncio.wait_for(
            service.get_shares(
                instrument_status=instrument_status,
                instrument_exchange=instrument_exchange,
                russian_only=russian_only,
                include_dealer=include_dealer,
            ),
            timeout=request_timeout_seconds,
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail=f"T-Bank request exceeded {request_timeout_seconds}s timeout",
        ) from exc
    except TBankInvestRequestError as exc:
        message = str(exc)
        if "token is not configured" in message.lower():
            raise HTTPException(status_code=503, detail=message) from exc
        if "HTTP 401" in message:
            raise HTTPException(status_code=401, detail=message) from exc
        if "HTTP 403" in message:
            raise HTTPException(status_code=403, detail=message) from exc
        raise HTTPException(status_code=502, detail=message) from exc


@tbank_router.post(
    "/shares/sync/task",
    response_model=TBankTaskEnqueueResponse,
    summary="Поставить в очередь синхронизацию российских акций",
)
async def enqueue_tbank_shares_sync_task(
    instrument_status: str = Query(default="INSTRUMENT_STATUS_BASE"),
    instrument_exchange: str = Query(default="INSTRUMENT_EXCHANGE_UNSPECIFIED"),
    include_dealer: bool = Query(default=True),
) -> TBankTaskEnqueueResponse:
    task = await sync_russian_shares_task.kiq(
        instrument_status=instrument_status,
        instrument_exchange=instrument_exchange,
        include_dealer=include_dealer,
    )
    return TBankTaskEnqueueResponse(task_id=task.task_id, queue_name=settings.TASKIQ_QUEUE_NAME)


@tbank_router.post(
    "/shares/sync",
    response_model=TBankSyncResponse,
    summary="Синхронизировать акции из T-Bank Invest API в базу данных",
)
async def sync_tbank_shares(
    db: AtomicDBDep,
    instrument_status: str = Query(default="INSTRUMENT_STATUS_BASE"),
    instrument_exchange: str = Query(default="INSTRUMENT_EXCHANGE_UNSPECIFIED"),
    request_timeout_seconds: int = Query(default=30, ge=3, le=90),
    russian_only: bool = Query(default=True),
    include_dealer: bool = Query(default=True),
) -> TBankSyncResponse:
    connector = _build_global_connector()
    service = TBankSharesService(connector=connector, db=db)
    try:
        return await asyncio.wait_for(
            service.sync_shares_to_db(
                instrument_status=instrument_status,
                instrument_exchange=instrument_exchange,
                russian_only=russian_only,
                include_dealer=include_dealer,
            ),
            timeout=request_timeout_seconds,
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail=f"T-Bank sync exceeded {request_timeout_seconds}s timeout",
        ) from exc
    except TBankInvestRequestError as exc:
        message = str(exc)
        if "token is not configured" in message.lower():
            raise HTTPException(status_code=503, detail=message) from exc
        if "HTTP 401" in message:
            raise HTTPException(status_code=401, detail=message) from exc
        if "HTTP 403" in message:
            raise HTTPException(status_code=403, detail=message) from exc
        raise HTTPException(status_code=502, detail=message) from exc


@tbank_router.get(
    "/shares/stored",
    response_model=TBankStoredSharesResponse,
    summary="Получить сохраненные акции T-Bank из базы данных",
)
async def get_stored_tbank_shares(
    db: DBDep,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> TBankStoredSharesResponse:
    connector = _build_global_connector()
    service = TBankSharesService(connector=connector, db=db)
    return await service.get_stored_shares(limit=limit, offset=offset)


@tbank_router.get(
    "/sectors",
    response_model=TBankAllowedSectorsResponse,
    summary="Получить фиксированный список отраслей из ТЗ",
)
async def get_allowed_sectors() -> TBankAllowedSectorsResponse:
    return TBankAllowedSectorsResponse(sectors=list(ALLOWED_SECTORS))


@tbank_router.post(
    "/users/credentials",
    response_model=TBankUserCredentialsStatusResponse,
    summary="Сохранить персональные учетные данные T-Bank пользователя",
)
async def upsert_user_credentials(
    payload: TBankUserCredentialsUpsertRequest,
    db: AtomicDBDep,
) -> TBankUserCredentialsStatusResponse:
    await db.tbank_user_credential.upsert_credentials(
        telegram_user_id=payload.telegram_user_id,
        tbank_token=payload.tbank_token.strip(),
        tbank_account_id=payload.tbank_account_id.strip(),
    )
    return TBankUserCredentialsStatusResponse(
        telegram_user_id=payload.telegram_user_id,
        configured=True,
        tbank_account_id=payload.tbank_account_id.strip(),
    )


@tbank_router.get(
    "/users/{telegram_user_id}/credentials/status",
    response_model=TBankUserCredentialsStatusResponse,
    summary="Проверить, настроены ли персональные учетные данные T-Bank",
)
async def get_user_credentials_status(
    telegram_user_id: int,
    db: DBDep,
) -> TBankUserCredentialsStatusResponse:
    creds = await db.tbank_user_credential.get_active_by_telegram_user_id(telegram_user_id)
    if creds is None:
        return TBankUserCredentialsStatusResponse(
            telegram_user_id=telegram_user_id,
            configured=False,
            tbank_account_id=None,
        )
    return TBankUserCredentialsStatusResponse(
        telegram_user_id=telegram_user_id,
        configured=True,
        tbank_account_id=creds.tbank_account_id,
    )


@tbank_router.post(
    "/monitors",
    response_model=TBankTradingActionResponse,
    summary="Создать мониторинг цены по пользователю",
)
async def create_monitor(payload: TBankMonitorCreateRequest, db: AtomicDBDep) -> TBankTradingActionResponse:
    service = TBankMonitorService(db=db)
    item = await service.create_monitor(payload)
    return TBankTradingActionResponse(ok=True, details=item.model_dump())


@tbank_router.get(
    "/favorites",
    response_model=TBankFavoriteSharesResponse,
    summary="Список избранных акций пользователя",
)
async def list_favorites(
    db: DBDep,
    telegram_user_id: int = Query(..., ge=1),
) -> TBankFavoriteSharesResponse:
    figies = await db.tbank_favorite_share.list_figies_by_user(telegram_user_id)
    return TBankFavoriteSharesResponse(
        telegram_user_id=telegram_user_id,
        total=len(figies),
        figies=figies,
    )


@tbank_router.post(
    "/favorites/add",
    response_model=TBankTradingActionResponse,
    summary="Добавить акцию в избранное",
)
async def add_favorite(payload: TBankFavoriteShareRequest, db: AtomicDBDep) -> TBankTradingActionResponse:
    ok = await db.tbank_favorite_share.add(
        telegram_user_id=payload.telegram_user_id,
        figi=payload.figi,
    )
    return TBankTradingActionResponse(ok=ok, details={"figi": payload.figi, "favorite": True})


@tbank_router.post(
    "/favorites/remove",
    response_model=TBankTradingActionResponse,
    summary="Удалить акцию из избранного",
)
async def remove_favorite(payload: TBankFavoriteShareRequest, db: AtomicDBDep) -> TBankTradingActionResponse:
    removed = await db.tbank_favorite_share.remove(
        telegram_user_id=payload.telegram_user_id,
        figi=payload.figi,
    )
    return TBankTradingActionResponse(ok=True, details={"figi": payload.figi, "favorite": False, "removed": removed})


@tbank_router.get(
    "/monitors",
    response_model=TBankMonitorListResponse,
    summary="Список мониторингов пользователя",
)
async def list_monitors(db: DBDep, telegram_user_id: int = Query(..., ge=1)) -> TBankMonitorListResponse:
    service = TBankMonitorService(db=db)
    return await service.list_monitors(telegram_user_id)


@tbank_router.post(
    "/monitors/toggle",
    response_model=TBankTradingActionResponse,
    summary="Включить или выключить мониторинг",
)
async def toggle_monitor(payload: TBankMonitorToggleRequest, db: AtomicDBDep) -> TBankTradingActionResponse:
    service = TBankMonitorService(db=db)
    ok = await service.toggle_monitor(payload.telegram_user_id, payload.monitor_id, payload.is_active)
    if not ok:
        raise HTTPException(status_code=404, detail="Monitor not found")
    return TBankTradingActionResponse(ok=True, details={"monitor_id": payload.monitor_id, "is_active": payload.is_active})


@tbank_router.post(
    "/monitors/delete",
    response_model=TBankTradingActionResponse,
    summary="Удалить мониторинг",
)
async def delete_monitor(payload: TBankMonitorDeleteRequest, db: AtomicDBDep) -> TBankTradingActionResponse:
    service = TBankMonitorService(db=db)
    ok = await service.delete_monitor(payload.telegram_user_id, payload.monitor_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Monitor not found")
    return TBankTradingActionResponse(ok=True, details={"deleted": True, "monitor_id": payload.monitor_id})


@tbank_router.post(
    "/monitors/rebase",
    response_model=TBankTradingActionResponse,
    summary="Обновить базовую цену мониторинга по текущей цене",
)
async def rebase_monitor(payload: TBankMonitorRebaseRequest, db: AtomicDBDep) -> TBankTradingActionResponse:
    service = TBankMonitorService(db=db)
    new_base = await service.rebase_monitor(payload.telegram_user_id, payload.monitor_id)
    if new_base is None:
        raise HTTPException(status_code=404, detail="Monitor or current price not found")
    return TBankTradingActionResponse(
        ok=True,
        details={"monitor_id": payload.monitor_id, "base_price": str(new_base)},
    )


@tbank_router.post(
    "/monitors/update-thresholds",
    response_model=TBankTradingActionResponse,
    summary="Обновить пороги мониторинга",
)
async def update_monitor_thresholds(
    payload: TBankMonitorUpdateThresholdsRequest,
    db: AtomicDBDep,
) -> TBankTradingActionResponse:
    service = TBankMonitorService(db=db)
    ok = await service.update_thresholds(
        telegram_user_id=payload.telegram_user_id,
        monitor_id=payload.monitor_id,
        threshold_percent=Decimal(payload.threshold_percent),
        threshold_rub=Decimal(payload.threshold_rub),
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Monitor not found")
    return TBankTradingActionResponse(
        ok=True,
        details={
            "monitor_id": payload.monitor_id,
            "threshold_percent": payload.threshold_percent,
            "threshold_rub": payload.threshold_rub,
        },
    )


@tbank_router.get(
    "/monitors/check",
    response_model=TBankMonitorCheckResponse,
    summary="Проверить мониторинги пользователя и вернуть сработавшие",
)
async def check_monitors(db: AtomicDBDep, telegram_user_id: int = Query(..., ge=1)) -> TBankMonitorCheckResponse:
    service = TBankMonitorService(db=db)
    return await service.check_monitors(telegram_user_id)


@tbank_router.get(
    "/monitors/check-all",
    response_model=TBankMonitorGlobalCheckResponse,
    summary="Проверить все активные мониторинги",
)
async def check_all_monitors(db: AtomicDBDep) -> TBankMonitorGlobalCheckResponse:
    service = TBankMonitorService(db=db)
    return await service.check_all_monitors()


@tbank_router.post(
    "/orders",
    response_model=TBankTradingActionResponse,
    summary="Разместить биржевую заявку (лимит/рынок/лучшая цена)",
)
async def create_order(payload: TBankOrderCreateRequest, db: DBDep) -> TBankTradingActionResponse:
    service = await _build_user_trading_service(db=db, telegram_user_id=payload.telegram_user_id)
    try:
        result = await service.create_order(
            figi=payload.figi,
            quantity_lots=payload.quantity_lots,
            direction=payload.direction,
            order_type=payload.order_type,
            price=payload.price,
        )
        return TBankTradingActionResponse(ok=True, details=result)
    except TBankInvestRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@tbank_router.post(
    "/orders/cancel",
    response_model=TBankTradingActionResponse,
    summary="Отменить активную заявку",
)
async def cancel_order(payload: TBankOrderCancelRequest, db: DBDep) -> TBankTradingActionResponse:
    service = await _build_user_trading_service(db=db, telegram_user_id=payload.telegram_user_id)
    try:
        result = await service.cancel_order(order_id=payload.order_id)
        return TBankTradingActionResponse(ok=True, details=result)
    except TBankInvestRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@tbank_router.get(
    "/orders",
    response_model=TBankTradingActionResponse,
    summary="Получить активные заявки пользователя",
)
async def get_orders(
    db: DBDep,
    telegram_user_id: int = Query(..., ge=1),
) -> TBankTradingActionResponse:
    service = await _build_user_trading_service(db=db, telegram_user_id=telegram_user_id)
    try:
        result = await service.get_orders()
        return TBankTradingActionResponse(ok=True, details=result)
    except TBankInvestRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@tbank_router.post(
    "/stop-orders",
    response_model=TBankTradingActionResponse,
    summary="Разместить стоп-приказ (stop-market/stop-limit/take-profit)",
)
async def create_stop_order(payload: TBankStopOrderCreateRequest, db: DBDep) -> TBankTradingActionResponse:
    service = await _build_user_trading_service(db=db, telegram_user_id=payload.telegram_user_id)
    try:
        result = await service.create_stop_order(
            figi=payload.figi,
            quantity_lots=payload.quantity_lots,
            direction=payload.direction,
            stop_order_type=payload.stop_order_type,
            stop_price=payload.stop_price,
            price=payload.price,
            expiration_type=payload.expiration_type,
        )
        return TBankTradingActionResponse(ok=True, details=result)
    except TBankInvestRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@tbank_router.post(
    "/stop-orders/cancel",
    response_model=TBankTradingActionResponse,
    summary="Отменить активный стоп-приказ",
)
async def cancel_stop_order(payload: TBankStopOrderCancelRequest, db: DBDep) -> TBankTradingActionResponse:
    service = await _build_user_trading_service(db=db, telegram_user_id=payload.telegram_user_id)
    try:
        result = await service.cancel_stop_order(stop_order_id=payload.stop_order_id)
        return TBankTradingActionResponse(ok=True, details=result)
    except TBankInvestRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@tbank_router.get(
    "/stop-orders",
    response_model=TBankTradingActionResponse,
    summary="Получить активные стоп-приказы пользователя",
)
async def get_stop_orders(
    db: DBDep,
    telegram_user_id: int = Query(..., ge=1),
) -> TBankTradingActionResponse:
    service = await _build_user_trading_service(db=db, telegram_user_id=telegram_user_id)
    try:
        result = await service.get_stop_orders()
        return TBankTradingActionResponse(ok=True, details=result)
    except TBankInvestRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@tbank_router.post(
    "/portfolio",
    response_model=TBankTradingActionResponse,
    summary="Получить портфель пользователя",
)
async def get_portfolio(payload: TBankPortfolioRequest, db: DBDep) -> TBankTradingActionResponse:
    service = await _build_user_trading_service(db=db, telegram_user_id=payload.telegram_user_id)
    try:
        result = await service.get_portfolio()
        return TBankTradingActionResponse(ok=True, details=result)
    except TBankInvestRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@tbank_router.post(
    "/operations",
    response_model=TBankTradingActionResponse,
    summary="Получить историю операций пользователя",
)
async def get_operations(payload: TBankOperationsRequest, db: DBDep) -> TBankTradingActionResponse:
    service = await _build_user_trading_service(db=db, telegram_user_id=payload.telegram_user_id)
    try:
        result = await service.get_operations(days=payload.days)
        return TBankTradingActionResponse(ok=True, details=result)
    except TBankInvestRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
