import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from src.connectors.ai_gateway_connector import AIGatewayConnector
from src.connectors.tbank_invest_connector import (
    TBankInstrumentTradingStatus,
    TBankInvestConnector,
    TBankInvestRequestError,
)
from src.connectors.redis_connector import RedisManager
from src.moduls.tbank.schemas import (
    TBankMonitorGlobalCheckResponse,
    TBankMonitorCheckResponse,
    TBankMonitorCreateRequest,
    TBankMonitorItem,
    TBankMonitorListResponse,
    TBankMonitorTriggeredGlobalEvent,
    TBankMonitorTriggeredEvent,
    TBankPortfolioAnalysisResponse,
    TBankShareItem,
    TBankSharesResponse,
    TBankStoredShareItem,
    TBankStoredSharesResponse,
    TBankSyncResponse,
)
from src.utils.db_manager import DBManager


class TBankSharesService:
    _DETAILS_CACHE_TTL_SECONDS = 24 * 60 * 60
    _PRICE_CACHE_TTL_SECONDS = 45
    _TRADING_STATUS_CACHE_TTL_SECONDS = 30

    def __init__(
        self,
        connector: TBankInvestConnector,
        db: DBManager | None = None,
        cache: RedisManager | None = None,
    ) -> None:
        self.connector = connector
        self.db = db
        self.cache = cache

    async def get_shares(
        self,
        instrument_status: str = "INSTRUMENT_STATUS_BASE",
        instrument_exchange: str = "INSTRUMENT_EXCHANGE_UNSPECIFIED",
        russian_only: bool = True,
        include_dealer: bool = True,
    ) -> TBankSharesResponse:
        return await self.get_instruments(
            instrument_types=["share"],
            instrument_status=instrument_status,
            instrument_exchange=instrument_exchange,
            russian_only=russian_only,
            include_dealer=include_dealer,
        )

    async def get_instruments(
        self,
        instrument_types: list[str] | None = None,
        instrument_status: str = "INSTRUMENT_STATUS_BASE",
        instrument_exchange: str = "INSTRUMENT_EXCHANGE_UNSPECIFIED",
        russian_only: bool = True,
        include_dealer: bool = True,
    ) -> TBankSharesResponse:
        normalized_types = self._normalize_instrument_types(instrument_types)
        instruments = await self._load_instruments(
            instrument_types=normalized_types,
            instrument_status=instrument_status,
            instrument_exchange=instrument_exchange,
            include_dealer=include_dealer,
        )
        if russian_only:
            instruments = [item for item in instruments if self._is_russian_instrument(item)]

        items = [TBankShareItem(instrument=item) for item in instruments]
        return TBankSharesResponse(
            total=len(items),
            instrument_status=instrument_status,
            instrument_exchange=instrument_exchange,
            items=items,
        )

    async def sync_shares_to_db(
        self,
        instrument_status: str = "INSTRUMENT_STATUS_BASE",
        instrument_exchange: str = "INSTRUMENT_EXCHANGE_UNSPECIFIED",
        russian_only: bool = True,
        check_trading_open: bool = True,
        include_dealer: bool = True,
    ) -> TBankSyncResponse:
        return await self.sync_instruments_to_db(
            instrument_types=["share"],
            instrument_status=instrument_status,
            instrument_exchange=instrument_exchange,
            russian_only=russian_only,
            check_trading_open=check_trading_open,
            include_dealer=include_dealer,
        )

    async def sync_instruments_to_db(
        self,
        instrument_types: list[str] | None = None,
        instrument_status: str = "INSTRUMENT_STATUS_BASE",
        instrument_exchange: str = "INSTRUMENT_EXCHANGE_UNSPECIFIED",
        russian_only: bool = True,
        check_trading_open: bool = True,
        include_dealer: bool = True,
    ) -> TBankSyncResponse:
        if self.db is None:
            raise RuntimeError("DB manager is not configured for sync operation")

        normalized_types = self._normalize_instrument_types(instrument_types)
        instruments = await self._load_instruments(
            instrument_types=normalized_types,
            instrument_status=instrument_status,
            instrument_exchange=instrument_exchange,
            include_dealer=include_dealer,
        )
        if russian_only:
            instruments = [item for item in instruments if self._is_russian_instrument(item)]

        rows = [self._to_storage_row(item) for item in instruments]
        rows = [row for row in rows if row["figi"]]

        await self.db.tbank_share.mark_all_inactive(normalized_types)
        synced = await self.db.tbank_share.upsert_many(rows)
        prices_saved = 0
        trading_open = True

        if synced > 0:
            figies = [row["figi"] for row in rows]
            await self._cache_instrument_details(instruments)
            last_prices = await self.connector.get_last_prices(figies)
            await self._cache_last_prices(last_prices)
            prices_saved = len(last_prices)

        if check_trading_open:
            try:
                trading_status = await self.connector.get_exchange_trading_status(exchange="MOEX")
                trading_open = trading_status.trading_open
                await self._cache_trading_status(trading_status)
            except TBankInvestRequestError:
                trading_open = True

        return TBankSyncResponse(
            synced=synced,
            prices_saved=prices_saved,
            instrument_status=instrument_status,
            instrument_exchange=instrument_exchange,
            instrument_types=normalized_types,
            trading_open=trading_open,
            skipped=False,
            skip_reason=None,
        )

    async def _load_instruments(
        self,
        *,
        instrument_types: list[str],
        instrument_status: str,
        instrument_exchange: str,
        include_dealer: bool,
    ) -> list[dict]:
        instruments: list[dict] = []
        for instrument_type in instrument_types:
            primary_result = await self.connector.get_instruments(
                instrument_type=instrument_type,
                instrument_status=instrument_status,
                instrument_exchange=instrument_exchange,
            )
            for item in primary_result.instruments:
                item.setdefault("instrumentType", instrument_type)
            instruments.extend(primary_result.instruments)

        should_load_dealer = include_dealer and instrument_exchange == "INSTRUMENT_EXCHANGE_UNSPECIFIED"
        if not should_load_dealer:
            return instruments

        # Внебиржевые инструменты (dealer) запрашиваются отдельным фильтром и
        # объединяются по FIGI, чтобы мониторинг видел обе площадки.
        try:
            dealer_instruments = []
            for instrument_type in instrument_types:
                dealer_result = await self.connector.get_instruments(
                    instrument_type=instrument_type,
                    instrument_status="INSTRUMENT_STATUS_ALL",
                    instrument_exchange="INSTRUMENT_EXCHANGE_DEALER",
                )
                for item in dealer_result.instruments:
                    item.setdefault("instrumentType", instrument_type)
                dealer_instruments.extend(dealer_result.instruments)
        except TBankInvestRequestError:
            dealer_instruments = []
        by_figi: dict[str, dict] = {}
        for instrument in instruments + dealer_instruments:
            figi = str(instrument.get("figi", "")).strip()
            if not figi:
                continue
            by_figi[figi] = instrument
        return list(by_figi.values())

    async def get_stored_shares(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> TBankStoredSharesResponse:
        return await self.get_stored_instruments(
            limit=limit,
            offset=offset,
            instrument_types=["share"],
        )

    async def get_stored_instruments(
        self,
        limit: int = 100,
        offset: int = 0,
        instrument_types: list[str] | None = None,
    ) -> TBankStoredSharesResponse:
        if self.db is None:
            raise RuntimeError("DB manager is not configured for read operation")

        normalized_types = self._normalize_instrument_types(instrument_types)
        items = await self.db.tbank_share.list_active(
            limit=limit,
            offset=offset,
            instrument_types=normalized_types,
        )
        total = await self.db.tbank_share.count_active(instrument_types=normalized_types)
        figies = [item.figi for item in items if item.figi]
        prices_by_figi, captured_at_by_figi = await self._get_last_prices_for_figies(figies)
        details_by_figi = await self._get_instrument_details_for_figies(figies)
        trading_status = await self._get_cached_trading_status()

        response_items: list[TBankStoredShareItem] = []
        for item in items:
            details = details_by_figi.get(item.figi, {})
            last_price = prices_by_figi.get(item.figi)
            captured_at_msk = captured_at_by_figi.get(item.figi)
            exchange_display = self._to_exchange_display(item.real_exchange, item.exchange)
            response_items.append(
                TBankStoredShareItem.model_validate(
                    {
                        "id": item.id,
                        "figi": item.figi,
                        "instrument_type": item.instrument_type,
                        "ticker": item.ticker,
                        "class_code": item.class_code,
                        "isin": item.isin,
                        "instrument_name": item.instrument_name,
                        "currency": item.currency,
                        "exchange": item.exchange,
                        "country_of_risk": item.country_of_risk,
                        "buy_available": item.buy_available,
                        "sell_available": item.sell_available,
                        "api_trade_available": item.api_trade_available,
                        "short_enabled": item.short_enabled,
                        "real_exchange": item.real_exchange,
                        "exchange_display": exchange_display,
                        "lot": details.get("lot"),
                        "sector": details.get("sector"),
                        "nominal": details.get("nominal"),
                        "is_active": item.is_active,
                        "last_synced_at": item.last_synced_at,
                        "last_price": str(last_price) if isinstance(last_price, Decimal) else None,
                        "last_price_captured_at_msk": captured_at_msk,
                        "trading_open": trading_status.trading_open if trading_status else None,
                        "session_opened_at_msk": trading_status.session_opened_at_msk if trading_status else None,
                        "session_closed_at_msk": trading_status.session_closed_at_msk if trading_status else None,
                        "next_session_opened_at_msk": trading_status.next_session_opened_at_msk if trading_status else None,
                        "next_session_closed_at_msk": trading_status.next_session_closed_at_msk if trading_status else None,
                        "day_open_price": None,
                        "day_close_price": None,
                        "day_change_percent": None,
                        "year_change_percent": None,
                        "trading_status": None,
                        "limit_order_available": None,
                        "market_order_available": None,
                    }
                )
            )

        return TBankStoredSharesResponse(
            total=total,
            limit=limit,
            offset=offset,
            items=response_items,
        )

    async def get_share_details_online(self, figi: str) -> TBankStoredShareItem | None:
        return await self.get_instrument_details_online(figi=figi, instrument_type="share")

    async def get_instrument_details_online(
        self,
        figi: str,
        instrument_type: str | None = None,
    ) -> TBankStoredShareItem | None:
        figi_value = figi.strip()
        if not figi_value:
            return None

        resolved_type = instrument_type.strip().lower() if instrument_type else None
        db_id = 0
        if self.db is not None:
            db_item = await self.db.tbank_share.get_active_by_figi(figi_value)
            if db_item is not None:
                db_id = int(db_item.id)
                if resolved_type is None:
                    resolved_type = str(db_item.instrument_type or "share").strip().lower()

        instrument = await self.connector.get_instrument_by_figi(
            figi=figi_value,
            instrument_type=resolved_type,
        )
        instrument_figi = str(instrument.get("figi", "")).strip() or figi_value
        exchange_value = self._to_str_or_none(instrument.get("exchange"))
        real_exchange_value = self._to_str_or_none(instrument.get("realExchange"))
        resolved_type = self._resolve_instrument_type(instrument, fallback=resolved_type)

        price_value: Decimal | None = None
        price_captured_at_msk: str | None = None
        try:
            points = await self.connector.get_last_price_points([instrument_figi])
            point = points.get(instrument_figi)
            if point is not None:
                price_value = point.price
                if point.captured_at_msk is not None:
                    price_captured_at_msk = point.captured_at_msk.isoformat()
        except TBankInvestRequestError:
            point = None

        trading_status = await self._resolve_trading_status_for_instrument(
            exchange=exchange_value,
            real_exchange=real_exchange_value,
        )
        instrument_trading_status: TBankInstrumentTradingStatus | None = None
        try:
            instrument_trading_status = await self.connector.get_trading_status(instrument_figi)
        except TBankInvestRequestError:
            instrument_trading_status = None

        metrics = {
            "day_open_price": None,
            "day_close_price": None,
            "day_change_percent": None,
            "year_change_percent": None,
        }
        try:
            metrics = await self.connector.get_daily_metrics(figi=instrument_figi, current_price=price_value)
        except TBankInvestRequestError:
            metrics = {
                "day_open_price": None,
                "day_close_price": None,
                "day_change_percent": None,
                "year_change_percent": None,
            }

        exchange_display = self._to_exchange_display(real_exchange_value, exchange_value)

        return TBankStoredShareItem.model_validate(
            {
                "id": db_id,
                "figi": instrument_figi,
                "instrument_type": resolved_type,
                "ticker": self._to_str_or_none(instrument.get("ticker")),
                "class_code": self._to_str_or_none(instrument.get("classCode")),
                "isin": self._to_str_or_none(instrument.get("isin")),
                "instrument_name": self._to_str_or_none(instrument.get("name")),
                "currency": self._to_str_or_none(instrument.get("currency")),
                "exchange": exchange_value,
                "country_of_risk": self._to_str_or_none(instrument.get("countryOfRisk")),
                "buy_available": instrument.get("buyAvailableFlag"),
                "sell_available": instrument.get("sellAvailableFlag"),
                "api_trade_available": (
                    instrument_trading_status.api_trade_available
                    if instrument_trading_status is not None
                    else instrument.get("apiTradeAvailableFlag")
                ),
                "short_enabled": instrument.get("shortEnabledFlag"),
                "real_exchange": real_exchange_value,
                "exchange_display": exchange_display,
                "lot": self._to_int_or_none(instrument.get("lot")),
                "sector": self._to_str_or_none(instrument.get("sector")),
                "nominal": self._format_nominal(instrument.get("nominal")),
                "is_active": True,
                "last_synced_at": datetime.now(ZoneInfo("Europe/Moscow")).replace(tzinfo=None).isoformat(),
                "last_price": str(price_value) if isinstance(price_value, Decimal) else None,
                "last_price_captured_at_msk": price_captured_at_msk,
                "trading_open": trading_status.trading_open if trading_status else None,
                "session_opened_at_msk": trading_status.session_opened_at_msk if trading_status else None,
                "session_closed_at_msk": trading_status.session_closed_at_msk if trading_status else None,
                "next_session_opened_at_msk": trading_status.next_session_opened_at_msk if trading_status else None,
                "next_session_closed_at_msk": trading_status.next_session_closed_at_msk if trading_status else None,
                "day_open_price": self._decimal_to_str(metrics.get("day_open_price")),
                "day_close_price": self._decimal_to_str(metrics.get("day_close_price")),
                "day_change_percent": self._decimal_to_str(metrics.get("day_change_percent")),
                "year_change_percent": self._decimal_to_str(metrics.get("year_change_percent")),
                "trading_status": (
                    instrument_trading_status.trading_status
                    if instrument_trading_status is not None
                    else None
                ),
                "limit_order_available": (
                    instrument_trading_status.limit_order_available
                    if instrument_trading_status is not None
                    else None
                ),
                "market_order_available": (
                    instrument_trading_status.market_order_available
                    if instrument_trading_status is not None
                    else None
                ),
            }
        )

    @staticmethod
    def _to_storage_row(instrument: dict) -> dict:
        return {
            "figi": str(instrument.get("figi", "")).strip(),
            "instrument_type": TBankSharesService._resolve_instrument_type(instrument),
            "ticker": instrument.get("ticker"),
            "class_code": instrument.get("classCode"),
            "isin": instrument.get("isin"),
            "instrument_name": instrument.get("name"),
            "currency": instrument.get("currency"),
            "exchange": instrument.get("exchange"),
            "country_of_risk": instrument.get("countryOfRisk"),
            "buy_available": instrument.get("buyAvailableFlag"),
            "sell_available": instrument.get("sellAvailableFlag"),
            "api_trade_available": instrument.get("apiTradeAvailableFlag"),
            "short_enabled": instrument.get("shortEnabledFlag"),
            "real_exchange": instrument.get("realExchange"),
        }

    @staticmethod
    def _is_russian_share(instrument: dict) -> bool:
        return TBankSharesService._is_russian_instrument(instrument)

    @staticmethod
    def _is_russian_instrument(instrument: dict) -> bool:
        country_of_risk = str(instrument.get("countryOfRisk", "")).strip().upper()
        return country_of_risk == "RU"

    @staticmethod
    def _normalize_instrument_types(instrument_types: list[str] | None = None) -> list[str]:
        allowed = {"share", "bond", "etf"}
        normalized: list[str] = []
        for raw_value in instrument_types or ["share", "bond", "etf"]:
            value = str(raw_value).strip().lower()
            if value in allowed and value not in normalized:
                normalized.append(value)
        return normalized or ["share", "bond", "etf"]

    @staticmethod
    def _resolve_instrument_type(instrument: dict, fallback: str | None = None) -> str:
        candidates = [
            instrument.get("instrumentType"),
            instrument.get("instrument_type"),
            fallback,
        ]
        for raw_value in candidates:
            value = str(raw_value or "").strip().lower()
            if value in {"share", "bond", "etf"}:
                return value
            if value in {"instrument_type_share", "share_type"}:
                return "share"
            if value in {"instrument_type_bond", "bond_type"}:
                return "bond"
            if value in {"instrument_type_etf", "fund", "etf_type"}:
                return "etf"
        return "share"

    @staticmethod
    def _to_exchange_display(real_exchange: str | None, exchange: str | None) -> str | None:
        if real_exchange:
            normalized = str(real_exchange).replace("REAL_EXCHANGE_", "").strip()
            if normalized:
                return normalized
        if exchange:
            return str(exchange).upper()
        return None

    async def _cache_instrument_details(self, instruments: list[dict]) -> None:
        for instrument in instruments:
            figi = str(instrument.get("figi", "")).strip()
            if not figi:
                continue
            payload = {
                "lot": self._to_int_or_none(instrument.get("lot")),
                "sector": self._to_str_or_none(instrument.get("sector")),
                "nominal": self._format_nominal(instrument.get("nominal")),
            }
            await self._cache_json(
                key=f"tbank:share:details:{figi}",
                payload=payload,
                ttl_seconds=self._DETAILS_CACHE_TTL_SECONDS,
            )

    async def _cache_last_prices(self, prices: dict[str, Decimal]) -> None:
        captured_at_msk = datetime.now(ZoneInfo("Europe/Moscow")).replace(tzinfo=None).isoformat()
        for figi, price in prices.items():
            await self._cache_json(
                key=f"tbank:share:price:{figi}",
                payload={
                    "price": str(price),
                    "captured_at_msk": captured_at_msk,
                },
                ttl_seconds=self._PRICE_CACHE_TTL_SECONDS,
            )

    async def _cache_trading_status(self, status: Any) -> None:
        await self._cache_json(
            key="tbank:exchange:trading_status:MOEX",
            payload={
                "trading_open": bool(status.trading_open),
                "session_opened_at_msk": status.session_opened_at_msk.isoformat() if status.session_opened_at_msk else None,
                "session_closed_at_msk": status.session_closed_at_msk.isoformat() if status.session_closed_at_msk else None,
                "next_session_opened_at_msk": status.next_session_opened_at_msk.isoformat() if status.next_session_opened_at_msk else None,
                "next_session_closed_at_msk": status.next_session_closed_at_msk.isoformat() if status.next_session_closed_at_msk else None,
            },
            ttl_seconds=self._TRADING_STATUS_CACHE_TTL_SECONDS,
        )

    async def _get_last_prices_for_figies(self, figies: list[str]) -> tuple[dict[str, Decimal], dict[str, str]]:
        prices: dict[str, Decimal] = {}
        captured_at: dict[str, str] = {}
        missed: list[str] = []

        for figi in figies:
            cached = await self._cache_json_get(f"tbank:share:price:{figi}")
            if not isinstance(cached, dict):
                missed.append(figi)
                continue
            raw_price = cached.get("price")
            try:
                price_dec = Decimal(str(raw_price))
            except Exception:
                missed.append(figi)
                continue
            prices[figi] = price_dec
            captured_text = self._to_str_or_none(cached.get("captured_at_msk"))
            if captured_text:
                captured_at[figi] = captured_text

        if missed:
            fresh_prices = await self.connector.get_last_prices(missed)
            await self._cache_last_prices(fresh_prices)
            prices.update(fresh_prices)
            fallback_captured_at = datetime.now(ZoneInfo("Europe/Moscow")).replace(tzinfo=None).isoformat()
            for figi in fresh_prices:
                captured_at[figi] = fallback_captured_at

        return prices, captured_at

    async def _get_instrument_details_for_figies(self, figies: list[str]) -> dict[str, dict[str, Any]]:
        details: dict[str, dict[str, Any]] = {}
        missing: list[str] = []
        for figi in figies:
            cached = await self._cache_json_get(f"tbank:share:details:{figi}")
            if isinstance(cached, dict):
                details[figi] = cached
            else:
                missing.append(figi)

        if not missing:
            return details

        try:
            instruments = await self._load_instruments(
                instrument_types=["share", "bond", "etf"],
                instrument_status="INSTRUMENT_STATUS_BASE",
                instrument_exchange="INSTRUMENT_EXCHANGE_UNSPECIFIED",
                include_dealer=True,
            )
        except TBankInvestRequestError:
            return details

        by_figi: dict[str, dict] = {}
        for instrument in instruments:
            figi = str(instrument.get("figi", "")).strip()
            if figi:
                by_figi[figi] = instrument

        missed_instruments = [by_figi[figi] for figi in missing if figi in by_figi]
        await self._cache_instrument_details(missed_instruments)

        for figi in missing:
            instrument = by_figi.get(figi)
            if not instrument:
                continue
            details[figi] = {
                "lot": self._to_int_or_none(instrument.get("lot")),
                "sector": self._to_str_or_none(instrument.get("sector")),
                "nominal": self._format_nominal(instrument.get("nominal")),
            }
        return details

    async def _get_cached_trading_status(self) -> Any | None:
        cached = await self._cache_json_get("tbank:exchange:trading_status:MOEX")
        if isinstance(cached, dict):
            return _CachedTradingStatus.from_payload(cached)
        try:
            fresh = await self.connector.get_exchange_trading_status(exchange="MOEX")
        except TBankInvestRequestError:
            return None
        await self._cache_trading_status(fresh)
        return fresh

    async def _resolve_trading_status_for_instrument(
        self,
        *,
        exchange: str | None,
        real_exchange: str | None,
    ) -> Any | None:
        candidates: list[str] = []
        for raw in (exchange, real_exchange):
            if not raw:
                continue
            value = raw.strip().upper()
            if not value:
                continue
            candidates.append(value)
            if value.startswith("INSTRUMENT_EXCHANGE_"):
                candidates.append(value.replace("INSTRUMENT_EXCHANGE_", "", 1))
            if value.startswith("REAL_EXCHANGE_"):
                candidates.append(value.replace("REAL_EXCHANGE_", "", 1))
        candidates.extend(["MOEX", "MOEX_PLUS"])

        seen: set[str] = set()
        normalized_candidates: list[str] = []
        for candidate in candidates:
            if not candidate:
                continue
            if candidate in seen:
                continue
            seen.add(candidate)
            normalized_candidates.append(candidate)

        fallback_status = None
        for candidate in normalized_candidates:
            try:
                status = await self.connector.get_exchange_trading_status(exchange=candidate)
            except TBankInvestRequestError:
                continue

            if (
                status.trading_open
                or status.session_opened_at_msk is not None
                or status.session_closed_at_msk is not None
                or status.next_session_opened_at_msk is not None
                or status.next_session_closed_at_msk is not None
            ):
                return status
            if fallback_status is None:
                fallback_status = status

        return fallback_status

    async def _cache_json(self, key: str, payload: dict[str, Any], ttl_seconds: int) -> None:
        if self.cache is None:
            return
        try:
            await self.cache.set(
                key=key,
                value=json.dumps(payload, ensure_ascii=False),
                expire=ttl_seconds,
            )
        except Exception:
            return

    async def _cache_json_get(self, key: str) -> dict[str, Any] | None:
        if self.cache is None:
            return None
        try:
            raw = await self.cache.get(key)
        except Exception:
            return None
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        if not isinstance(raw, str):
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    @staticmethod
    def _format_nominal(raw_nominal: object) -> str | None:
        if not isinstance(raw_nominal, dict):
            return None
        units_raw = raw_nominal.get("units")
        nano_raw = raw_nominal.get("nano", 0)
        currency = str(raw_nominal.get("currency", "")).strip().upper()
        try:
            units = int(units_raw)
            nano = int(nano_raw)
        except (TypeError, ValueError):
            return None
        amount = Decimal(units) + (Decimal(nano) / Decimal(1_000_000_000))
        normalized = format(amount.normalize(), "f")
        if "." in normalized:
            normalized = normalized.rstrip("0").rstrip(".")
        if currency:
            return f"{normalized} {currency}"
        return normalized

    @staticmethod
    def _to_int_or_none(raw_value: object) -> int | None:
        try:
            if raw_value is None:
                return None
            return int(raw_value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_str_or_none(raw_value: object) -> str | None:
        if raw_value is None:
            return None
        value = str(raw_value).strip()
        return value or None

    @staticmethod
    def _decimal_to_str(raw_value: object) -> str | None:
        if not isinstance(raw_value, Decimal):
            return None
        return format(raw_value, "f")


class _CachedTradingStatus:
    def __init__(
        self,
        *,
        trading_open: bool,
        session_opened_at_msk: datetime | None,
        session_closed_at_msk: datetime | None,
        next_session_opened_at_msk: datetime | None,
        next_session_closed_at_msk: datetime | None,
    ) -> None:
        self.trading_open = trading_open
        self.session_opened_at_msk = session_opened_at_msk
        self.session_closed_at_msk = session_closed_at_msk
        self.next_session_opened_at_msk = next_session_opened_at_msk
        self.next_session_closed_at_msk = next_session_closed_at_msk

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "_CachedTradingStatus":
        return cls(
            trading_open=bool(payload.get("trading_open")),
            session_opened_at_msk=cls._parse_dt(payload.get("session_opened_at_msk")),
            session_closed_at_msk=cls._parse_dt(payload.get("session_closed_at_msk")),
            next_session_opened_at_msk=cls._parse_dt(payload.get("next_session_opened_at_msk")),
            next_session_closed_at_msk=cls._parse_dt(payload.get("next_session_closed_at_msk")),
        )

    @staticmethod
    def _parse_dt(raw_value: object) -> datetime | None:
        if not isinstance(raw_value, str):
            return None
        value = raw_value.strip()
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None


ALLOWED_SECTORS: tuple[str, ...] = (
    "информационные технологии",
    "сырьевая промышленность",
    "машиностроение и транспорт",
    "другое",
    "здравоохранение",
    "зелёная энергетика",
    "материалы для экотехнологий",
    "недвижимость",
    "потребительские товары и услуги",
    "телекоммуникации",
    "финансовый сектор",
    "электротранспорт и комплектующие",
    "электроэнергетика",
    "энергетика",
    "энергоэффективные здания",
)


class TBankTradingService:
    def __init__(self, connector: TBankInvestConnector, account_id: str) -> None:
        self.connector = connector
        self.account_id = account_id.strip()

    async def create_order(
        self,
        *,
        figi: str,
        quantity_lots: int,
        direction: str,
        order_type: str,
        price: str | None,
        confirm_margin_trade: bool | None = None,
    ) -> dict:
        if not self.account_id:
            raise TBankInvestRequestError("User account_id is not configured")

        decimal_price: Decimal | None = None
        if price is not None and price.strip():
            decimal_price = Decimal(price.strip())

        if order_type == "ORDER_TYPE_LIMIT" and decimal_price is None:
            raise TBankInvestRequestError("price is required for ORDER_TYPE_LIMIT")

        return await self.connector.post_order(
            account_id=self.account_id,
            instrument_id=figi,
            quantity_lots=quantity_lots,
            direction=direction,
            order_type=order_type,
            price=decimal_price,
            confirm_margin_trade=confirm_margin_trade,
        )

    async def cancel_order(self, order_id: str) -> dict:
        if not self.account_id:
            raise TBankInvestRequestError("User account_id is not configured")
        return await self.connector.cancel_order(account_id=self.account_id, order_id=order_id)

    async def get_orders(self) -> dict:
        if not self.account_id:
            raise TBankInvestRequestError("User account_id is not configured")
        return await self.connector.get_orders(account_id=self.account_id)

    async def create_stop_order(
        self,
        *,
        figi: str,
        quantity_lots: int,
        direction: str,
        stop_order_type: str,
        stop_price: str,
        price: str | None = None,
        expiration_type: str = "STOP_ORDER_EXPIRATION_TYPE_GOOD_TILL_CANCEL",
        confirm_margin_trade: bool | None = None,
    ) -> dict:
        if not self.account_id:
            raise TBankInvestRequestError("User account_id is not configured")

        stop_price_value = Decimal(stop_price.strip())
        limit_price_value = Decimal(price.strip()) if price and price.strip() else None
        if stop_order_type == "STOP_ORDER_TYPE_STOP_LOSS" and limit_price_value is not None:
            raise TBankInvestRequestError("price is not supported for STOP_ORDER_TYPE_STOP_LOSS; use STOP_ORDER_TYPE_STOP_LIMIT")
        normalized_direction = self._to_stop_order_direction(direction)
        exchange_order_type = self._resolve_stop_exchange_order_type(
            stop_order_type=stop_order_type,
            price=limit_price_value,
        )

        return await self.connector.post_stop_order(
            account_id=self.account_id,
            instrument_id=figi,
            quantity_lots=quantity_lots,
            direction=normalized_direction,
            stop_order_type=stop_order_type,
            stop_price=stop_price_value,
            price=limit_price_value,
            expiration_type=expiration_type,
            exchange_order_type=exchange_order_type,
            confirm_margin_trade=confirm_margin_trade,
        )

    async def cancel_stop_order(self, stop_order_id: str) -> dict:
        if not self.account_id:
            raise TBankInvestRequestError("User account_id is not configured")
        return await self.connector.cancel_stop_order(account_id=self.account_id, stop_order_id=stop_order_id)

    async def get_stop_orders(self) -> dict:
        if not self.account_id:
            raise TBankInvestRequestError("User account_id is not configured")
        return await self.connector.get_stop_orders(account_id=self.account_id)

    async def get_portfolio(self) -> dict:
        if not self.account_id:
            raise TBankInvestRequestError("User account_id is not configured")
        return await self.connector.get_portfolio(account_id=self.account_id)

    async def get_operations(self, days: int = 30) -> dict:
        if not self.account_id:
            raise TBankInvestRequestError("User account_id is not configured")
        return await self.connector.get_operations(account_id=self.account_id, days=days)

    @staticmethod
    def _to_stop_order_direction(direction: str) -> str:
        mapping = {
            "ORDER_DIRECTION_BUY": "STOP_ORDER_DIRECTION_BUY",
            "ORDER_DIRECTION_SELL": "STOP_ORDER_DIRECTION_SELL",
            "STOP_ORDER_DIRECTION_BUY": "STOP_ORDER_DIRECTION_BUY",
            "STOP_ORDER_DIRECTION_SELL": "STOP_ORDER_DIRECTION_SELL",
        }
        normalized = mapping.get(direction.strip())
        if normalized is None:
            raise TBankInvestRequestError("Unsupported stop order direction")
        return normalized

    @staticmethod
    def _resolve_stop_exchange_order_type(
        *,
        stop_order_type: str,
        price: Decimal | None,
    ) -> str:
        if stop_order_type not in {
            "STOP_ORDER_TYPE_STOP_LOSS",
            "STOP_ORDER_TYPE_STOP_LIMIT",
            "STOP_ORDER_TYPE_TAKE_PROFIT",
        }:
            raise TBankInvestRequestError("Unsupported stop order type")
        if stop_order_type == "STOP_ORDER_TYPE_STOP_LIMIT":
            if price is None:
                raise TBankInvestRequestError("price is required for STOP_ORDER_TYPE_STOP_LIMIT")
            return "EXCHANGE_ORDER_TYPE_LIMIT"
        if stop_order_type == "STOP_ORDER_TYPE_TAKE_PROFIT":
            return "EXCHANGE_ORDER_TYPE_LIMIT" if price is not None else "EXCHANGE_ORDER_TYPE_MARKET"
        return "EXCHANGE_ORDER_TYPE_MARKET"


class TBankPortfolioAnalysisService:
    _HORIZONS: dict[str, str] = {
        "1d": "1 день",
        "7d": "7 дней",
        "1m": "1 месяц",
        "1y": "1 год",
    }
    _HORIZON_DAYS: dict[str, int] = {
        "1d": 1,
        "7d": 7,
        "1m": 30,
        "1y": 365,
    }

    def __init__(
        self,
        *,
        trading_service: TBankTradingService,
        ai_connector: AIGatewayConnector,
        model: str,
        web_plugins: list[dict[str, Any]] | None = None,
        web_search_options: dict[str, Any] | None = None,
    ) -> None:
        self.trading_service = trading_service
        self.ai_connector = ai_connector
        self.model = model
        self.web_plugins = web_plugins or []
        self.web_search_options = web_search_options

    async def analyze(self, horizon: str) -> TBankPortfolioAnalysisResponse:
        normalized_horizon = self._normalize_horizon(horizon)
        horizon_label = self._HORIZONS[normalized_horizon]
        generated_at = datetime.now(ZoneInfo("Europe/Moscow")).replace(microsecond=0)
        raw_portfolio = await self.trading_service.get_portfolio()
        normalized_portfolio = self._normalize_portfolio(raw_portfolio)
        window_end = generated_at + timedelta(days=self._HORIZON_DAYS[normalized_horizon])
        payload = {
            "generated_at_msk": generated_at.isoformat(),
            "horizon": normalized_horizon,
            "horizon_label": horizon_label,
            "analysis_window_msk": {
                "from": generated_at.isoformat(),
                "to": window_end.isoformat(),
                "rule": "Events outside this window may be mentioned only in risks/triggers, not in the verdict.",
            },
            "portfolio": normalized_portfolio,
        }

        report = await self.ai_connector.create_chat_completion(
            messages=[
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": self._user_prompt(payload)},
            ],
            reasoning={"effort": "high"},
            plugins=self.web_plugins,
            web_search_options=self.web_search_options,
        )
        return TBankPortfolioAnalysisResponse(
            horizon=normalized_horizon,
            horizon_label=horizon_label,
            generated_at_msk=generated_at.isoformat(),
            model=self.model,
            report=report,
            portfolio=normalized_portfolio,
        )

    @classmethod
    def _normalize_horizon(cls, horizon: str) -> str:
        value = str(horizon or "").strip().lower()
        aliases = {
            "day": "1d",
            "1day": "1d",
            "1_day": "1d",
            "1": "1d",
            "week": "7d",
            "7days": "7d",
            "7_days": "7d",
            "7": "7d",
            "month": "1m",
            "1month": "1m",
            "1_month": "1m",
            "30d": "1m",
            "year": "1y",
            "1year": "1y",
            "1_year": "1y",
            "365d": "1y",
        }
        normalized = aliases.get(value, value)
        if normalized not in cls._HORIZONS:
            raise TBankInvestRequestError("Unsupported analysis horizon")
        return normalized

    @classmethod
    def _system_prompt(cls) -> str:
        return (
            "Ты инвестиционный AI-аналитик портфеля. Работай как агент: сначала изучи портфель из JSON, "
            "особенно weight_percent, portfolio_rank, market_value, expected_yield и daily_yield. "
            "затем сам собери актуальное информационное поле через доступные web tools по каждому существенному активу, "
            "индексу Мосбиржи, рублю, ставке ЦБ, сырью и отраслевым факторам, если они важны для позиций. "
            "Перед финальным ответом сделай внутреннюю проверку релевантности: факт должен быть связан с весом позиции "
            "и попадать в analysis_window_msk. События вне горизонта запрещено выносить в ИТОГ или ФАКТОРЫ; "
            "их можно упомянуть только в РИСКИ И ТРИГГЕРЫ и только одной короткой строкой. "
            "Для горизонта 1 день делай упор на свежие новости, рынок, технику и ближайшие события. "
            "Для 7 дней — на новости, календарь событий, дивиденды, отчеты, ставку и отраслевые драйверы. "
            "Для 1 месяца — на тренды, макро, ожидания по ставке, отчетности и корпоративные события. "
            "Для 1 года — на фундаментальные факторы, отрасли, ставку, дивидендную политику, санкции, валюту и циклы. "
            "Не пиши воду. Каждое утверждение должно отвечать на вопрос: что это значит для портфеля, "
            "какая позиция/класс активов затронуты и почему это важно именно на выбранный горизонт. "
            "Запрещены общие фразы без факта: 'рынок волатилен', 'может вырасти/упасть', 'проверьте новости'. "
            "Запрещены расплывчатые связки: 'начинает влиять', 'участники учитывают', 'риск-аппетит' без конкретного "
            "источника, даты, инструмента и механизма влияния. "
            "Если используешь внешний факт, укажи источник коротко в скобках. Не выдумывай цены, новости и события. "
            "Не давай прямых команд купить/продать и не обещай доходность. Пиши по-русски, четко и по делу. "
            "Структура ответа строго такая: "
            "ИТОГ: направление портфеля на горизонт — позитивно/нейтрально/негативно; 2-3 причины по весам. "
            "ФАКТОРЫ: 3-5 пунктов, только факты внутри горизонта или уже действующий фон; у каждого пункта укажи влияние. "
            "ПО ПОЗИЦИЯМ: только позиции с существенным весом, в порядке portfolio_rank; формат 'тикер, вес — вывод'. "
            "СЦЕНАРИЙ: базовый/позитивный/негативный на выбранный горизонт, без фантазий и без точных обещаний доходности. "
            "РИСКИ И ТРИГГЕРЫ: конкретные уровни/даты/публикации/события, которые надо отслеживать дальше. "
            "В конце одна строка: 'Не является индивидуальной инвестиционной рекомендацией.' "
            "Если после web search нет релевантных фактов по горизонту, прямо напиши 'релевантных свежих факторов не найдено' "
            "и не заменяй это общими словами. Объем — до 3800 знаков."
        )

    @staticmethod
    def _user_prompt(payload: dict[str, Any]) -> str:
        return (
            "Сформируй короткий агентный анализ портфеля на указанный горизонт.\n"
            "Дата и время расчета строго по Москве.\n"
            "Перед выводом используй web search, чтобы изучить актуальный фон вокруг позиций и рынка. "
            "Ищи по тикерам, названиям эмитентов, индексу IMOEX, ключевой ставке ЦБ РФ, рублю и важным сырьевым факторам. "
            "Для малых позиций не трать много поиска; фокус на концентрациях и главных драйверах риска. "
            "Сначала проверь, попадает ли найденное событие в analysis_window_msk; если нет — не делай его главным выводом.\n\n"
            f"Данные портфеля:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )

    @classmethod
    def _normalize_portfolio(cls, raw_portfolio: dict[str, Any]) -> dict[str, Any]:
        positions = raw_portfolio.get("positions")
        safe_positions = positions if isinstance(positions, list) else []
        totals = {
            "shares": cls._money_payload(raw_portfolio.get("totalAmountShares")),
            "bonds": cls._money_payload(raw_portfolio.get("totalAmountBonds")),
            "etf": cls._money_payload(raw_portfolio.get("totalAmountEtf")),
            "currencies": cls._money_payload(raw_portfolio.get("totalAmountCurrencies")),
            "futures": cls._money_payload(raw_portfolio.get("totalAmountFutures")),
            "options": cls._money_payload(raw_portfolio.get("totalAmountOptions")),
            "portfolio": cls._money_payload(raw_portfolio.get("totalAmountPortfolio")),
            "expected_yield": cls._money_payload(raw_portfolio.get("expectedYield")),
            "daily_yield": cls._money_payload(raw_portfolio.get("dailyYield")),
        }
        positions_payload = [
            cls._normalize_position(position)
            for position in safe_positions
            if isinstance(position, dict)
        ]
        positions_payload = cls._enrich_position_weights(positions_payload, totals)
        return {
            "totals": totals,
            "positions_count": len(positions_payload),
            "positions": positions_payload,
        }

    @classmethod
    def _normalize_position(cls, position: dict[str, Any]) -> dict[str, Any]:
        return {
            "figi": cls._clean_str(position.get("figi")),
            "instrument_uid": cls._clean_str(position.get("instrumentUid")),
            "ticker": cls._clean_str(position.get("ticker")),
            "instrument_type": cls._clean_str(position.get("instrumentType")),
            "quantity": cls._quantity_payload(position.get("quantity")),
            "quantity_lots": cls._quantity_payload(position.get("quantityLots")),
            "blocked": bool(position.get("blocked")),
            "blocked_lots": cls._quantity_payload(position.get("blockedLots")),
            "average_price": cls._money_payload(position.get("averagePositionPrice")),
            "average_price_fifo": cls._money_payload(position.get("averagePositionPriceFifo")),
            "current_price": cls._money_payload(position.get("currentPrice")),
            "expected_yield": cls._money_payload(position.get("expectedYield")),
            "var_margin": cls._money_payload(position.get("varMargin")),
        }

    @classmethod
    def _enrich_position_weights(
        cls,
        positions: list[dict[str, Any]],
        totals: dict[str, dict[str, str | None] | None],
    ) -> list[dict[str, Any]]:
        portfolio_total = cls._money_value_to_decimal(totals.get("portfolio"))
        value_rows: list[tuple[dict[str, Any], Decimal | None]] = []
        for position in positions:
            market_value = cls._position_market_value(position)
            if market_value is not None:
                currency = None
                current_price = position.get("current_price")
                if isinstance(current_price, dict):
                    currency = current_price.get("currency")
                position["market_value"] = {
                    "value": cls._decimal_to_text(market_value),
                    "currency": currency,
                }
            value_rows.append((position, market_value))

        if portfolio_total is None or portfolio_total <= 0:
            calculated_total = sum(
                value
                for _, value in value_rows
                if value is not None and value > 0
            )
            portfolio_total = calculated_total if calculated_total > 0 else None

        sorted_rows = sorted(
            value_rows,
            key=lambda row: abs(row[1] or Decimal("0")),
            reverse=True,
        )
        for rank, (position, market_value) in enumerate(sorted_rows, start=1):
            position["portfolio_rank"] = rank
            if portfolio_total is not None and portfolio_total > 0 and market_value is not None:
                weight = (market_value / portfolio_total) * Decimal("100")
                position["weight_percent"] = cls._decimal_to_text(
                    weight.quantize(Decimal("0.01"))
                )
        return [position for position, _ in sorted_rows]

    @classmethod
    def _position_market_value(cls, position: dict[str, Any]) -> Decimal | None:
        quantity = cls._text_to_decimal(position.get("quantity"))
        current_price = cls._money_value_to_decimal(position.get("current_price"))
        if quantity is None or current_price is None:
            return None
        return quantity * current_price

    @classmethod
    def _money_value_to_decimal(cls, raw_value: object) -> Decimal | None:
        if not isinstance(raw_value, dict):
            return None
        return cls._text_to_decimal(raw_value.get("value"))

    @staticmethod
    def _text_to_decimal(raw_value: object) -> Decimal | None:
        if raw_value is None:
            return None
        try:
            return Decimal(str(raw_value))
        except Exception:
            return None

    @staticmethod
    def _money_payload(raw_value: object) -> dict[str, str | None] | None:
        if not isinstance(raw_value, dict):
            return None
        value = TBankPortfolioAnalysisService._quotation_to_decimal(raw_value)
        currency = str(raw_value.get("currency") or "").strip().upper() or None
        return {
            "value": TBankPortfolioAnalysisService._decimal_to_text(value),
            "currency": currency,
        }

    @staticmethod
    def _quantity_payload(raw_value: object) -> str | None:
        if isinstance(raw_value, dict):
            return TBankPortfolioAnalysisService._decimal_to_text(
                TBankPortfolioAnalysisService._quotation_to_decimal(raw_value)
            )
        if raw_value is None:
            return None
        try:
            return TBankPortfolioAnalysisService._decimal_to_text(Decimal(str(raw_value)))
        except Exception:
            return str(raw_value)

    @staticmethod
    def _quotation_to_decimal(raw_value: dict[str, Any]) -> Decimal | None:
        try:
            units = int(raw_value.get("units", 0))
            nano = int(raw_value.get("nano", 0))
        except Exception:
            return None
        return Decimal(units) + (Decimal(nano) / Decimal(1_000_000_000))

    @staticmethod
    def _decimal_to_text(value: Decimal | None) -> str | None:
        if value is None:
            return None
        text = format(value.normalize(), "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text

    @staticmethod
    def _clean_str(raw_value: object) -> str | None:
        if raw_value is None:
            return None
        value = str(raw_value).strip()
        return value or None


class TBankMonitorService:
    def __init__(self, db: DBManager, connector: TBankInvestConnector | None = None) -> None:
        self.db = db
        self.connector = connector

    async def create_monitor(self, payload: TBankMonitorCreateRequest) -> TBankMonitorItem:
        row = await self.db.tbank_price_monitor.create_monitor(
            telegram_user_id=payload.telegram_user_id,
            figi=payload.figi.strip(),
            ticker=payload.ticker,
            instrument_name=payload.instrument_name,
            interval_seconds=payload.interval_seconds,
            threshold_percent=Decimal(payload.threshold_percent),
            threshold_rub=Decimal(payload.threshold_rub),
            base_price=Decimal(payload.base_price),
        )
        return self._to_item(row)

    async def list_monitors(self, telegram_user_id: int) -> TBankMonitorListResponse:
        rows = await self.db.tbank_price_monitor.list_by_user(telegram_user_id)
        return TBankMonitorListResponse(total=len(rows), items=[self._to_item(row) for row in rows])

    async def toggle_monitor(self, telegram_user_id: int, monitor_id: int, is_active: bool) -> bool:
        return await self.db.tbank_price_monitor.set_active(monitor_id, telegram_user_id, is_active)

    async def delete_monitor(self, telegram_user_id: int, monitor_id: int) -> bool:
        return await self.db.tbank_price_monitor.delete_by_id_and_user(monitor_id, telegram_user_id)

    async def rebase_monitor(self, telegram_user_id: int, monitor_id: int) -> Decimal | None:
        row = await self.db.tbank_price_monitor.get_by_id_and_user(monitor_id, telegram_user_id)
        if row is None:
            return None

        connector = self._require_connector()
        figi = row.figi.strip()
        if not figi:
            return None

        try:
            prices = await connector.get_last_prices([figi])
        except TBankInvestRequestError:
            return None
        current_price = prices.get(figi)
        if not isinstance(current_price, Decimal):
            return None

        ok = await self.db.tbank_price_monitor.set_base_price(
            monitor_id=monitor_id,
            telegram_user_id=telegram_user_id,
            base_price=current_price,
        )
        if not ok:
            return None
        return current_price

    async def update_thresholds(
        self,
        telegram_user_id: int,
        monitor_id: int,
        threshold_percent: Decimal,
        threshold_rub: Decimal,
    ) -> bool:
        return await self.db.tbank_price_monitor.update_thresholds(
            monitor_id=monitor_id,
            telegram_user_id=telegram_user_id,
            threshold_percent=threshold_percent,
            threshold_rub=threshold_rub,
        )

    async def check_monitors(self, telegram_user_id: int) -> TBankMonitorCheckResponse:
        connector = self._require_connector()
        rows = await self.db.tbank_price_monitor.list_by_user(telegram_user_id)
        now_msk = datetime.now(ZoneInfo("Europe/Moscow")).replace(tzinfo=None)
        events: list[TBankMonitorTriggeredEvent] = []
        due_rows = []

        for row in rows:
            if not row.is_active:
                continue

            if row.last_checked_at_msk is not None:
                delta = now_msk - row.last_checked_at_msk
                if delta.total_seconds() < row.interval_seconds:
                    continue

            due_rows.append(row)

        checked = len(due_rows)
        prices_by_figi: dict[str, Decimal] = {}
        if due_rows:
            requested_figies = list(
                dict.fromkeys([row.figi.strip() for row in due_rows if row.figi and row.figi.strip()])
            )
            try:
                prices_by_figi = await connector.get_last_prices(requested_figies)
            except TBankInvestRequestError:
                prices_by_figi = {}

        for row in due_rows:
            figi = row.figi.strip()
            current_price = prices_by_figi.get(figi)
            if not isinstance(current_price, Decimal):
                await self.db.tbank_price_monitor.touch_checked(row.id, now_msk)
                continue

            base_price = row.base_price
            if base_price == 0:
                await self.db.tbank_price_monitor.touch_checked(row.id, now_msk)
                continue

            change_rub = current_price - base_price
            change_percent = (change_rub / base_price) * Decimal("100")
            hit = abs(change_percent) >= abs(row.threshold_percent) or abs(change_rub) >= abs(row.threshold_rub)

            if hit:
                if not row.alert_active:
                    await self.db.tbank_price_monitor.touch_notified(row.id, now_msk)
                    events.append(
                        TBankMonitorTriggeredEvent(
                            monitor_id=row.id,
                            figi=row.figi,
                            ticker=row.ticker,
                            instrument_name=row.instrument_name,
                            current_price=str(current_price),
                            base_price=str(base_price),
                            change_percent=str(change_percent.quantize(Decimal("0.01"))),
                            change_rub=str(change_rub.quantize(Decimal("0.01"))),
                            threshold_percent=str(row.threshold_percent),
                            threshold_rub=str(row.threshold_rub),
                            triggered_at_msk=now_msk.isoformat(),
                        )
                    )
                else:
                    await self.db.tbank_price_monitor.touch_checked(row.id, now_msk)
            else:
                if row.alert_active:
                    await self.db.tbank_price_monitor.clear_alert(row.id, now_msk)
                else:
                    await self.db.tbank_price_monitor.touch_checked(row.id, now_msk)

        return TBankMonitorCheckResponse(checked=checked, triggered=len(events), events=events)

    async def check_all_monitors(self) -> TBankMonitorGlobalCheckResponse:
        user_ids = await self.db.tbank_price_monitor.list_active_user_ids()
        all_events: list[TBankMonitorTriggeredGlobalEvent] = []
        checked_total = 0

        for telegram_user_id in user_ids:
            result = await self.check_monitors(telegram_user_id)
            checked_total += result.checked
            for event in result.events:
                all_events.append(
                    TBankMonitorTriggeredGlobalEvent(
                        telegram_user_id=telegram_user_id,
                        **event.model_dump(),
                    )
                )

        return TBankMonitorGlobalCheckResponse(
            checked_users=len(user_ids),
            checked_monitors=checked_total,
            triggered=len(all_events),
            events=all_events,
        )

    @staticmethod
    def _to_item(row) -> TBankMonitorItem:
        return TBankMonitorItem(
            id=row.id,
            telegram_user_id=row.telegram_user_id,
            figi=row.figi,
            ticker=row.ticker,
            instrument_name=row.instrument_name,
            interval_seconds=row.interval_seconds,
            threshold_percent=row.threshold_percent,
            threshold_rub=row.threshold_rub,
            base_price=row.base_price,
            is_active=row.is_active,
            last_checked_at_msk=row.last_checked_at_msk,
            last_notified_at_msk=row.last_notified_at_msk,
        )

    def _require_connector(self) -> TBankInvestConnector:
        if self.connector is None:
            raise RuntimeError("TBank connector is not configured")
        return self.connector
