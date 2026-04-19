from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from src.connectors.tbank_invest_connector import TBankInvestConnector, TBankInvestRequestError
from src.moduls.tbank.schemas import (
    TBankMonitorGlobalCheckResponse,
    TBankMonitorCheckResponse,
    TBankMonitorCreateRequest,
    TBankMonitorItem,
    TBankMonitorListResponse,
    TBankMonitorTriggeredGlobalEvent,
    TBankMonitorTriggeredEvent,
    TBankShareItem,
    TBankSharesResponse,
    TBankStoredShareItem,
    TBankStoredSharesResponse,
    TBankSyncResponse,
)
from src.utils.db_manager import DBManager


class TBankSharesService:
    def __init__(self, connector: TBankInvestConnector, db: DBManager | None = None) -> None:
        self.connector = connector
        self.db = db

    async def get_shares(
        self,
        instrument_status: str = "INSTRUMENT_STATUS_BASE",
        instrument_exchange: str = "INSTRUMENT_EXCHANGE_UNSPECIFIED",
        russian_only: bool = True,
        include_dealer: bool = True,
    ) -> TBankSharesResponse:
        instruments = await self._load_instruments(
            instrument_status=instrument_status,
            instrument_exchange=instrument_exchange,
            include_dealer=include_dealer,
        )
        if russian_only:
            instruments = [item for item in instruments if self._is_russian_share(item)]

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
        if self.db is None:
            raise RuntimeError("DB manager is not configured for sync operation")

        trading_status = None
        trading_open = True
        if check_trading_open:
            try:
                trading_status = await self.connector.get_exchange_trading_status(exchange="MOEX")
                trading_open = trading_status.trading_open
            except TBankInvestRequestError:
                trading_status = None
                trading_open = True
        instruments = await self._load_instruments(
            instrument_status=instrument_status,
            instrument_exchange=instrument_exchange,
            include_dealer=include_dealer,
        )
        if russian_only:
            instruments = [item for item in instruments if self._is_russian_share(item)]

        rows = [self._to_storage_row(item) for item in instruments]
        rows = [row for row in rows if row["figi"]]

        await self.db.tbank_share.mark_all_inactive()
        synced = await self.db.tbank_share.upsert_many(rows)
        prices_saved = 0

        if synced > 0:
            figies = [row["figi"] for row in rows]
            share_ids_by_figi = await self.db.tbank_share.get_ids_by_figi(figies)
            last_prices = await self.connector.get_last_prices(figies)
            share_ids = [share_id for share_id in share_ids_by_figi.values() if share_id]
            now_msk = datetime.now(ZoneInfo("Europe/Moscow")).replace(tzinfo=None)
            day_start_msk = now_msk.replace(hour=0, minute=0, second=0, microsecond=0)
            year_cutoff_msk = now_msk - timedelta(days=365)
            day_open_prices = await self.db.tbank_share_price.get_first_price_since(share_ids, day_start_msk)
            day_close_prices = await self.db.tbank_share_price.get_latest_price_before(share_ids, day_start_msk)
            year_base_prices = await self.db.tbank_share_price.get_latest_price_before(share_ids, year_cutoff_msk)
            prices_rows = self._to_price_rows(
                last_prices,
                share_ids_by_figi,
                trading_open=trading_open,
                session_opened_at_msk=trading_status.session_opened_at_msk if trading_status else None,
                session_closed_at_msk=trading_status.session_closed_at_msk if trading_status else None,
                next_session_opened_at_msk=trading_status.next_session_opened_at_msk if trading_status else None,
                next_session_closed_at_msk=trading_status.next_session_closed_at_msk if trading_status else None,
                day_open_prices=day_open_prices,
                day_close_prices=day_close_prices,
                year_base_prices=year_base_prices,
            )
            prices_saved = await self.db.tbank_share_price.create_many(prices_rows)

        return TBankSyncResponse(
            synced=synced,
            prices_saved=prices_saved,
            instrument_status=instrument_status,
            instrument_exchange=instrument_exchange,
            trading_open=trading_open,
            skipped=False,
            skip_reason=None,
        )

    async def _load_instruments(
        self,
        *,
        instrument_status: str,
        instrument_exchange: str,
        include_dealer: bool,
    ) -> list[dict]:
        primary_result = await self.connector.get_shares(
            instrument_status=instrument_status,
            instrument_exchange=instrument_exchange,
        )
        instruments = list(primary_result.instruments)

        should_load_dealer = include_dealer and instrument_exchange == "INSTRUMENT_EXCHANGE_UNSPECIFIED"
        if not should_load_dealer:
            return instruments

        # Внебиржевые инструменты (dealer) запрашиваются отдельным фильтром и
        # объединяются по FIGI, чтобы мониторинг видел обе площадки.
        try:
            dealer_result = await self.connector.get_shares(
                instrument_status="INSTRUMENT_STATUS_ALL",
                instrument_exchange="INSTRUMENT_EXCHANGE_DEALER",
            )
            dealer_instruments = dealer_result.instruments
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
        if self.db is None:
            raise RuntimeError("DB manager is not configured for read operation")

        items = await self.db.tbank_share.list_active(limit=limit, offset=offset)
        total = await self.db.tbank_share.count_active()
        share_ids = [item.id for item in items if getattr(item, "id", None)]
        latest_prices = await self.db.tbank_share_price.get_latest_by_share_ids(share_ids)

        response_items: list[TBankStoredShareItem] = []
        for item in items:
            (
                last_price,
                captured_at_msk,
                price_trading_open,
                session_opened_at_msk,
                session_closed_at_msk,
                next_session_opened_at_msk,
                next_session_closed_at_msk,
                day_open_price,
                day_close_price,
                day_change_percent,
                year_change_percent,
            ) = latest_prices.get(item.id, (None, None, None, None, None, None, None, None, None, None, None))
            exchange_display = self._to_exchange_display(item.real_exchange, item.exchange)
            response_items.append(
                TBankStoredShareItem.model_validate(
                    {
                        "id": item.id,
                        "figi": item.figi,
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
                        "is_active": item.is_active,
                        "instrument_payload": item.instrument_payload,
                        "last_synced_at": item.last_synced_at,
                        "last_price": last_price,
                        "last_price_captured_at_msk": captured_at_msk,
                        "trading_open": price_trading_open,
                        "session_opened_at_msk": session_opened_at_msk,
                        "session_closed_at_msk": session_closed_at_msk,
                        "next_session_opened_at_msk": next_session_opened_at_msk,
                        "next_session_closed_at_msk": next_session_closed_at_msk,
                        "day_open_price": day_open_price,
                        "day_close_price": day_close_price,
                        "day_change_percent": day_change_percent,
                        "year_change_percent": year_change_percent,
                    }
                )
            )

        return TBankStoredSharesResponse(
            total=total,
            limit=limit,
            offset=offset,
            items=response_items,
        )

    @staticmethod
    def _to_storage_row(instrument: dict) -> dict:
        return {
            "figi": str(instrument.get("figi", "")).strip(),
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
            "instrument_payload": instrument,
        }

    @staticmethod
    def _is_russian_share(instrument: dict) -> bool:
        country_of_risk = str(instrument.get("countryOfRisk", "")).strip().upper()
        return country_of_risk == "RU"

    @staticmethod
    def _to_price_rows(
        last_prices: dict[str, object],
        share_ids_by_figi: dict[str, int],
        trading_open: bool,
        session_opened_at_msk: datetime | None,
        session_closed_at_msk: datetime | None,
        next_session_opened_at_msk: datetime | None,
        next_session_closed_at_msk: datetime | None,
        day_open_prices: dict[int, object],
        day_close_prices: dict[int, object],
        year_base_prices: dict[int, object],
    ) -> list[dict]:
        captured_at_msk = datetime.now(ZoneInfo("Europe/Moscow")).replace(tzinfo=None)
        rows: list[dict] = []
        for figi, price in last_prices.items():
            share_id = share_ids_by_figi.get(figi)
            if not share_id:
                continue
            day_open_price = day_open_prices.get(share_id, price)
            day_close_price = day_close_prices.get(share_id)
            year_base_price = year_base_prices.get(share_id)
            rows.append(
                {
                    "share_id": share_id,
                    "price": price,
                    "trading_open": trading_open,
                    "session_opened_at_msk": session_opened_at_msk,
                    "session_closed_at_msk": session_closed_at_msk,
                    "next_session_opened_at_msk": next_session_opened_at_msk,
                    "next_session_closed_at_msk": next_session_closed_at_msk,
                    "day_open_price": day_open_price,
                    "day_close_price": day_close_price,
                    "day_change_percent": TBankSharesService._calculate_change_percent(price, day_close_price),
                    "year_change_percent": TBankSharesService._calculate_change_percent(price, year_base_price),
                    "captured_at_msk": captured_at_msk,
                }
            )
        return rows

    @staticmethod
    def _calculate_change_percent(current_price: object, base_price: object) -> Decimal | None:
        if not isinstance(current_price, Decimal) or not isinstance(base_price, Decimal):
            return None
        if base_price == 0:
            return None
        return ((current_price - base_price) / base_price) * Decimal("100")

    @staticmethod
    def _to_exchange_display(real_exchange: str | None, exchange: str | None) -> str | None:
        if real_exchange:
            normalized = str(real_exchange).replace("REAL_EXCHANGE_", "").strip()
            if normalized:
                return normalized
        if exchange:
            return str(exchange).upper()
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
    ) -> dict:
        if not self.account_id:
            raise TBankInvestRequestError("User account_id is not configured")

        stop_price_value = Decimal(stop_price.strip())
        limit_price_value = Decimal(price.strip()) if price and price.strip() else None

        return await self.connector.post_stop_order(
            account_id=self.account_id,
            instrument_id=figi,
            quantity_lots=quantity_lots,
            direction=direction,
            stop_order_type=stop_order_type,
            stop_price=stop_price_value,
            price=limit_price_value,
            expiration_type=expiration_type,
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


class TBankMonitorService:
    def __init__(self, db: DBManager) -> None:
        self.db = db

    async def create_monitor(self, payload: TBankMonitorCreateRequest) -> TBankMonitorItem:
        row = await self.db.tbank_price_monitor.create_monitor(
            telegram_user_id=payload.telegram_user_id,
            figi=payload.figi.strip(),
            ticker=payload.ticker,
            instrument_name=payload.instrument_name,
            interval_minutes=payload.interval_minutes,
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

        share = await self.db.tbank_share.get_active_by_figi(row.figi)
        if share is None:
            return None

        latest_map = await self.db.tbank_share_price.get_latest_by_share_ids([share.id])
        latest = latest_map.get(share.id)
        if not latest:
            return None
        current_price = latest[0]
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
        rows = await self.db.tbank_price_monitor.list_by_user(telegram_user_id)
        now_msk = datetime.now(ZoneInfo("Europe/Moscow")).replace(tzinfo=None)
        events: list[TBankMonitorTriggeredEvent] = []
        checked = 0

        for row in rows:
            if not row.is_active:
                continue

            if row.last_checked_at_msk is not None:
                delta = now_msk - row.last_checked_at_msk
                if delta.total_seconds() < row.interval_minutes * 60:
                    continue

            checked += 1
            share = await self.db.tbank_share.get_active_by_figi(row.figi)
            if share is None:
                await self.db.tbank_price_monitor.touch_checked(row.id, now_msk)
                continue

            latest_map = await self.db.tbank_share_price.get_latest_by_share_ids([share.id])
            latest = latest_map.get(share.id)
            if not latest:
                await self.db.tbank_price_monitor.touch_checked(row.id, now_msk)
                continue
            current_price = latest[0]
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
                can_notify = True
                if row.last_notified_at_msk is not None:
                    can_notify = (now_msk - row.last_notified_at_msk).total_seconds() >= row.interval_minutes * 60
                if can_notify:
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
            interval_minutes=row.interval_minutes,
            threshold_percent=row.threshold_percent,
            threshold_rub=row.threshold_rub,
            base_price=row.base_price,
            is_active=row.is_active,
            last_checked_at_msk=row.last_checked_at_msk,
            last_notified_at_msk=row.last_notified_at_msk,
        )
