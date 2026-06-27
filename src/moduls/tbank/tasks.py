import json
from typing import Any

from src.connectors.redis_connector import RedisManager
from src.connectors.tbank_invest_connector import TBankInvestConnector
from src.core.config import settings
from src.db.database import async_session_maker
from src.moduls.tbank.service import TBankCalendarService, TBankMonitorService, TBankSharesService
from src.tasks.broker import broker
from src.utils.db_manager import DBManager


def _build_connector() -> TBankInvestConnector:
    return TBankInvestConnector(
        token=settings.TBANK_INVEST_TOKEN,
        base_url=settings.TBANK_INVEST_BASE_URL,
        timeout=settings.TBANK_INVEST_TIMEOUT_SECONDS,
        ssl_verify=settings.TBANK_INVEST_SSL_VERIFY,
        ca_bundle_path=settings.TBANK_INVEST_CA_BUNDLE_PATH,
    )


async def _connect_cache() -> RedisManager | None:
    cache = RedisManager(host=settings.REDIS_HOST, port=settings.REDIS_PORT)
    try:
        await cache.connect()
        return cache
    except Exception:
        return None


async def _close_cache(cache: RedisManager | None) -> None:
    if cache is None:
        return
    try:
        await cache.close()
    except Exception:
        return


@broker.task(task_name="tbank.sync_russian_shares")
async def sync_russian_shares_task(
        instrument_status: str = "INSTRUMENT_STATUS_BASE",
        instrument_exchange: str = "INSTRUMENT_EXCHANGE_UNSPECIFIED",
        include_dealer: bool = True,
        instrument_types: list[str] | None = None,
) -> dict:
    connector = _build_connector()
    cache = await _connect_cache()
    try:
        async with DBManager(session_factory=async_session_maker) as db:
            async with db.transaction():
                service = TBankSharesService(connector=connector, db=db, cache=cache)
                result = await service.sync_instruments_to_db(
                    instrument_types=instrument_types or ["share", "bond", "etf"],
                    instrument_status=instrument_status,
                    instrument_exchange=instrument_exchange,
                    russian_only=True,
                    include_dealer=include_dealer,
                )
                return result.model_dump()
    finally:
        await _close_cache(cache)


@broker.task(task_name="tbank.check_all_monitors")
async def check_all_monitors_task() -> dict[str, Any]:
    connector = _build_connector()
    cache = await _connect_cache()
    try:
        async with DBManager(session_factory=async_session_maker) as db:
            async with db.transaction():
                service = TBankMonitorService(db=db, connector=connector)
                result = await service.check_all_monitors()
                payload = result.model_dump()
                events = payload.get("events")
                if cache is not None and isinstance(events, list) and events:
                    queue_key = settings.TBANK_MONITOR_EVENTS_QUEUE_KEY
                    serialized_events = [
                        json.dumps(event, ensure_ascii=False)
                        for event in events
                        if isinstance(event, dict)
                    ]
                    if serialized_events:
                        await cache.rpush(queue_key, *serialized_events)
                return payload
    finally:
        await _close_cache(cache)


@broker.task(task_name="tbank.check_calendar_notifications")
async def check_calendar_notifications_task() -> dict[str, Any]:
    connector = _build_connector()
    cache = await _connect_cache()
    try:
        async with DBManager(session_factory=async_session_maker) as db:
            async with db.transaction():
                service = TBankCalendarService(db=db, connector=connector)
                result = await service.check_all_notifications()
                payload = result.model_dump()
                events = payload.get("events")
                if cache is not None and isinstance(events, list) and events:
                    serialized_events = [
                        json.dumps(event, ensure_ascii=False)
                        for event in events
                        if isinstance(event, dict)
                    ]
                    if serialized_events:
                        await cache.rpush(settings.TBANK_CALENDAR_EVENTS_QUEUE_KEY, *serialized_events)
                return payload
    finally:
        await _close_cache(cache)
