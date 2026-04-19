import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot_service.config import settings
from bot_service.handlers import MON_CB_DEL_PREFIX, MON_CB_REBASE_PREFIX, router
from bot_service.service import TelegramSharesBrowserService


main_logger = logging.getLogger("moex_bot")
if not main_logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def _run_monitor_poller(bot: Bot, stop_event: asyncio.Event) -> None:
    service = TelegramSharesBrowserService()
    interval_seconds = max(10, settings.MONITOR_POLL_INTERVAL_SECONDS)

    while not stop_event.is_set():
        try:
            payload = await service.check_all_monitors()
            events = payload.get("events", []) if isinstance(payload, dict) else []
            if isinstance(events, list):
                for event in events:
                    if not isinstance(event, dict):
                        continue
                    telegram_user_id = event.get("telegram_user_id")
                    monitor_id = event.get("monitor_id")
                    if not isinstance(telegram_user_id, int) or not isinstance(monitor_id, int):
                        continue

                    keyboard = InlineKeyboardMarkup(
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
                    text = (
                        "🔔 <b>Monitoring triggered</b>\n"
                        f"Monitor: <b>#{monitor_id}</b>\n"
                        f"Instrument: <b>{event.get('ticker') or event.get('figi')}</b>\n"
                        f"Price: <b>{event.get('current_price')}</b>\n"
                        f"Change: <b>{event.get('change_percent')}%</b> / <b>{event.get('change_rub')} RUB</b>\n"
                        f"Threshold: <b>{event.get('threshold_percent')}%</b> or <b>{event.get('threshold_rub')} RUB</b>"
                    )
                    await bot.send_message(
                        chat_id=telegram_user_id,
                        text=text,
                        reply_markup=keyboard,
                    )
        except Exception as exc:
            main_logger.info(f"Monitor poller error: {exc}")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue


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
        monitor_task = asyncio.create_task(_run_monitor_poller(bot=bot, stop_event=stop_event))
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
