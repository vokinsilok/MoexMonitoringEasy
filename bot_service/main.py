import asyncio
import json
import logging

import redis.asyncio as redis
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot_service.config import settings
from bot_service.handlers import MON_CB_DEL_PREFIX, MON_CB_REBASE_PREFIX, router


main_logger = logging.getLogger("moex_bot")
if not main_logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _build_bot(token: str, *, use_proxy: bool) -> Bot:
    proxy_url = settings.TELEGRAM_PROXY_URL.strip()
    session = AiohttpSession(proxy=proxy_url) if use_proxy and proxy_url else None
    return Bot(
        token=token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

try:
    from bot_service.access_middleware import AccessGuardMiddleware
except ModuleNotFoundError:
    from aiogram import BaseMiddleware
    from aiogram.types import CallbackQuery, Message, TelegramObject
    from bot_service.service import TelegramSharesBrowserService

    _FALLBACK_ADMIN_TELEGRAM_IDS = {570843200, 248280244}

    class AccessGuardMiddleware(BaseMiddleware):
        def __init__(self) -> None:
            self._service = TelegramSharesBrowserService()

        async def __call__(self, handler, event: TelegramObject, data: dict):
            from_user = getattr(event, "from_user", None)
            if from_user is None:
                return await handler(event, data)

            telegram_user_id = int(from_user.id)
            if telegram_user_id in _FALLBACK_ADMIN_TELEGRAM_IDS:
                data["is_admin_user"] = True
                return await handler(event, data)

            if isinstance(event, Message):
                text = (event.text or "").strip().lower()
                if text.startswith("/start"):
                    return await handler(event, data)

            try:
                payload = await self._service.get_bot_access_status(telegram_user_id)
            except RuntimeError:
                if isinstance(event, CallbackQuery):
                    await event.answer("Сервис доступа временно недоступен.", show_alert=True)
                elif isinstance(event, Message):
                    await event.answer("Сервис доступа временно недоступен. Попробуйте позже.")
                return None

            if bool(payload.get("is_allowed")):
                return await handler(event, data)

            if isinstance(event, CallbackQuery):
                await event.answer("Доступ к боту не подтверждён. Нажмите /start.", show_alert=True)
            elif isinstance(event, Message):
                await event.answer(
                    "⛔ Доступ к боту пока не подтверждён.\n"
                    "Нажмите /start, чтобы отправить заявку администраторам."
                )
            return None

    main_logger.warning(
        "bot_service.access_middleware module not found, using fallback AccessGuardMiddleware from main.py"
    )


def _build_monitor_keyboard(monitor_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Delete",
                    callback_data=f"{MON_CB_DEL_PREFIX}:{monitor_id}",
                ),
                InlineKeyboardButton(
                    text="Rebase",
                    callback_data=f"{MON_CB_REBASE_PREFIX}:{monitor_id}",
                ),
            ]
        ]
    )


def _render_monitor_text(event: dict, monitor_id: int) -> str:
    return (
        "<b>Monitoring triggered</b>\n"
        f"Monitor: <b>#{monitor_id}</b>\n"
        f"Instrument: <b>{event.get('ticker') or event.get('figi')}</b>\n"
        f"Price: <b>{event.get('current_price')}</b>\n"
        f"Change: <b>{event.get('change_percent')}%</b> / <b>{event.get('change_rub')} RUB</b>\n"
        f"Threshold: <b>{event.get('threshold_percent')}%</b> or <b>{event.get('threshold_rub')} RUB</b>"
    )


def _render_calendar_notification_text(event: dict) -> str:
    ticker = event.get("ticker") or event.get("figi") or "Инструмент"
    event_type = event.get("event_type_label") or event.get("event_type") or "Событие"
    event_date = event.get("event_date") or "—"
    days_before = event.get("days_before")
    amount = event.get("amount")
    currency = event.get("currency")
    amount_text = f"\nСумма: <b>{amount} {currency or ''}</b>" if amount else ""
    description = event.get("description")
    description_text = f"\n{description}" if description else ""
    return (
        "🗓 <b>Календарное уведомление</b>\n"
        f"Инструмент: <b>{ticker}</b>\n"
        f"Событие: <b>{event_type}</b>\n"
        f"Дата: <b>{event_date}</b>\n"
        f"Напоминание: <b>за {days_before} дн.</b>"
        f"{amount_text}"
        f"{description_text}"
    )


async def _run_monitor_consumer(bot: Bot, stop_event: asyncio.Event) -> None:
    queue_key = settings.TBANK_MONITOR_EVENTS_QUEUE_KEY
    redis_client = None

    while not stop_event.is_set():
        try:
            if redis_client is None:
                redis_client = redis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT)
                await redis_client.ping()

            item = await redis_client.blpop(queue_key, timeout=5)
            if item is None:
                continue

            _, raw_payload = item
            if isinstance(raw_payload, bytes):
                raw_payload = raw_payload.decode("utf-8", errors="ignore")
            if not isinstance(raw_payload, str):
                continue

            event = json.loads(raw_payload)
            if not isinstance(event, dict):
                continue

            telegram_user_id = event.get("telegram_user_id")
            monitor_id = event.get("monitor_id")
            if not isinstance(telegram_user_id, int) or not isinstance(monitor_id, int):
                continue

            await bot.send_message(
                chat_id=telegram_user_id,
                text=_render_monitor_text(event, monitor_id),
                reply_markup=_build_monitor_keyboard(monitor_id),
            )
        except Exception as exc:
            main_logger.info(f"Monitor consumer error: {exc}")
            if redis_client is not None:
                try:
                    await redis_client.aclose()
                except Exception:
                    pass
                redis_client = None
            await asyncio.sleep(2)

    if redis_client is not None:
        try:
            await redis_client.aclose()
        except Exception:
            pass


async def _run_calendar_consumer(bot: Bot, stop_event: asyncio.Event) -> None:
    queue_key = settings.TBANK_CALENDAR_EVENTS_QUEUE_KEY
    redis_client = None

    while not stop_event.is_set():
        try:
            if redis_client is None:
                redis_client = redis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT)
                await redis_client.ping()

            item = await redis_client.blpop(queue_key, timeout=5)
            if item is None:
                continue

            _, raw_payload = item
            if isinstance(raw_payload, bytes):
                raw_payload = raw_payload.decode("utf-8", errors="ignore")
            if not isinstance(raw_payload, str):
                continue

            event = json.loads(raw_payload)
            if not isinstance(event, dict):
                continue

            telegram_user_id = event.get("telegram_user_id")
            if not isinstance(telegram_user_id, int):
                continue

            await bot.send_message(
                chat_id=telegram_user_id,
                text=_render_calendar_notification_text(event),
            )
        except Exception as exc:
            main_logger.info(f"Calendar consumer error: {exc}")
            if redis_client is not None:
                try:
                    await redis_client.aclose()
                except Exception:
                    pass
                redis_client = None
            await asyncio.sleep(2)

    if redis_client is not None:
        try:
            await redis_client.aclose()
        except Exception:
            pass


async def main() -> None:
    token = settings.TELEGRAM_BOT_TOKEN.strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    dispatcher = Dispatcher(storage=MemoryStorage())
    access_guard = AccessGuardMiddleware()
    dispatcher.message.outer_middleware(access_guard)
    dispatcher.callback_query.outer_middleware(access_guard)
    dispatcher.include_router(router)

    retry_delay_seconds = 5
    use_proxy = False
    while True:
        bot = _build_bot(token=token, use_proxy=use_proxy)
        stop_event = asyncio.Event()
        monitor_task = asyncio.create_task(_run_monitor_consumer(bot=bot, stop_event=stop_event))
        calendar_task = asyncio.create_task(_run_calendar_consumer(bot=bot, stop_event=stop_event))
        try:
            await dispatcher.start_polling(bot)
            return
        except TelegramNetworkError as exc:
            if settings.TELEGRAM_PROXY_URL.strip() and not use_proxy:
                use_proxy = True
                main_logger.info("Direct Telegram connection failed; retrying through configured proxy")
            main_logger.info(f"Telegram network error, retry in {retry_delay_seconds}s: {exc}")
            await asyncio.sleep(retry_delay_seconds)
        except OSError as exc:
            if settings.TELEGRAM_PROXY_URL.strip() and not use_proxy:
                use_proxy = True
                main_logger.info("Direct Telegram connection failed; retrying through configured proxy")
            main_logger.info(f"Network OS error, retry in {retry_delay_seconds}s: {exc}")
            await asyncio.sleep(retry_delay_seconds)
        finally:
            stop_event.set()
            try:
                await monitor_task
            except Exception:
                pass
            try:
                await calendar_task
            except Exception:
                pass
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
