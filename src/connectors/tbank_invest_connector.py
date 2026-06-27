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


TBankInstrumentsResult = TBankSharesResult


@dataclass(slots=True)
class TBankExchangeTradingStatus:
    exchange: str
    trading_open: bool
    session_opened_at_msk: datetime | None
    session_closed_at_msk: datetime | None
    next_session_opened_at_msk: datetime | None
    next_session_closed_at_msk: datetime | None


@dataclass(slots=True)
class TBankLastPricePoint:
    figi: str
    price: Decimal
    captured_at_msk: datetime | None


@dataclass(slots=True)
class TBankInstrumentTradingStatus:
    instrument_id: str
    trading_status: str | None
    limit_order_available: bool | None
    market_order_available: bool | None
    api_trade_available: bool | None


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
        return await self.get_instruments(
            instrument_type="share",
            instrument_status=instrument_status,
            instrument_exchange=instrument_exchange,
        )

    async def get_bonds(
        self,
        instrument_status: str = "INSTRUMENT_STATUS_BASE",
        instrument_exchange: str = "INSTRUMENT_EXCHANGE_UNSPECIFIED",
    ) -> TBankInstrumentsResult:
        return await self.get_instruments(
            instrument_type="bond",
            instrument_status=instrument_status,
            instrument_exchange=instrument_exchange,
        )

    async def get_etfs(
        self,
        instrument_status: str = "INSTRUMENT_STATUS_BASE",
        instrument_exchange: str = "INSTRUMENT_EXCHANGE_UNSPECIFIED",
    ) -> TBankInstrumentsResult:
        return await self.get_instruments(
            instrument_type="etf",
            instrument_status=instrument_status,
            instrument_exchange=instrument_exchange,
        )

    async def get_instruments(
        self,
        instrument_type: str,
        instrument_status: str = "INSTRUMENT_STATUS_BASE",
        instrument_exchange: str = "INSTRUMENT_EXCHANGE_UNSPECIFIED",
    ) -> TBankInstrumentsResult:
        if not self.token:
            raise TBankInvestRequestError("T-Bank token is not configured")

        endpoint_by_type = {
            "share": "Shares",
            "bond": "Bonds",
            "etf": "Etfs",
        }
        endpoint_name = endpoint_by_type.get(instrument_type.strip().lower())
        if endpoint_name is None:
            raise TBankInvestRequestError(f"Unsupported instrument_type '{instrument_type}'")

        payload = await self._request_json(
            f"/rest/tinkoff.public.invest.api.contract.v1.InstrumentsService/{endpoint_name}",
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
        return TBankInstrumentsResult(instruments=instruments, raw_payload=payload)

    async def get_last_prices(self, figies: list[str]) -> dict[str, Decimal]:
        points = await self.get_last_price_points(figies)
        return {figi: point.price for figi, point in points.items()}

    async def get_last_price_points(self, figies: list[str]) -> dict[str, TBankLastPricePoint]:
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

        prices: dict[str, TBankLastPricePoint] = {}
        for item in raw_prices:
            if not isinstance(item, dict):
                continue
            figi = str(item.get("figi", "")).strip()
            if not figi:
                continue
            price_value = self._quotation_to_decimal(item.get("price"))
            if price_value is None:
                continue
            captured_at_msk: datetime | None = None
            parsed_time = self._parse_iso_datetime(item.get("time"))
            if parsed_time is not None:
                captured_at_msk = self._to_msk_naive(parsed_time)
            prices[figi] = TBankLastPricePoint(
                figi=figi,
                price=price_value,
                captured_at_msk=captured_at_msk,
            )

        return prices

    async def get_share_by_figi(self, figi: str) -> dict[str, Any]:
        return await self.get_instrument_by_figi(figi=figi, instrument_type="share")

    async def get_bond_by_figi(self, figi: str) -> dict[str, Any]:
        return await self.get_instrument_by_figi(figi=figi, instrument_type="bond")

    async def get_etf_by_figi(self, figi: str) -> dict[str, Any]:
        return await self.get_instrument_by_figi(figi=figi, instrument_type="etf")

    async def get_instrument_by_figi(
        self,
        figi: str,
        instrument_type: str | None = None,
    ) -> dict[str, Any]:
        figi_value = figi.strip()
        if not figi_value:
            raise TBankInvestRequestError("figi is required")

        types_to_try = [instrument_type.strip().lower()] if instrument_type else ["share", "bond", "etf"]
        types_to_try = [item for item in types_to_try if item]
        endpoint_by_type = {
            "share": "ShareBy",
            "bond": "BondBy",
            "etf": "EtfBy",
        }
        errors: list[TBankInvestRequestError] = []

        for current_type in types_to_try:
            endpoint_name = endpoint_by_type.get(current_type)
            if endpoint_name is None:
                continue
            try:
                payload = await self._request_json(
                    f"/rest/tinkoff.public.invest.api.contract.v1.InstrumentsService/{endpoint_name}",
                    {
                        "idType": "INSTRUMENT_ID_TYPE_FIGI",
                        "id": figi_value,
                    },
                )
                instrument = payload.get("instrument")
                if isinstance(instrument, dict):
                    instrument.setdefault("instrumentType", current_type)
                    return instrument
            except TBankInvestRequestError as exc:
                errors.append(exc)
                exc_text = str(exc).lower()
                if "token is not configured" in exc_text or "http 401" in exc_text or "http 403" in exc_text:
                    raise

        # Fallback for API environments where *By methods may be unavailable.
        instruments = []
        for current_type in types_to_try:
            try:
                base = await self.get_instruments(
                    instrument_type=current_type,
                    instrument_status="INSTRUMENT_STATUS_ALL",
                    instrument_exchange="INSTRUMENT_EXCHANGE_UNSPECIFIED",
                )
                for item in base.instruments:
                    item.setdefault("instrumentType", current_type)
                instruments.extend(base.instruments)
            except TBankInvestRequestError as exc:
                errors.append(exc)
                exc_text = str(exc).lower()
                if "token is not configured" in exc_text or "http 401" in exc_text or "http 403" in exc_text:
                    raise
            try:
                dealer = await self.get_instruments(
                    instrument_type=current_type,
                    instrument_status="INSTRUMENT_STATUS_ALL",
                    instrument_exchange="INSTRUMENT_EXCHANGE_DEALER",
                )
                for item in dealer.instruments:
                    item.setdefault("instrumentType", current_type)
                instruments.extend(dealer.instruments)
            except TBankInvestRequestError as exc:
                errors.append(exc)
                exc_text = str(exc).lower()
                if "token is not configured" in exc_text or "http 401" in exc_text or "http 403" in exc_text:
                    raise

        for instrument in instruments:
            if not isinstance(instrument, dict):
                continue
            if str(instrument.get("figi", "")).strip() == figi_value:
                return instrument

        if errors and not instruments:
            raise errors[-1]
        raise TBankInvestRequestError(f"Instrument with FIGI '{figi_value}' not found")

    async def get_dividends(
        self,
        *,
        figi: str,
        from_date: datetime,
        to_date: datetime,
    ) -> list[dict[str, Any]]:
        payload = await self._request_json(
            "/rest/tinkoff.public.invest.api.contract.v1.InstrumentsService/GetDividends",
            {
                "instrumentId": figi.strip(),
                "from": self._to_utc_rfc3339(from_date),
                "to": self._to_utc_rfc3339(to_date),
            },
        )
        raw_items = payload.get("dividends")
        return raw_items if isinstance(raw_items, list) else []

    async def get_bond_coupons(
        self,
        *,
        figi: str,
        from_date: datetime,
        to_date: datetime,
    ) -> list[dict[str, Any]]:
        payload = await self._request_json(
            "/rest/tinkoff.public.invest.api.contract.v1.InstrumentsService/GetBondCoupons",
            {
                "instrumentId": figi.strip(),
                "from": self._to_utc_rfc3339(from_date),
                "to": self._to_utc_rfc3339(to_date),
            },
        )
        raw_items = payload.get("events")
        if isinstance(raw_items, list):
            return raw_items
        raw_items = payload.get("coupons")
        return raw_items if isinstance(raw_items, list) else []

    async def get_bond_events(
        self,
        *,
        figi: str,
        from_date: datetime,
        to_date: datetime,
    ) -> list[dict[str, Any]]:
        payload = await self._request_json(
            "/rest/tinkoff.public.invest.api.contract.v1.InstrumentsService/GetBondEvents",
            {
                "instrumentId": figi.strip(),
                "from": self._to_utc_rfc3339(from_date),
                "to": self._to_utc_rfc3339(to_date),
            },
        )
        raw_items = payload.get("events")
        return raw_items if isinstance(raw_items, list) else []

    async def get_daily_metrics(
        self,
        *,
        figi: str,
        current_price: Decimal | None = None,
    ) -> dict[str, Decimal | None]:
        figi_value = figi.strip()
        if not figi_value:
            return {
                "day_open_price": None,
                "day_close_price": None,
                "day_change_percent": None,
                "year_change_percent": None,
            }

        now_utc = datetime.now(timezone.utc)
        from_utc = now_utc - timedelta(days=400)
        payload = await self._request_json(
            "/rest/tinkoff.public.invest.api.contract.v1.MarketDataService/GetCandles",
            {
                "instrumentId": figi_value,
                "from": self._to_utc_rfc3339(from_utc),
                "to": self._to_utc_rfc3339(now_utc),
                "interval": "CANDLE_INTERVAL_DAY",
            },
        )
        raw_candles = payload.get("candles")
        if not isinstance(raw_candles, list):
            return {
                "day_open_price": None,
                "day_close_price": None,
                "day_change_percent": None,
                "year_change_percent": None,
            }

        candles: list[tuple[datetime, Decimal, Decimal]] = []
        for candle in raw_candles:
            if not isinstance(candle, dict):
                continue
            candle_time = self._parse_iso_datetime(candle.get("time"))
            open_price = self._quotation_to_decimal(candle.get("open"))
            close_price = self._quotation_to_decimal(candle.get("close"))
            if candle_time is None or open_price is None or close_price is None:
                continue
            candles.append((candle_time, open_price, close_price))

        if not candles:
            return {
                "day_open_price": None,
                "day_close_price": None,
                "day_change_percent": None,
                "year_change_percent": None,
            }

        candles.sort(key=lambda item: item[0])
        _latest_time, latest_open, latest_close = candles[-1]
        previous_close = candles[-2][2] if len(candles) > 1 else None

        reference_price = current_price if isinstance(current_price, Decimal) else latest_close
        day_change_percent: Decimal | None = None
        if latest_open and latest_open != 0:
            day_change_percent = ((reference_price - latest_open) / latest_open) * Decimal("100")

        one_year_ago = now_utc - timedelta(days=365)
        year_base: Decimal | None = None
        for candle_time, _, candle_close in candles:
            if candle_time >= one_year_ago:
                year_base = candle_close
                break
        if year_base is None:
            year_base = candles[0][2]

        year_change_percent: Decimal | None = None
        if isinstance(year_base, Decimal) and year_base != 0:
            year_change_percent = ((reference_price - year_base) / year_base) * Decimal("100")

        return {
            "day_open_price": latest_open,
            "day_close_price": previous_close,
            "day_change_percent": day_change_percent,
            "year_change_percent": year_change_percent,
        }

    async def get_trading_status(self, instrument_id: str) -> TBankInstrumentTradingStatus:
        instrument_id_value = instrument_id.strip()
        if not instrument_id_value:
            raise TBankInvestRequestError("instrument_id is required")

        payload = await self._request_json(
            "/rest/tinkoff.public.invest.api.contract.v1.MarketDataService/GetTradingStatus",
            {"instrumentId": instrument_id_value},
        )
        raw_status = payload.get("tradingStatus")
        status_value = str(raw_status).strip() if raw_status is not None else ""

        return TBankInstrumentTradingStatus(
            instrument_id=str(payload.get("figi") or instrument_id_value).strip() or instrument_id_value,
            trading_status=status_value or None,
            limit_order_available=self._to_optional_bool(payload.get("limitOrderAvailableFlag")),
            market_order_available=self._to_optional_bool(payload.get("marketOrderAvailableFlag")),
            api_trade_available=self._to_optional_bool(payload.get("apiTradeAvailableFlag")),
        )

    async def is_exchange_trading_open(self, exchange: str = "MOEX") -> bool:
        status = await self.get_exchange_trading_status(exchange=exchange)
        return status.trading_open

    async def get_exchange_trading_status(self, exchange: str = "MOEX") -> TBankExchangeTradingStatus:
        if not self.token:
            raise TBankInvestRequestError("T-Bank token is not configured")

        now_utc = datetime.now(timezone.utc)
        day_start_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        windows = [
            (day_start_utc, day_start_utc + timedelta(days=7)),
            (now_utc, now_utc + timedelta(days=7)),
        ]

        payload: dict[str, Any] | None = None
        last_error: TBankInvestRequestError | None = None
        for from_utc, to_utc in windows:
            try:
                payload = await self._request_json(
                    "/rest/tinkoff.public.invest.api.contract.v1.InstrumentsService/TradingSchedules",
                    {
                        "exchange": exchange,
                        "from": self._to_utc_rfc3339(from_utc),
                        "to": self._to_utc_rfc3339(to_utc),
                    },
                )
                break
            except TBankInvestRequestError as exc:
                last_error = exc

        if payload is None:
            if last_error is not None:
                raise last_error
            raise TBankInvestRequestError("Unable to resolve trading schedule")

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
        confirm_margin_trade: bool | None = None,
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
        if confirm_margin_trade is not None:
            payload["confirmMarginTrade"] = bool(confirm_margin_trade)

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
        exchange_order_type: str | None = None,
        order_id: str | None = None,
        price_type: str | None = "PRICE_TYPE_CURRENCY",
        confirm_margin_trade: bool | None = None,
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
            "orderId": order_id or str(uuid.uuid4()),
        }
        if exchange_order_type:
            payload["exchangeOrderType"] = exchange_order_type
        if price_type:
            payload["priceType"] = price_type
        if confirm_margin_trade is not None:
            payload["confirmMarginTrade"] = bool(confirm_margin_trade)
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
        if isinstance(raw_value, datetime):
            if raw_value.tzinfo is None:
                return raw_value.replace(tzinfo=timezone.utc)
            return raw_value.astimezone(timezone.utc)
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
            ("mainStartTime", "mainEndTime"),
            ("eveningStartTime", "eveningEndTime"),
            ("preMarketStartTime", "preMarketEndTime"),
            ("afterHoursStartTime", "afterHoursEndTime"),
            ("start", "end"),
        ]
        for start_key, end_key in candidate_keys:
            if start_key in item and end_key in item:
                pairs.append((item.get(start_key), item.get(end_key)))

        # Fallback: derive an interval from all timestamp-like fields when explicit pairs are absent.
        if not pairs:
            times: list[datetime] = []
            for key, raw_value in item.items():
                if not isinstance(key, str):
                    continue
                if "time" not in key.lower():
                    continue
                parsed = TBankInvestConnector._parse_iso_datetime(raw_value)
                if parsed is not None:
                    times.append(parsed)
            if len(times) >= 2:
                times.sort()
                pairs.append((times[0], times[-1]))
        return pairs

    @staticmethod
    def _to_optional_bool(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        return None
