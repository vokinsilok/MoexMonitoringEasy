import httpx
from decimal import Decimal
from urllib.parse import quote

from bot_service.config import settings
from bot_service.models import ShareViewItem


class TelegramSharesBrowserService:
    def __init__(self) -> None:
        self._base_url = settings.BACKEND_API_BASE_URL.rstrip("/")
        self._timeout_seconds = max(3, settings.BACKEND_API_TIMEOUT_SECONDS)

    async def get_shares_page(
        self,
        page: int,
        page_size: int,
    ) -> tuple[list[ShareViewItem], int, int, int]:
        safe_page_size = min(max(page_size, 1), 1000)
        safe_page = max(page, 1)
        payload = await self._fetch_stored_shares_payload(
            limit=safe_page_size,
            offset=(safe_page - 1) * safe_page_size,
        )
        total = self._safe_int(payload.get("total")) or 0
        total_pages = max(1, (total + safe_page_size - 1) // safe_page_size)
        if safe_page > total_pages:
            safe_page = total_pages
            payload = await self._fetch_stored_shares_payload(
                limit=safe_page_size,
                offset=(safe_page - 1) * safe_page_size,
            )
            total = self._safe_int(payload.get("total")) or total

        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raise RuntimeError("Некорректный ответ backend API: поле 'items' отсутствует")

        items: list[ShareViewItem] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            parsed = self._to_share_view_item(raw_item)
            if parsed is not None:
                items.append(parsed)

        return items, total, safe_page, total_pages

    async def get_share_details_online(self, figi: str) -> ShareViewItem:
        figi_value = figi.strip()
        if not figi_value:
            raise RuntimeError("Пустой FIGI")
        payload = await self._get_payload(f"/api/v1/tbank/shares/{quote(figi_value, safe='')}/online")
        parsed = self._to_share_view_item(payload)
        if parsed is None:
            raise RuntimeError("Некорректный ответ backend API для live-деталей акции")
        return parsed

    async def _fetch_stored_shares_payload(self, limit: int, offset: int) -> dict:
        params = {
            "limit": str(limit),
            "offset": str(offset),
        }
        url = f"{self._base_url}/api/v1/tbank/shares/stored"
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Не удалось обратиться к backend API: {exc}") from exc

        if response.status_code != 200:
            detail = response.text.strip()
            raise RuntimeError(
                f"Backend API вернул статус {response.status_code}: {detail or 'без деталей'}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Backend API вернул невалидный JSON") from exc

        if not isinstance(payload, dict):
            raise RuntimeError("Некорректный ответ backend API")
        return payload

    async def get_sectors(self) -> list[str]:
        payload = await self._get_payload("/api/v1/tbank/sectors")
        raw = payload.get("sectors")
        if not isinstance(raw, list):
            return []
        return [str(item) for item in raw if isinstance(item, str) and item.strip()]

    async def create_order(
        self,
        *,
        telegram_user_id: int,
        figi: str,
        quantity_lots: int,
        direction: str,
        order_type: str,
        price: str | None = None,
        confirm_margin_trade: bool | None = None,
    ) -> dict:
        return await self._post_payload(
            "/api/v1/tbank/orders",
            {
                "telegram_user_id": telegram_user_id,
                "figi": figi,
                "quantity_lots": quantity_lots,
                "direction": direction,
                "order_type": order_type,
                "price": price,
                "confirm_margin_trade": confirm_margin_trade,
            },
        )

    async def create_stop_order(
        self,
        *,
        telegram_user_id: int,
        figi: str,
        quantity_lots: int,
        direction: str,
        stop_order_type: str,
        stop_price: str,
        price: str | None = None,
    ) -> dict:
        return await self._post_payload(
            "/api/v1/tbank/stop-orders",
            {
                "telegram_user_id": telegram_user_id,
                "figi": figi,
                "quantity_lots": quantity_lots,
                "direction": direction,
                "stop_order_type": stop_order_type,
                "stop_price": stop_price,
                "price": price,
            },
        )

    async def get_orders(self, telegram_user_id: int) -> dict:
        return await self._get_payload(f"/api/v1/tbank/orders?telegram_user_id={telegram_user_id}")

    async def get_stop_orders(self, telegram_user_id: int) -> dict:
        return await self._get_payload(f"/api/v1/tbank/stop-orders?telegram_user_id={telegram_user_id}")

    async def cancel_order(self, telegram_user_id: int, order_id: str) -> dict:
        return await self._post_payload(
            "/api/v1/tbank/orders/cancel",
            {
                "telegram_user_id": telegram_user_id,
                "order_id": order_id,
            },
        )

    async def cancel_stop_order(self, telegram_user_id: int, stop_order_id: str) -> dict:
        return await self._post_payload(
            "/api/v1/tbank/stop-orders/cancel",
            {
                "telegram_user_id": telegram_user_id,
                "stop_order_id": stop_order_id,
            },
        )

    async def upsert_user_credentials(self, telegram_user_id: int, tbank_token: str, tbank_account_id: str) -> dict:
        return await self._post_payload(
            "/api/v1/tbank/users/credentials",
            {
                "telegram_user_id": telegram_user_id,
                "tbank_token": tbank_token,
                "tbank_account_id": tbank_account_id,
            },
        )

    async def get_user_credentials_status(self, telegram_user_id: int) -> dict:
        return await self._get_payload(
            f"/api/v1/tbank/users/{telegram_user_id}/credentials/status"
        )

    async def create_monitor(
        self,
        *,
        telegram_user_id: int,
        figi: str,
        ticker: str | None,
        instrument_name: str | None,
        interval_minutes: int,
        threshold_percent: str,
        threshold_rub: str,
        base_price: str,
    ) -> dict:
        return await self._post_payload(
            "/api/v1/tbank/monitors",
            {
                "telegram_user_id": telegram_user_id,
                "figi": figi,
                "ticker": ticker,
                "instrument_name": instrument_name,
                "interval_minutes": interval_minutes,
                "threshold_percent": threshold_percent,
                "threshold_rub": threshold_rub,
                "base_price": base_price,
            },
        )

    async def list_monitors(self, telegram_user_id: int) -> dict:
        return await self._get_payload(f"/api/v1/tbank/monitors?telegram_user_id={telegram_user_id}")

    async def toggle_monitor(self, telegram_user_id: int, monitor_id: int, is_active: bool) -> dict:
        return await self._post_payload(
            "/api/v1/tbank/monitors/toggle",
            {
                "telegram_user_id": telegram_user_id,
                "monitor_id": monitor_id,
                "is_active": is_active,
            },
        )

    async def delete_monitor(self, telegram_user_id: int, monitor_id: int) -> dict:
        return await self._post_payload(
            "/api/v1/tbank/monitors/delete",
            {
                "telegram_user_id": telegram_user_id,
                "monitor_id": monitor_id,
            },
        )

    async def rebase_monitor(self, telegram_user_id: int, monitor_id: int) -> dict:
        return await self._post_payload(
            "/api/v1/tbank/monitors/rebase",
            {
                "telegram_user_id": telegram_user_id,
                "monitor_id": monitor_id,
            },
        )

    async def update_monitor_thresholds(
        self,
        telegram_user_id: int,
        monitor_id: int,
        threshold_percent: str,
        threshold_rub: str,
    ) -> dict:
        return await self._post_payload(
            "/api/v1/tbank/monitors/update-thresholds",
            {
                "telegram_user_id": telegram_user_id,
                "monitor_id": monitor_id,
                "threshold_percent": threshold_percent,
                "threshold_rub": threshold_rub,
            },
        )

    async def check_monitors(self, telegram_user_id: int) -> dict:
        return await self._get_payload(f"/api/v1/tbank/monitors/check?telegram_user_id={telegram_user_id}")

    async def get_portfolio(self, telegram_user_id: int) -> dict:
        return await self._post_payload(
            "/api/v1/tbank/portfolio",
            {"telegram_user_id": telegram_user_id},
        )

    async def get_operations(self, telegram_user_id: int, days: int = 30) -> dict:
        return await self._post_payload(
            "/api/v1/tbank/operations",
            {"telegram_user_id": telegram_user_id, "days": days},
        )

    async def get_favorites(self, telegram_user_id: int) -> list[str]:
        payload = await self._get_payload(f"/api/v1/tbank/favorites?telegram_user_id={telegram_user_id}")
        raw = payload.get("figies")
        if not isinstance(raw, list):
            return []
        return [str(figi).strip() for figi in raw if str(figi).strip()]

    async def add_favorite(self, telegram_user_id: int, figi: str) -> dict:
        return await self._post_payload(
            "/api/v1/tbank/favorites/add",
            {"telegram_user_id": telegram_user_id, "figi": figi},
        )

    async def remove_favorite(self, telegram_user_id: int, figi: str) -> dict:
        return await self._post_payload(
            "/api/v1/tbank/favorites/remove",
            {"telegram_user_id": telegram_user_id, "figi": figi},
        )

    async def get_all_stored_shares(self, limit: int = 3000) -> list[ShareViewItem]:
        safe_limit = max(limit, 1)
        page_size = min(safe_limit, 1000)
        all_items: list[ShareViewItem] = []
        page = 1
        total_pages = 1

        while len(all_items) < safe_limit and page <= total_pages:
            items, _, _, total_pages = await self.get_shares_page(page=page, page_size=page_size)
            if not items:
                break
            all_items.extend(items)
            if len(items) < page_size:
                break
            page += 1

        return all_items[:safe_limit]

    async def find_share_by_ticker_or_figi(self, query: str) -> ShareViewItem | None:
        normalized = query.strip().upper()
        if not normalized:
            return None
        shares = await self.get_all_stored_shares()
        for item in shares:
            if item.figi.upper() == normalized or item.ticker.upper() == normalized:
                return item
        return None

    @staticmethod
    def calc_change(current_price: str | None, base_price: Decimal) -> tuple[Decimal | None, Decimal | None]:
        if not current_price:
            return None, None
        try:
            current = Decimal(current_price)
        except Exception:
            return None, None
        if base_price == 0:
            return None, current - base_price
        percent = ((current - base_price) / base_price) * Decimal("100")
        amount = current - base_price
        return percent, amount

    async def _get_payload(self, path: str) -> dict:
        url = f"{self._base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Не удалось обратиться к backend API: {exc}") from exc
        return self._handle_response(response)

    async def _post_payload(self, path: str, payload: dict) -> dict:
        url = f"{self._base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(url, json=payload)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Не удалось обратиться к backend API: {exc}") from exc
        return self._handle_response(response)

    @staticmethod
    def _handle_response(response: httpx.Response) -> dict:
        if response.status_code >= 400:
            detail = response.text.strip()
            raise RuntimeError(
                f"Backend API вернул статус {response.status_code}: {detail or 'без деталей'}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Backend API вернул невалидный JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Некорректный ответ backend API")
        return payload

    @staticmethod
    def _safe_int(value: object) -> int | None:
        try:
            if value is None:
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_str(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _extract_nominal(item: dict) -> str | None:
        direct_nominal = TelegramSharesBrowserService._safe_str(item.get("nominal"))
        if direct_nominal:
            return direct_nominal

        raw_nominal = TelegramSharesBrowserService._extract_from_payload(item, "nominal")
        if not isinstance(raw_nominal, dict):
            return None

        units = raw_nominal.get("units")
        nano = raw_nominal.get("nano")
        currency = str(raw_nominal.get("currency", "")).strip().upper()
        if units is None and nano is None:
            return None

        units_text = str(units) if units is not None else "0"
        nano_text = ""
        if isinstance(nano, int) and nano != 0:
            nano_text = f".{abs(nano):09d}".rstrip("0")
        sign = "-" if isinstance(units, int) and units < 0 else ""
        if units_text.lstrip("-").isdigit():
            value = f"{sign}{str(abs(int(units_text)))}{nano_text}"
        else:
            value = f"{units_text}{nano_text}"
        if currency:
            return f"{value} {currency}"
        return value

    @staticmethod
    def _extract_from_payload(item: dict, key: str) -> object:
        payload = item.get("instrument_payload")
        if not isinstance(payload, dict):
            return None

        aliases = {
            "lot": ["lot"],
            "sector": ["sector"],
            "nominal": ["nominal"],
        }
        for alias in aliases.get(key, [key]):
            if alias in payload:
                return payload.get(alias)
        return None

    def _to_share_view_item(self, item: dict) -> ShareViewItem | None:
        figi = str(item.get("figi", "")).strip()
        if not figi:
            return None

        ticker = str(item.get("ticker", "")).strip() or "—"
        name = str(item.get("instrument_name", "")).strip() or "Без названия"
        currency = str(item.get("currency", "")).strip().upper() or "—"
        exchange_display = str(item.get("exchange_display", "")).strip()
        exchange_raw = str(item.get("exchange", "")).strip()
        exchange = exchange_display or exchange_raw or "—"

        return ShareViewItem(
            id=self._safe_int(item.get("id")) or 0,
            figi=figi,
            ticker=ticker,
            name=name,
            currency=currency,
            exchange=exchange,
            lot=self._safe_int(item.get("lot")) or self._safe_int(self._extract_from_payload(item, "lot")),
            nominal=self._extract_nominal(item),
            country_of_risk=str(item.get("country_of_risk", "")).strip().upper() or "—",
            buy_available=item.get("buy_available"),
            sell_available=item.get("sell_available"),
            api_trade_available=item.get("api_trade_available"),
            short_enabled=item.get("short_enabled"),
            isin=self._safe_str(item.get("isin")),
            class_code=self._safe_str(item.get("class_code")),
            sector=self._safe_str(item.get("sector")) or self._safe_str(self._extract_from_payload(item, "sector")),
            last_price=self._safe_str(item.get("last_price")),
            last_price_captured_at_msk=self._safe_str(item.get("last_price_captured_at_msk")),
            trading_open=item.get("trading_open"),
            session_opened_at_msk=self._safe_str(item.get("session_opened_at_msk")),
            session_closed_at_msk=self._safe_str(item.get("session_closed_at_msk")),
            next_session_opened_at_msk=self._safe_str(item.get("next_session_opened_at_msk")),
            next_session_closed_at_msk=self._safe_str(item.get("next_session_closed_at_msk")),
            day_open_price=self._safe_str(item.get("day_open_price")),
            day_close_price=self._safe_str(item.get("day_close_price")),
            day_change_percent=self._safe_str(item.get("day_change_percent")),
            year_change_percent=self._safe_str(item.get("year_change_percent")),
            trading_status=self._safe_str(item.get("trading_status")),
            limit_order_available=item.get("limit_order_available"),
            market_order_available=item.get("market_order_available"),
        )
