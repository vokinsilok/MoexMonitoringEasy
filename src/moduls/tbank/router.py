import asyncio
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query

from src.connectors.ai_gateway_connector import AIGatewayConnector, AIGatewayRequestError
from src.connectors.tbank_invest_connector import TBankInvestConnector, TBankInvestRequestError
from src.core.config import settings
from src.init import redis_manager
from src.moduls.tbank.schemas import (
    TBankAllowedSectorsResponse,
    TBankBotAccessActionRequest,
    TBankBotAccessListResponse,
    TBankBotAccessRequest,
    TBankBotAccessStatusResponse,
    TBankCalendarNotificationCheckResponse,
    TBankCalendarNotificationSettingsRequest,
    TBankCalendarNotificationSettingsResponse,
    TBankCalendarResponse,
    TBankFavoriteShareRequest,
    TBankFavoriteSharesResponse,
    TBankMonitorCheckResponse,
    TBankMonitorCreateRequest,
    TBankMonitorDeleteRequest,
    TBankMonitorRebaseRequest,
    TBankMonitorUpdateThresholdsRequest,
    TBankMonitorListResponse,
    TBankMonitorToggleRequest,
    TBankOrderCancelRequest,
    TBankOrderCreateRequest,
    TBankOperationsRequest,
    TBankPortfolioAnalysisRequest,
    TBankPortfolioAnalysisResponse,
    TBankPortfolioRequest,
    TBankSharesResponse,
    TBankStopOrderCancelRequest,
    TBankStopOrderCreateRequest,
    TBankStoredShareItem,
    TBankStoredSharesResponse,
    TBankSyncResponse,
    TBankTaskEnqueueResponse,
    TBankTradingActionResponse,
    TBankUserCredentialsStatusResponse,
    TBankUserCredentialsUpsertRequest,
)
from src.moduls.tbank.service import (
    ALLOWED_SECTORS,
    TBankCalendarService,
    TBankMonitorService,
    TBankPortfolioAnalysisService,
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


def _build_ai_connector() -> AIGatewayConnector:
    return AIGatewayConnector(
        api_key=settings.AI_API_KEY,
        base_url=settings.AI_BASE_URL,
        model=settings.AI_MODEL,
        timeout=settings.AI_TIMEOUT_SECONDS,
        max_tokens=settings.AI_MAX_TOKENS,
        temperature=settings.AI_TEMPERATURE,
    )


def _build_ai_web_plugins() -> list[dict]:
    plugins: list[dict] = []
    if settings.AI_WEB_SEARCH_ENABLED:
        web_plugin = {
            "id": "web",
            "max_results": settings.AI_WEB_SEARCH_MAX_RESULTS,
        }
        engine = settings.AI_WEB_SEARCH_ENGINE.strip()
        if engine:
            web_plugin["engine"] = engine
        plugins.append(web_plugin)
    return plugins


def _build_ai_web_search_options() -> dict | None:
    if not settings.AI_WEB_SEARCH_ENABLED:
        return None
    return {"search_context_size": settings.AI_WEB_SEARCH_CONTEXT_SIZE}


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


def _to_bot_access_status_response(row, *, default_user_id: int | None = None) -> TBankBotAccessStatusResponse:
    if row is None:
        return TBankBotAccessStatusResponse(
            telegram_user_id=int(default_user_id or 0),
            username=None,
            first_name=None,
            last_name=None,
            status="none",
            is_allowed=False,
            requested_at=None,
            approved_at=None,
            revoked_at=None,
            approved_by=None,
            revoked_by=None,
        )

    status = str(getattr(row, "status", "none") or "none").strip().lower()
    return TBankBotAccessStatusResponse(
        telegram_user_id=int(getattr(row, "telegram_user_id", 0) or 0),
        username=getattr(row, "username", None),
        first_name=getattr(row, "first_name", None),
        last_name=getattr(row, "last_name", None),
        status=status,
        is_allowed=(status == "approved"),
        requested_at=getattr(row, "requested_at", None),
        approved_at=getattr(row, "approved_at", None),
        revoked_at=getattr(row, "revoked_at", None),
        approved_by=getattr(row, "approved_by", None),
        revoked_by=getattr(row, "revoked_by", None),
    )


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
    service = TBankSharesService(connector=connector, cache=redis_manager)
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


@tbank_router.get(
    "/instruments",
    response_model=TBankSharesResponse,
    summary="Получить список инструментов из T-Bank Invest API",
)
async def get_tbank_instruments(
    instrument_status: str = Query(default="INSTRUMENT_STATUS_BASE"),
    instrument_exchange: str = Query(default="INSTRUMENT_EXCHANGE_UNSPECIFIED"),
    request_timeout_seconds: int = Query(default=30, ge=3, le=90),
    russian_only: bool = Query(default=True),
    include_dealer: bool = Query(default=True),
    instrument_types: list[str] = Query(default=["share", "bond", "etf"]),
) -> TBankSharesResponse:
    connector = _build_global_connector()
    service = TBankSharesService(connector=connector, cache=redis_manager)
    try:
        return await asyncio.wait_for(
            service.get_instruments(
                instrument_types=instrument_types,
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
        instrument_types=["share"],
    )
    return TBankTaskEnqueueResponse(task_id=task.task_id, queue_name=settings.TASKIQ_QUEUE_NAME)


@tbank_router.post(
    "/instruments/sync/task",
    response_model=TBankTaskEnqueueResponse,
    summary="Поставить в очередь синхронизацию российских инструментов",
)
async def enqueue_tbank_instruments_sync_task(
    instrument_status: str = Query(default="INSTRUMENT_STATUS_BASE"),
    instrument_exchange: str = Query(default="INSTRUMENT_EXCHANGE_UNSPECIFIED"),
    include_dealer: bool = Query(default=True),
    instrument_types: list[str] = Query(default=["share", "bond", "etf"]),
) -> TBankTaskEnqueueResponse:
    task = await sync_russian_shares_task.kiq(
        instrument_status=instrument_status,
        instrument_exchange=instrument_exchange,
        include_dealer=include_dealer,
        instrument_types=instrument_types,
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
    service = TBankSharesService(connector=connector, db=db, cache=redis_manager)
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


@tbank_router.post(
    "/instruments/sync",
    response_model=TBankSyncResponse,
    summary="Синхронизировать акции, облигации и фонды из T-Bank Invest API в базу данных",
)
async def sync_tbank_instruments(
    db: AtomicDBDep,
    instrument_status: str = Query(default="INSTRUMENT_STATUS_BASE"),
    instrument_exchange: str = Query(default="INSTRUMENT_EXCHANGE_UNSPECIFIED"),
    request_timeout_seconds: int = Query(default=60, ge=3, le=180),
    russian_only: bool = Query(default=True),
    include_dealer: bool = Query(default=True),
    instrument_types: list[str] = Query(default=["share", "bond", "etf"]),
) -> TBankSyncResponse:
    connector = _build_global_connector()
    service = TBankSharesService(connector=connector, db=db, cache=redis_manager)
    try:
        return await asyncio.wait_for(
            service.sync_instruments_to_db(
                instrument_types=instrument_types,
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
    service = TBankSharesService(connector=connector, db=db, cache=redis_manager)
    return await service.get_stored_shares(limit=limit, offset=offset)


@tbank_router.get(
    "/instruments/stored",
    response_model=TBankStoredSharesResponse,
    summary="Получить сохраненные инструменты T-Bank из базы данных",
)
async def get_stored_tbank_instruments(
    db: DBDep,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    instrument_types: list[str] = Query(default=["share", "bond", "etf"]),
) -> TBankStoredSharesResponse:
    connector = _build_global_connector()
    service = TBankSharesService(connector=connector, db=db, cache=redis_manager)
    return await service.get_stored_instruments(
        limit=limit,
        offset=offset,
        instrument_types=instrument_types,
    )


@tbank_router.get(
    "/shares/{figi}/online",
    response_model=TBankStoredShareItem,
    summary="Получить live-детали акции по FIGI из T-Bank Invest API",
)
async def get_online_share_details(
    figi: str,
    db: DBDep,
) -> TBankStoredShareItem:
    connector = _build_global_connector()
    service = TBankSharesService(connector=connector, db=db, cache=redis_manager)
    try:
        item = await service.get_share_details_online(figi=figi)
    except TBankInvestRequestError as exc:
        message = str(exc)
        if "token is not configured" in message.lower():
            raise HTTPException(status_code=503, detail=message) from exc
        if "HTTP 401" in message:
            raise HTTPException(status_code=401, detail=message) from exc
        if "HTTP 403" in message:
            raise HTTPException(status_code=403, detail=message) from exc
        raise HTTPException(status_code=502, detail=message) from exc

    if item is None:
        raise HTTPException(status_code=404, detail="Share not found")
    return item


@tbank_router.get(
    "/instruments/{figi}/online",
    response_model=TBankStoredShareItem,
    summary="Получить live-детали инструмента по FIGI из T-Bank Invest API",
)
async def get_online_instrument_details(
    figi: str,
    db: DBDep,
    instrument_type: str | None = Query(default=None),
) -> TBankStoredShareItem:
    connector = _build_global_connector()
    service = TBankSharesService(connector=connector, db=db, cache=redis_manager)
    try:
        item = await service.get_instrument_details_online(figi=figi, instrument_type=instrument_type)
    except TBankInvestRequestError as exc:
        message = str(exc)
        if "token is not configured" in message.lower():
            raise HTTPException(status_code=503, detail=message) from exc
        if "HTTP 401" in message:
            raise HTTPException(status_code=401, detail=message) from exc
        if "HTTP 403" in message:
            raise HTTPException(status_code=403, detail=message) from exc
        raise HTTPException(status_code=502, detail=message) from exc

    if item is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return item


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
    "/bot-access/request",
    response_model=TBankBotAccessStatusResponse,
    summary="Создать/обновить заявку на доступ к Telegram-боту",
)
async def request_bot_access(payload: TBankBotAccessRequest, db: AtomicDBDep) -> TBankBotAccessStatusResponse:
    row = await db.tbank_bot_access_user.upsert_request(
        telegram_user_id=payload.telegram_user_id,
        username=(payload.username.strip() if payload.username else None),
        first_name=(payload.first_name.strip() if payload.first_name else None),
        last_name=(payload.last_name.strip() if payload.last_name else None),
    )
    return _to_bot_access_status_response(row)


@tbank_router.get(
    "/bot-access/status",
    response_model=TBankBotAccessStatusResponse,
    summary="Получить статус доступа пользователя к Telegram-боту",
)
async def get_bot_access_status(
    db: DBDep,
    telegram_user_id: int = Query(..., ge=1),
) -> TBankBotAccessStatusResponse:
    row = await db.tbank_bot_access_user.get_by_telegram_user_id(telegram_user_id)
    return _to_bot_access_status_response(row, default_user_id=telegram_user_id)


@tbank_router.post(
    "/bot-access/approve",
    response_model=TBankBotAccessStatusResponse,
    summary="Выдать доступ к Telegram-боту пользователю",
)
async def approve_bot_access(payload: TBankBotAccessActionRequest, db: AtomicDBDep) -> TBankBotAccessStatusResponse:
    row = await db.tbank_bot_access_user.approve(
        telegram_user_id=payload.telegram_user_id,
        admin_telegram_user_id=payload.admin_telegram_user_id,
    )
    return _to_bot_access_status_response(row)


@tbank_router.post(
    "/bot-access/revoke",
    response_model=TBankBotAccessStatusResponse,
    summary="Отозвать доступ к Telegram-боту у пользователя",
)
async def revoke_bot_access(payload: TBankBotAccessActionRequest, db: AtomicDBDep) -> TBankBotAccessStatusResponse:
    row = await db.tbank_bot_access_user.revoke(
        telegram_user_id=payload.telegram_user_id,
        admin_telegram_user_id=payload.admin_telegram_user_id,
    )
    return _to_bot_access_status_response(row)


@tbank_router.get(
    "/bot-access/pending",
    response_model=TBankBotAccessListResponse,
    summary="Список заявок на доступ к Telegram-боту",
)
async def list_pending_bot_access(
    db: DBDep,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> TBankBotAccessListResponse:
    rows, total = await db.tbank_bot_access_user.list_pending(limit=limit, offset=offset)
    return TBankBotAccessListResponse(
        total=total,
        items=[_to_bot_access_status_response(row) for row in rows],
    )


@tbank_router.get(
    "/bot-access/users",
    response_model=TBankBotAccessListResponse,
    summary="Список пользователей Telegram-бота",
)
async def list_bot_access_users(
    db: DBDep,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    exclude_telegram_user_ids: list[int] = Query(default=[]),
) -> TBankBotAccessListResponse:
    rows, total = await db.tbank_bot_access_user.list_users(
        limit=limit,
        offset=offset,
        exclude_telegram_user_ids=exclude_telegram_user_ids,
    )
    return TBankBotAccessListResponse(
        total=total,
        items=[_to_bot_access_status_response(row) for row in rows],
    )


@tbank_router.post(
    "/monitors",
    response_model=TBankTradingActionResponse,
    summary="Создать мониторинг цены по пользователю",
)
async def create_monitor(payload: TBankMonitorCreateRequest, db: AtomicDBDep) -> TBankTradingActionResponse:
    service = TBankMonitorService(db=db, connector=_build_global_connector())
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
    "/calendar",
    response_model=TBankCalendarResponse,
    summary="Календарь выплат и событий по избранным инструментам",
)
async def get_calendar(
    db: DBDep,
    telegram_user_id: int = Query(..., ge=1),
    days_ahead: int = Query(default=180, ge=1, le=730),
) -> TBankCalendarResponse:
    service = TBankCalendarService(db=db, connector=_build_global_connector())
    return await service.list_calendar(telegram_user_id=telegram_user_id, days_ahead=days_ahead)


@tbank_router.get(
    "/calendar/settings",
    response_model=TBankCalendarNotificationSettingsResponse,
    summary="Настройки календарных уведомлений",
)
async def get_calendar_settings(
    db: AtomicDBDep,
    telegram_user_id: int = Query(..., ge=1),
) -> TBankCalendarNotificationSettingsResponse:
    service = TBankCalendarService(db=db, connector=_build_global_connector())
    return await service.get_notification_settings(telegram_user_id)


@tbank_router.post(
    "/calendar/settings",
    response_model=TBankCalendarNotificationSettingsResponse,
    summary="Обновить настройки календарных уведомлений",
)
async def update_calendar_settings(
    payload: TBankCalendarNotificationSettingsRequest,
    db: AtomicDBDep,
) -> TBankCalendarNotificationSettingsResponse:
    service = TBankCalendarService(db=db, connector=_build_global_connector())
    return await service.update_notification_settings(
        telegram_user_id=payload.telegram_user_id,
        enabled=payload.enabled,
        days_before=payload.days_before,
    )


@tbank_router.post(
    "/calendar/notifications/check",
    response_model=TBankCalendarNotificationCheckResponse,
    summary="Проверить календарные уведомления пользователя",
)
async def check_calendar_notifications(
    payload: TBankPortfolioRequest,
    db: AtomicDBDep,
) -> TBankCalendarNotificationCheckResponse:
    service = TBankCalendarService(db=db, connector=_build_global_connector())
    return await service.check_notifications(telegram_user_id=payload.telegram_user_id)


@tbank_router.get(
    "/monitors",
    response_model=TBankMonitorListResponse,
    summary="Список мониторингов пользователя",
)
async def list_monitors(db: DBDep, telegram_user_id: int = Query(..., ge=1)) -> TBankMonitorListResponse:
    service = TBankMonitorService(db=db, connector=_build_global_connector())
    return await service.list_monitors(telegram_user_id)


@tbank_router.post(
    "/monitors/toggle",
    response_model=TBankTradingActionResponse,
    summary="Включить или выключить мониторинг",
)
async def toggle_monitor(payload: TBankMonitorToggleRequest, db: AtomicDBDep) -> TBankTradingActionResponse:
    service = TBankMonitorService(db=db, connector=_build_global_connector())
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
    service = TBankMonitorService(db=db, connector=_build_global_connector())
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
    service = TBankMonitorService(db=db, connector=_build_global_connector())
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
    service = TBankMonitorService(db=db, connector=_build_global_connector())
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
    service = TBankMonitorService(db=db, connector=_build_global_connector())
    return await service.check_monitors(telegram_user_id)


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
            confirm_margin_trade=payload.confirm_margin_trade,
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
            confirm_margin_trade=payload.confirm_margin_trade,
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
    "/portfolio/analyze",
    response_model=TBankPortfolioAnalysisResponse,
    summary="Сформировать AI-анализ портфеля пользователя",
)
async def analyze_portfolio(
    payload: TBankPortfolioAnalysisRequest,
    db: DBDep,
) -> TBankPortfolioAnalysisResponse:
    trading_service = await _build_user_trading_service(db=db, telegram_user_id=payload.telegram_user_id)
    analysis_service = TBankPortfolioAnalysisService(
        trading_service=trading_service,
        ai_connector=_build_ai_connector(),
        model=settings.AI_MODEL,
        web_plugins=_build_ai_web_plugins(),
        web_search_options=_build_ai_web_search_options(),
    )
    try:
        return await analysis_service.analyze(horizon=payload.horizon)
    except AIGatewayRequestError as exc:
        message = str(exc)
        if "api key is not configured" in message.lower():
            raise HTTPException(status_code=503, detail=message) from exc
        raise HTTPException(status_code=502, detail=message) from exc
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
