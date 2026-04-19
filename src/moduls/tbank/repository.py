from datetime import datetime, timezone

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.moduls.base.base import BaseRepository
from src.moduls.tbank.models import (
    TBankFavoriteShare,
    TBankPriceMonitor,
    TBankShare,
    TBankSharePrice,
    TBankUserCredential,
)


class TBankShareRepository(BaseRepository):
    model = TBankShare

    async def mark_all_inactive(self) -> None:
        await self.session.execute(update(self.model).values(is_active=False))

    async def upsert_many(self, rows: list[dict]) -> int:
        if not rows:
            return 0

        now = datetime.now(timezone.utc)
        prepared_rows = [{**row, "last_synced_at": now, "is_active": True} for row in rows]
        insert_stmt = pg_insert(self.model).values(prepared_rows)

        update_columns = {
            "ticker": insert_stmt.excluded.ticker,
            "class_code": insert_stmt.excluded.class_code,
            "isin": insert_stmt.excluded.isin,
            "instrument_name": insert_stmt.excluded.instrument_name,
            "currency": insert_stmt.excluded.currency,
            "exchange": insert_stmt.excluded.exchange,
            "country_of_risk": insert_stmt.excluded.country_of_risk,
            "buy_available": insert_stmt.excluded.buy_available,
            "sell_available": insert_stmt.excluded.sell_available,
            "api_trade_available": insert_stmt.excluded.api_trade_available,
            "short_enabled": insert_stmt.excluded.short_enabled,
            "real_exchange": insert_stmt.excluded.real_exchange,
            "is_active": insert_stmt.excluded.is_active,
            "instrument_payload": insert_stmt.excluded.instrument_payload,
            "last_synced_at": insert_stmt.excluded.last_synced_at,
            "updated_at": now,
        }

        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=[self.model.figi],
            set_=update_columns,
        )
        await self.session.execute(upsert_stmt)
        return len(rows)

    async def list_active(self, limit: int = 100, offset: int = 0) -> list[TBankShare]:
        stmt = (
            select(self.model)
            .where(self.model.is_active.is_(True))
            .order_by(self.model.ticker.asc().nulls_last(), self.model.figi.asc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count_active(self) -> int:
        stmt = select(func.count(self.model.id)).where(self.model.is_active.is_(True))
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def list_active_ids(self) -> list[int]:
        stmt = select(self.model.id).where(self.model.is_active.is_(True))
        result = await self.session.execute(stmt)
        return [share_id for share_id in result.scalars().all() if share_id]

    async def get_ids_by_figi(self, figies: list[str]) -> dict[str, int]:
        unique_figies = list(dict.fromkeys([figi.strip() for figi in figies if figi and figi.strip()]))
        if not unique_figies:
            return {}

        stmt = select(self.model.figi, self.model.id).where(self.model.figi.in_(unique_figies))
        result = await self.session.execute(stmt)
        return {figi: share_id for figi, share_id in result.all()}

    async def get_active_by_figi(self, figi: str) -> TBankShare | None:
        figi_value = figi.strip()
        if not figi_value:
            return None

        stmt = (
            select(self.model)
            .where(self.model.figi == figi_value)
            .where(self.model.is_active.is_(True))
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()


class TBankSharePriceRepository(BaseRepository):
    model = TBankSharePrice

    async def create_many(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        await self.session.execute(insert(self.model).values(rows))
        return len(rows)

    async def touch_latest_for_share_ids(self, share_ids: list[int], values: dict) -> int:
        unique_ids = list(dict.fromkeys([share_id for share_id in share_ids if share_id]))
        if not unique_ids:
            return 0

        latest_ids_subquery = (
            select(self.model.id)
            .where(self.model.share_id.in_(unique_ids))
            .distinct(self.model.share_id)
            .order_by(
                self.model.share_id.asc(),
                self.model.captured_at_msk.desc(),
                self.model.id.desc(),
            )
        )
        update_stmt = update(self.model).where(self.model.id.in_(latest_ids_subquery)).values(**values)
        result = await self.session.execute(update_stmt)
        return int(result.rowcount or 0)

    async def get_max_captured_at_for_share_ids(self, share_ids: list[int]) -> datetime | None:
        unique_ids = list(dict.fromkeys([share_id for share_id in share_ids if share_id]))
        if not unique_ids:
            return None

        stmt = select(func.max(self.model.captured_at_msk)).where(self.model.share_id.in_(unique_ids))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_first_price_since(self, share_ids: list[int], since_msk: datetime) -> dict[int, object]:
        unique_ids = list(dict.fromkeys([share_id for share_id in share_ids if share_id]))
        if not unique_ids:
            return {}

        stmt = (
            select(self.model.share_id, self.model.price)
            .where(self.model.share_id.in_(unique_ids))
            .where(self.model.captured_at_msk >= since_msk)
            .distinct(self.model.share_id)
            .order_by(
                self.model.share_id.asc(),
                self.model.captured_at_msk.asc(),
                self.model.id.asc(),
            )
        )
        result = await self.session.execute(stmt)
        return {share_id: price for share_id, price in result.all()}

    async def get_latest_price_before(self, share_ids: list[int], before_msk: datetime) -> dict[int, object]:
        unique_ids = list(dict.fromkeys([share_id for share_id in share_ids if share_id]))
        if not unique_ids:
            return {}

        stmt = (
            select(self.model.share_id, self.model.price)
            .where(self.model.share_id.in_(unique_ids))
            .where(self.model.captured_at_msk < before_msk)
            .distinct(self.model.share_id)
            .order_by(
                self.model.share_id.asc(),
                self.model.captured_at_msk.desc(),
                self.model.id.desc(),
            )
        )
        result = await self.session.execute(stmt)
        return {share_id: price for share_id, price in result.all()}

    async def get_latest_by_share_ids(self, share_ids: list[int]) -> dict[int, tuple[object, object, bool | None, object, object, object, object, object, object, object, object]]:
        unique_ids = list(dict.fromkeys([share_id for share_id in share_ids if share_id]))
        if not unique_ids:
            return {}

        latest_stmt = (
            select(
                self.model.share_id,
                self.model.price,
                self.model.captured_at_msk,
                self.model.trading_open,
                self.model.session_opened_at_msk,
                self.model.session_closed_at_msk,
                self.model.next_session_opened_at_msk,
                self.model.next_session_closed_at_msk,
                self.model.day_open_price,
                self.model.day_close_price,
                self.model.day_change_percent,
                self.model.year_change_percent,
            )
            .where(self.model.share_id.in_(unique_ids))
            .distinct(self.model.share_id)
            .order_by(
                self.model.share_id.asc(),
                self.model.captured_at_msk.desc(),
                self.model.id.desc(),
            )
        )
        result = await self.session.execute(latest_stmt)
        return {
            share_id: (
                price,
                captured_at_msk,
                trading_open,
                session_opened_at_msk,
                session_closed_at_msk,
                next_session_opened_at_msk,
                next_session_closed_at_msk,
                day_open_price,
                day_close_price,
                day_change_percent,
                year_change_percent,
            )
            for (
                share_id,
                price,
                captured_at_msk,
                trading_open,
                session_opened_at_msk,
                session_closed_at_msk,
                next_session_opened_at_msk,
                next_session_closed_at_msk,
                day_open_price,
                day_close_price,
                day_change_percent,
                year_change_percent,
            ) in result.all()
        }


class TBankUserCredentialRepository(BaseRepository):
    model = TBankUserCredential

    async def get_active_by_telegram_user_id(self, telegram_user_id: int) -> TBankUserCredential | None:
        stmt = (
            select(self.model)
            .where(self.model.telegram_user_id == telegram_user_id)
            .where(self.model.is_active.is_(True))
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def upsert_credentials(self, telegram_user_id: int, tbank_token: str, tbank_account_id: str) -> TBankUserCredential:
        now = datetime.now(timezone.utc)
        insert_stmt = pg_insert(self.model).values(
            telegram_user_id=telegram_user_id,
            tbank_token=tbank_token,
            tbank_account_id=tbank_account_id,
            is_active=True,
            updated_at=now,
        )
        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=[self.model.telegram_user_id],
            set_={
                "tbank_token": insert_stmt.excluded.tbank_token,
                "tbank_account_id": insert_stmt.excluded.tbank_account_id,
                "is_active": True,
                "updated_at": now,
            },
        ).returning(self.model)
        result = await self.session.execute(upsert_stmt)
        return result.scalar_one()

    async def deactivate(self, telegram_user_id: int) -> bool:
        stmt = (
            update(self.model)
            .where(self.model.telegram_user_id == telegram_user_id)
            .where(self.model.is_active.is_(True))
            .values(is_active=False, updated_at=datetime.now(timezone.utc))
        )
        result = await self.session.execute(stmt)
        return bool(result.rowcount)


class TBankFavoriteShareRepository(BaseRepository):
    model = TBankFavoriteShare

    async def list_figies_by_user(self, telegram_user_id: int) -> list[str]:
        stmt = (
            select(self.model.figi)
            .where(self.model.telegram_user_id == telegram_user_id)
            .order_by(self.model.created_at.desc(), self.model.id.desc())
        )
        result = await self.session.execute(stmt)
        return [str(figi) for figi in result.scalars().all() if figi]

    async def add(self, telegram_user_id: int, figi: str) -> bool:
        figi_value = figi.strip()
        if not figi_value:
            return False
        insert_stmt = pg_insert(self.model).values(
            telegram_user_id=telegram_user_id,
            figi=figi_value,
            updated_at=datetime.now(timezone.utc),
        )
        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=[self.model.telegram_user_id, self.model.figi],
            set_={"updated_at": datetime.now(timezone.utc)},
        )
        await self.session.execute(upsert_stmt)
        return True

    async def remove(self, telegram_user_id: int, figi: str) -> bool:
        figi_value = figi.strip()
        if not figi_value:
            return False
        stmt = (
            self.model.__table__.delete()
            .where(self.model.telegram_user_id == telegram_user_id)
            .where(self.model.figi == figi_value)
        )
        result = await self.session.execute(stmt)
        return bool(result.rowcount)

    async def is_favorite(self, telegram_user_id: int, figi: str) -> bool:
        figi_value = figi.strip()
        if not figi_value:
            return False
        stmt = (
            select(self.model.id)
            .where(self.model.telegram_user_id == telegram_user_id)
            .where(self.model.figi == figi_value)
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None


class TBankPriceMonitorRepository(BaseRepository):
    model = TBankPriceMonitor

    async def list_active_user_ids(self) -> list[int]:
        stmt = (
            select(self.model.telegram_user_id)
            .where(self.model.is_active.is_(True))
            .distinct()
            .order_by(self.model.telegram_user_id.asc())
        )
        result = await self.session.execute(stmt)
        return [int(user_id) for user_id in result.scalars().all() if user_id]

    async def create_monitor(
        self,
        *,
        telegram_user_id: int,
        figi: str,
        ticker: str | None,
        instrument_name: str | None,
        interval_minutes: int,
        threshold_percent: object,
        threshold_rub: object,
        base_price: object,
    ) -> TBankPriceMonitor:
        row = self.model(
            telegram_user_id=telegram_user_id,
            figi=figi,
            ticker=ticker,
            instrument_name=instrument_name,
            interval_minutes=interval_minutes,
            threshold_percent=threshold_percent,
            threshold_rub=threshold_rub,
            base_price=base_price,
            is_active=True,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_by_user(self, telegram_user_id: int) -> list[TBankPriceMonitor]:
        stmt = (
            select(self.model)
            .where(self.model.telegram_user_id == telegram_user_id)
            .order_by(self.model.is_active.desc(), self.model.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_by_id_and_user(self, monitor_id: int, telegram_user_id: int) -> TBankPriceMonitor | None:
        stmt = (
            select(self.model)
            .where(self.model.id == monitor_id)
            .where(self.model.telegram_user_id == telegram_user_id)
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def set_active(self, monitor_id: int, telegram_user_id: int, is_active: bool) -> bool:
        stmt = (
            update(self.model)
            .where(self.model.id == monitor_id)
            .where(self.model.telegram_user_id == telegram_user_id)
            .values(is_active=is_active, updated_at=datetime.now(timezone.utc))
        )
        result = await self.session.execute(stmt)
        return bool(result.rowcount)

    async def delete_by_id_and_user(self, monitor_id: int, telegram_user_id: int) -> bool:
        stmt = (
            self.model.__table__.delete()
            .where(self.model.id == monitor_id)
            .where(self.model.telegram_user_id == telegram_user_id)
        )
        result = await self.session.execute(stmt)
        return bool(result.rowcount)

    async def touch_checked(self, monitor_id: int, checked_at_msk: datetime) -> None:
        stmt = (
            update(self.model)
            .where(self.model.id == monitor_id)
            .values(last_checked_at_msk=checked_at_msk, updated_at=datetime.now(timezone.utc))
        )
        await self.session.execute(stmt)

    async def touch_notified(self, monitor_id: int, notified_at_msk: datetime) -> None:
        stmt = (
            update(self.model)
            .where(self.model.id == monitor_id)
            .values(
                last_checked_at_msk=notified_at_msk,
                last_notified_at_msk=notified_at_msk,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await self.session.execute(stmt)

    async def set_base_price(self, monitor_id: int, telegram_user_id: int, base_price: object) -> bool:
        stmt = (
            update(self.model)
            .where(self.model.id == monitor_id)
            .where(self.model.telegram_user_id == telegram_user_id)
            .values(base_price=base_price, updated_at=datetime.now(timezone.utc))
        )
        result = await self.session.execute(stmt)
        return bool(result.rowcount)

    async def update_thresholds(
        self,
        monitor_id: int,
        telegram_user_id: int,
        threshold_percent: object,
        threshold_rub: object,
    ) -> bool:
        stmt = (
            update(self.model)
            .where(self.model.id == monitor_id)
            .where(self.model.telegram_user_id == telegram_user_id)
            .values(
                threshold_percent=threshold_percent,
                threshold_rub=threshold_rub,
                updated_at=datetime.now(timezone.utc),
            )
        )
        result = await self.session.execute(stmt)
        return bool(result.rowcount)
