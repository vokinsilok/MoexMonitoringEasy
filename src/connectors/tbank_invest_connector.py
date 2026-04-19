import asyncio
import json
import ssl
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


@dataclass(slots=True)
class TBankSharesResult:
    instruments: list[dict[str, Any]]
    raw_payload: dict[str, Any]


@dataclass(slots=True)
class TBankExchangeTradingStatus:
    exchange: str
    trading_open: bool
    session_opened_at_msk: datetime | None
    session_closed_at_msk: datetime | None
    next_session_opened_at_msk: datetime | None
    next_session_closed_at_msk: datetime | None


class TBankInvestRequestError(RuntimeError):
    pass


class TBankInvestConnector:
    def __init__(
        self,
        token: str,
        base_url: str = "https://invest-public-api.tbank.ru",
        timeout: float = 10.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
        ssl_verify: bool = True,
        ca_bundle_path: str | None = None,
    ) -> None:
        self.token = token.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.ssl_verify = ssl_verify
        self.ca_bundle_path = ca_bundle_path

    async def get_shares(
        self,
        instrument_status: str = "INSTRUMENT_STATUS_BASE",
        instrument_exchange: str = "INSTRUMENT_EXCHANGE_UNSPECIFIED",
    ) -> TBankSharesResult:
        if not self.token:
            raise TBankInvestRequestError("T-Bank token is not configured")

        payload = await self._request_json(
            "/rest/tinkoff.public.invest.api.contract.v1.InstrumentsService/Shares",
            {
                "instrumentStatus": instrument_status,
                "instrumentExchange": instrument_exchange,
            },
        )
        instruments = payload.get("instruments")
        if not isinstance(instruments, list):
            raise TBankInvestRequestError(
                "Unexpected T-Bank response shape: 'instruments' is missing"
            )
        return TBankSharesResult(instruments=instruments, raw_payload=payload)

    async def get_last_prices(self, figies: list[str]) -> dict[str, Decimal]:
        unique_figies = list(dict.fromkeys([figi.strip() for figi in figies if figi and figi.strip()]))
        if not unique_figies:
            return {}

        payload = await self._request_json(
            "/rest/tinkoff.public.invest.api.contract.v1.MarketDataService/GetLastPrices",
            {"instrumentId": unique_figies},
        )
        raw_prices = payload.get("lastPrices")
        if not isinstance(raw_prices, list):
            raise TBankInvestRequestError(
                "Unexpected T-Bank response shape: 'lastPrices' is missing"
            )

        prices: dict[str, Decimal] = {}
        for item in raw_prices:
            if not isinstance(item, dict):
                continue
            figi = str(item.get("figi", "")).strip()
            if not figi:
                continue
            price_value = self._quotation_to_decimal(item.get("price"))
            if price_value is None:
                continue
            prices[figi] = price_value

        return prices

    async def is_exchange_trading_open(self, exchange: str = "MOEX") -> bool:
        status = await self.get_exchange_trading_status(exchange=exchange)
        return status.trading_open

    async def get_exchange_trading_status(self, exchange: str = "MOEX") -> TBankExchangeTradingStatus:
        if not self.token:
            raise TBankInvestRequestError("T-Bank token is not configured")

        now_utc = datetime.now(timezone.utc)
        from_utc = now_utc
        to_utc = now_utc + timedelta(days=3)

        payload = await self._request_json(
            "/rest/tinkoff.public.invest.api.contract.v1.InstrumentsService/TradingSchedules",
            {
                "exchange": exchange,
                "from": self._to_utc_rfc3339(from_utc),
                "to": self._to_utc_rfc3339(to_utc),
            },
        )

        raw_exchanges = payload.get("exchanges")
        if not isinstance(raw_exchanges, list):
            raise TBankInvestRequestError(
                "Unexpected T-Bank response shape: 'exchanges' is missing"
            )

        intervals: list[tuple[datetime, datetime]] = []
        for exchange_item in raw_exchanges:
            if not isinstance(exchange_item, dict):
                continue
            raw_days = exchange_item.get("days")
            if not isinstance(raw_days, list):
                continue

            for day_item in raw_days:
                if not isinstance(day_item, dict):
                    continue
                if day_item.get("isTradingDay") is False:
                    continue
                intervals.extend(self._collect_day_intervals(day_item))

        current_interval: tuple[datetime, datetime] | None = None
        previous_interval: tuple[datetime, datetime] | None = None
        next_interval: tuple[datetime, datetime] | None = None

        for start_utc, end_utc in sorted(intervals, key=lambda pair: pair[0]):
            if start_utc <= now_utc <= end_utc:
                current_interval = (start_utc, end_utc)
                break
            if end_utc < now_utc:
                previous_interval = (start_utc, end_utc)
                continue
            if start_utc > now_utc and next_interval is None:
                next_interval = (start_utc, end_utc)

        if current_interval is not None:
            return TBankExchangeTradingStatus(
                exchange=exchange,
                trading_open=True,
                session_opened_at_msk=self._to_msk_naive(current_interval[0]),
                session_closed_at_msk=self._to_msk_naive(current_interval[1]),
                next_session_opened_at_msk=self._to_msk_naive(next_interval[0]) if next_interval else None,
                next_session_closed_at_msk=self._to_msk_naive(next_interval[1]) if next_interval else None,
            )

        return TBankExchangeTradingStatus(
            exchange=exchange,
            trading_open=False,
            session_opened_at_msk=self._to_msk_naive(previous_interval[0]) if previous_interval else None,
            session_closed_at_msk=self._to_msk_naive(previous_interval[1]) if previous_interval else None,
            next_session_opened_at_msk=self._to_msk_naive(next_interval[0]) if next_interval else None,
            next_session_closed_at_msk=self._to_msk_naive(next_interval[1]) if next_interval else None,
        )

    async def post_order(
        self,
        *,
        account_id: str,
        instrument_id: str,
        quantity_lots: int,
        direction: str,
        order_type: str,
        price: Decimal | None = None,
        order_id: str | None = None,
    ) -> dict[str, Any]:
        if quantity_lots <= 0:
            raise TBankInvestRequestError("quantity_lots must be greater than 0")
        if not account_id.strip():
            raise TBankInvestRequestError("account_id is required")
        if not instrument_id.strip():
            raise TBankInvestRequestError("instrument_id is required")

        payload: dict[str, Any] = {
            "quantity": str(quantity_lots),
            "direction": direction,
            "accountId": account_id.strip(),
            "orderType": order_type,
            "orderId": order_id or str(uuid.uuid4()),
            "instrumentId": instrument_id.strip(),
        }
        if price is not None:
            payload["price"] = self._decimal_to_quotation(price)

        return await self._request_json(
            "/rest/tinkoff.public.invest.api.contract.v1.OrdersService/PostOrder",
            payload,
        )

    async def get_orders(self, *, account_id: str) -> dict[str, Any]:
        if not account_id.strip():
            raise TBankInvestRequestError("account_id is required")
        return await self._request_json(
            "/rest/tinkoff.public.invest.api.contract.v1.OrdersService/GetOrders",
            {"accountId": account_id.strip()},
        )

    async def cancel_order(self, *, account_id: str, order_id: str) -> dict[str, Any]:
        if not account_id.strip():
            raise TBankInvestRequestError("account_id is required")
        if not order_id.strip():
            raise TBankInvestRequestError("order_id is required")
        return await self._request_json(
            "/rest/tinkoff.public.invest.api.contract.v1.OrdersService/CancelOrder",
            {
                "accountId": account_id.strip(),
                "orderId": order_id.strip(),
            },
        )

    async def post_stop_order(
        self,
        *,
        account_id: str,
        instrument_id: str,
        quantity_lots: int,
        direction: str,
        stop_order_type: str,
        stop_price: Decimal,
        expiration_type: str = "STOP_ORDER_EXPIRATION_TYPE_GOOD_TILL_CANCEL",
        price: Decimal | None = None,
        expire_date: datetime | None = None,
    ) -> dict[str, Any]:
        if quantity_lots <= 0:
            raise TBankInvestRequestError("quantity_lots must be greater than 0")
        if not account_id.strip():
            raise TBankInvestRequestError("account_id is required")
        if not instrument_id.strip():
            raise TBankInvestRequestError("instrument_id is required")

        payload: dict[str, Any] = {
            "quantity": str(quantity_lots),
            "price": self._decimal_to_quotation(price) if price is not None else None,
            "stopPrice": self._decimal_to_quotation(stop_price),
            "direction": direction,
            "accountId": account_id.strip(),
            "expirationType": expiration_type,
            "stopOrderType": stop_order_type,
            "instrumentId": instrument_id.strip(),
        }
        if expire_date is not None:
            payload["expireDate"] = self._to_utc_rfc3339(expire_date)
        if payload["price"] is None:
            payload.pop("price")

        return await self._request_json(
            "/rest/tinkoff.public.invest.api.contract.v1.StopOrdersService/PostStopOrder",
            payload,
        )

    async def get_stop_orders(self, *, account_id: str) -> dict[str, Any]:
        if not account_id.strip():
            raise TBankInvestRequestError("account_id is required")
        return await self._request_json(
            "/rest/tinkoff.public.invest.api.contract.v1.StopOrdersService/GetStopOrders",
            {"accountId": account_id.strip()},
        )

    async def cancel_stop_order(self, *, account_id: str, stop_order_id: str) -> dict[str, Any]:
        if not account_id.strip():
            raise TBankInvestRequestError("account_id is required")
        if not stop_order_id.strip():
            raise TBankInvestRequestError("stop_order_id is required")
        return await self._request_json(
            "/rest/tinkoff.public.invest.api.contract.v1.StopOrdersService/CancelStopOrder",
            {
                "accountId": account_id.strip(),
                "stopOrderId": stop_order_id.strip(),
            },
        )

    async def get_portfolio(self, *, account_id: str) -> dict[str, Any]:
        if not account_id.strip():
            raise TBankInvestRequestError("account_id is required")
        return await self._request_json(
            "/rest/tinkoff.public.invest.api.contract.v1.OperationsService/GetPortfolio",
            {"accountId": account_id.strip()},
        )

    async def get_operations(self, *, account_id: str, days: int = 30) -> dict[str, Any]:
        if not account_id.strip():
            raise TBankInvestRequestError("account_id is required")
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=max(1, days))
        return await self._request_json(
            "/rest/tinkoff.public.invest.api.contract.v1.OperationsService/GetOperations",
            {
                "accountId": account_id.strip(),
                "from": self._to_utc_rfc3339(start),
                "to": self._to_utc_rfc3339(now),
            },
        )

    async def _request_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        clean_path = path[1:] if path.startswith("/") else path
        request_url = f"{self.base_url}/{clean_path}"
        request_body = json.dumps(payload).encode("utf-8")

        def fetch() -> dict[str, Any]:
            request = Request(
                request_url,
                data=request_body,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "MoexMonitorEasy/1.0",
                    "Connection": "close",
                },
            )
            context = self._ssl_context()
            with urlopen(request, timeout=self.timeout, context=context) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                content = response.read().decode(charset)
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise TBankInvestRequestError("Unexpected T-Bank response shape")
            return parsed

        last_exception: Exception | None = None
        total_timeout = (
            (self.timeout * self.max_retries)
            + (self.retry_backoff_seconds * max(self.max_retries - 1, 0))
            + 1.0
        )

        try:
            async with asyncio.timeout(total_timeout):
                for attempt in range(1, self.max_retries + 1):
                    try:
                        return await asyncio.to_thread(fetch)
                    except HTTPError as exc:
                        last_exception = exc
                        if 500 <= exc.code < 600 and attempt < self.max_retries:
                            await asyncio.sleep(self.retry_backoff_seconds * attempt)
                            continue
                        message = self._http_error_message(exc)
                        raise TBankInvestRequestError(message) from exc
                    except json.JSONDecodeError as exc:
                        raise TBankInvestRequestError("T-Bank returned non-JSON payload") from exc
                    except (URLError, TimeoutError) as exc:
                        last_exception = exc
                        if attempt < self.max_retries:
                            await asyncio.sleep(self.retry_backoff_seconds * attempt)
                            continue
        except TimeoutError as exc:
            raise TBankInvestRequestError(
                f"T-Bank request exceeded {total_timeout:.1f}s budget"
            ) from exc

        raise TBankInvestRequestError(
            f"Unable to reach T-Bank API: {self._exc_text(last_exception)}"
        )

    @staticmethod
    def _http_error_message(exc: HTTPError) -> str:
        try:
            raw = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            raw = ""
        raw = raw.strip()
        if raw:
            return f"T-Bank API returned HTTP {exc.code}: {raw}"
        return f"T-Bank API returned HTTP {exc.code}"

    @staticmethod
    def _exc_text(exc: Exception | None) -> str:
        if exc is None:
            return "unknown network error"
        if isinstance(exc, URLError):
            return str(exc.reason)
        return str(exc)

    def _ssl_context(self) -> ssl.SSLContext:
        if not self.ssl_verify:
            return ssl._create_unverified_context()
        if self.ca_bundle_path:
            return ssl.create_default_context(cafile=self.ca_bundle_path)
        return ssl.create_default_context()

    @staticmethod
    def _quotation_to_decimal(raw_price: Any) -> Decimal | None:
        if not isinstance(raw_price, dict):
            return None

        units_raw = raw_price.get("units")
        nano_raw = raw_price.get("nano", 0)
        try:
            units = int(units_raw)
            nano = int(nano_raw)
        except (TypeError, ValueError):
            return None

        return Decimal(units) + (Decimal(nano) / Decimal(1_000_000_000))

    @staticmethod
    def _decimal_to_quotation(value: Decimal) -> dict[str, int | str]:
        sign = -1 if value < 0 else 1
        abs_value = abs(value)
        units = int(abs_value)
        nanos_decimal = (abs_value - Decimal(units)) * Decimal(1_000_000_000)
        nanos = int(nanos_decimal.quantize(Decimal("1")))
        if sign < 0:
            units *= -1
            nanos *= -1
        return {"units": str(units), "nano": nanos}

    @staticmethod
    def _to_utc_rfc3339(value: datetime) -> str:
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _parse_iso_datetime(raw_value: Any) -> datetime | None:
        if not isinstance(raw_value, str):
            return None

        value = raw_value.strip()
        if not value:
            return None
        if value.endswith("Z"):
            value = f"{value[:-1]}+00:00"

        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _is_today_session(self, day_item: dict[str, Any], today_msk: date) -> bool:
        raw_date = day_item.get("date")
        parsed_date = self._parse_iso_datetime(raw_date)
        if parsed_date is None:
            return raw_date in (None, "")
        return parsed_date.astimezone(ZoneInfo("Europe/Moscow")).date() == today_msk

    def _is_day_open_now(self, day_item: dict[str, Any], now_utc: datetime) -> bool:
        is_trading_day = day_item.get("isTradingDay")
        if is_trading_day is False:
            return False

        intervals: list[tuple[Any, Any]] = []
        intervals.extend(self._extract_interval_pairs(day_item))

        raw_sessions = day_item.get("sessions")
        if isinstance(raw_sessions, list):
            for session_item in raw_sessions:
                if not isinstance(session_item, dict):
                    continue
                if session_item.get("isTradingSession") is False:
                    continue
                intervals.extend(self._extract_interval_pairs(session_item))

        if not intervals:
            return bool(is_trading_day)

        for raw_start, raw_end in intervals:
            start = self._parse_iso_datetime(raw_start)
            end = self._parse_iso_datetime(raw_end)
            if start is None or end is None:
                continue
            if start <= now_utc <= end:
                return True
        return False

    def _collect_day_intervals(self, day_item: dict[str, Any]) -> list[tuple[datetime, datetime]]:
        intervals: list[tuple[datetime, datetime]] = []
        for raw_start, raw_end in self._extract_interval_pairs(day_item):
            start = self._parse_iso_datetime(raw_start)
            end = self._parse_iso_datetime(raw_end)
            if start is None or end is None:
                continue
            if start <= end:
                intervals.append((start, end))

        raw_sessions = day_item.get("sessions")
        if isinstance(raw_sessions, list):
            for session_item in raw_sessions:
                if not isinstance(session_item, dict):
                    continue
                if session_item.get("isTradingSession") is False:
                    continue
                for raw_start, raw_end in self._extract_interval_pairs(session_item):
                    start = self._parse_iso_datetime(raw_start)
                    end = self._parse_iso_datetime(raw_end)
                    if start is None or end is None:
                        continue
                    if start <= end:
                        intervals.append((start, end))

        return intervals

    @staticmethod
    def _to_msk_naive(value: datetime) -> datetime:
        return value.astimezone(ZoneInfo("Europe/Moscow")).replace(tzinfo=None)

    @staticmethod
    def _extract_interval_pairs(item: dict[str, Any]) -> list[tuple[Any, Any]]:
        pairs: list[tuple[Any, Any]] = []
        candidate_keys = [
            ("startTime", "endTime"),
            ("openingTime", "closingTime"),
            ("start", "end"),
        ]
        for start_key, end_key in candidate_keys:
            if start_key in item and end_key in item:
                pairs.append((item.get(start_key), item.get(end_key)))
        return pairs
