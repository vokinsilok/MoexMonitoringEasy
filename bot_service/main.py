import asyncio
import json
import logging

import redis.asyncio as redis
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot_service.config import settings
from bot_service.handlers import MON_CB_DEL_PREFIX, MON_CB_REBASE_PREFIX, router


main_logger = logging.getLogger("moex_bot")
if not main_logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


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


async def main() -> None:
    token = settings.TELEGRAM_BOT_TOKEN.strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)

    retry_delay_seconds = 5
    while True:
        bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        stop_event = asyncio.Event()
        monitor_task = asyncio.create_task(_run_monitor_consumer(bot=bot, stop_event=stop_event))
        try:
            await dispatcher.start_polling(bot)
            return
        except TelegramNetworkError as exc:
            main_logger.info(f"Telegram network error, retry in {retry_delay_seconds}s: {exc}")
            await asyncio.sleep(retry_delay_seconds)
        except OSError as exc:
            main_logger.info(f"Network OS error, retry in {retry_delay_seconds}s: {exc}")
            await asyncio.sleep(retry_delay_seconds)
        finally:
            stop_event.set()
            try:
                await monitor_task
            except Exception:
                pass
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
