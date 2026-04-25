from __future__ import annotations

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot_service.access import is_admin_user
from bot_service.service import TelegramSharesBrowserService


class AccessGuardMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self._service = TelegramSharesBrowserService()

    async def __call__(self, handler, event: TelegramObject, data: dict):
        from_user = getattr(event, "from_user", None)
        if from_user is None:
            return await handler(event, data)

        telegram_user_id = int(from_user.id)
        if is_admin_user(telegram_user_id):
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

        is_allowed = bool(payload.get("is_allowed"))
        if is_allowed:
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            await event.answer("Доступ к боту не подтверждён. Нажмите /start.", show_alert=True)
        elif isinstance(event, Message):
            await event.answer(
                "⛔ Доступ к боту пока не подтверждён.\n"
                "Нажмите /start, чтобы отправить заявку администраторам."
            )
        return None

