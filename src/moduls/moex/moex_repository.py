from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from src.moduls.base.base import BaseRepository
from src.moduls.moex.moex_share import MoexShare


class MoexShareRepository(BaseRepository):
    model = MoexShare

    async def mark_all_inactive(self) -> None:
        await self.session.execute(update(self.model).values(is_active=False))

    async def upsert_many(self, rows: list[dict]) -> int:
        if not rows:
            return 0

        now = datetime.now(timezone.utc)
        prepared_rows = [{**row, "last_synced_at": now, "is_active": True} for row in rows]
        insert_stmt = insert(self.model).values(prepared_rows)

        update_columns = {
            "shortname": insert_stmt.excluded.shortname,
            "security_name": insert_stmt.excluded.security_name,
            "isin": insert_stmt.excluded.isin,
            "regnumber": insert_stmt.excluded.regnumber,
            "boardid": insert_stmt.excluded.boardid,
            "primary_boardid": insert_stmt.excluded.primary_boardid,
            "sectype": insert_stmt.excluded.sectype,
            "group_name": insert_stmt.excluded.group_name,
            "is_traded": insert_stmt.excluded.is_traded,
            "is_active": insert_stmt.excluded.is_active,
            "security_payload": insert_stmt.excluded.security_payload,
            "details_payload": insert_stmt.excluded.details_payload,
            "details_error": insert_stmt.excluded.details_error,
            "last_synced_at": insert_stmt.excluded.last_synced_at,
            "updated_at": now,
        }

        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=[self.model.secid],
            set_=update_columns,
        )
        await self.session.execute(upsert_stmt)
        return len(rows)

    async def list_active(self, limit: int = 100, offset: int = 0) -> list[MoexShare]:
        stmt = (
            select(self.model)
            .where(self.model.is_active.is_(True))
            .order_by(self.model.secid.asc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count_active(self) -> int:
        stmt = select(func.count(self.model.id)).where(self.model.is_active.is_(True))
        result = await self.session.execute(stmt)
        return int(result.scalar_one())
