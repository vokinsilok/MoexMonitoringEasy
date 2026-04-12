import asyncio

from src.connectors.moex_iss_connector import MoexISSConnector
from src.moduls.base.base_service import BaseService
from src.moduls.moex.schemas import (
    MoexShareItem,
    MoexSharesResponse,
    MoexStoredShareItem,
    MoexStoredSharesResponse,
    MoexSyncResponse,
)
from src.utils.db_manager import DBManager


class MoexSharesService(BaseService):
    def __init__(self, connector: MoexISSConnector, db: DBManager | None = None) -> None:
        super().__init__(db=db)
        self.connector = connector

    async def get_all_shares_with_info(
        self,
        with_details: bool = True,
        details_concurrency: int = 10,
    ) -> MoexSharesResponse:
        shares = await self.connector.get_all_shares()

        if not with_details:
            short_items = [MoexShareItem(security=share) for share in shares]
            return MoexSharesResponse(
                total=len(short_items),
                with_details=False,
                items=short_items,
            )

        semaphore = asyncio.Semaphore(details_concurrency)

        async def enrich_share(share: dict) -> MoexShareItem:
            secid = str(share.get("SECID", "")).strip()
            if not secid:
                return MoexShareItem(security=share, details_error="SECID is missing")

            async with semaphore:
                try:
                    details = await self.connector.get_security_details(secid)
                    return MoexShareItem(security=share, details=details)
                except Exception as exc:
                    return MoexShareItem(security=share, details_error=str(exc))

        enriched = await asyncio.gather(*(enrich_share(share) for share in shares))
        return MoexSharesResponse(
            total=len(enriched),
            with_details=True,
            items=enriched,
        )

    async def sync_shares_to_db(
        self,
        with_details: bool = False,
        details_concurrency: int = 10,
    ) -> MoexSyncResponse:
        if self.db is None:
            raise RuntimeError("DB manager is not configured for sync operation")

        response = await self.get_all_shares_with_info(
            with_details=with_details,
            details_concurrency=details_concurrency,
        )
        rows = [self._to_storage_row(item) for item in response.items]
        unique_rows = self._deduplicate_rows(rows)

        await self.db.moex_share.mark_all_inactive()
        synced = await self.db.moex_share.upsert_many(unique_rows)

        return MoexSyncResponse(synced=synced, with_details=with_details)

    async def get_stored_shares(
        self, limit: int = 100, offset: int = 0
    ) -> MoexStoredSharesResponse:
        if self.db is None:
            raise RuntimeError("DB manager is not configured for read operation")

        items = await self.db.moex_share.list_active(limit=limit, offset=offset)
        total = await self.db.moex_share.count_active()

        return MoexStoredSharesResponse(
            total=total,
            limit=limit,
            offset=offset,
            items=[MoexStoredShareItem.model_validate(item) for item in items],
        )

    @staticmethod
    def _to_storage_row(item: MoexShareItem) -> dict:
        security = item.security
        return {
            "secid": str(security.get("SECID", "")).strip(),
            "shortname": security.get("SHORTNAME"),
            "security_name": security.get("SECNAME") or security.get("NAME"),
            "isin": security.get("ISIN"),
            "regnumber": security.get("REGNUMBER"),
            "boardid": security.get("BOARDID"),
            "primary_boardid": security.get("PRIMARYBOARDID"),
            "sectype": security.get("SECTYPE"),
            "group_name": security.get("GROUP"),
            "is_traded": MoexSharesService._to_bool(security.get("IS_TRADED")),
            "security_payload": security,
            "details_payload": item.details,
            "details_error": item.details_error,
        }

    @staticmethod
    def _to_bool(value) -> bool | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"1", "true", "t", "yes"}:
            return True
        if text in {"0", "false", "f", "no"}:
            return False
        return None

    @staticmethod
    def _deduplicate_rows(rows: list[dict]) -> list[dict]:
        by_secid: dict[str, dict] = {}

        for row in rows:
            secid = row["secid"]
            current = by_secid.get(secid)
            if current is None:
                by_secid[secid] = row
                continue

            incoming_is_primary = (
                row.get("boardid") == row.get("primary_boardid") and row.get("boardid") is not None
            )
            current_is_primary = (
                current.get("boardid") == current.get("primary_boardid")
                and current.get("boardid") is not None
            )
            incoming_has_details = row.get("details_payload") is not None
            current_has_details = current.get("details_payload") is not None

            if incoming_is_primary and not current_is_primary:
                by_secid[secid] = row
            elif incoming_has_details and not current_has_details:
                by_secid[secid] = row

        return list(by_secid.values())
