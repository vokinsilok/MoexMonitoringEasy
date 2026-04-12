from typing import Any

from pydantic import BaseModel, Field


class MoexShareItem(BaseModel):
    security: dict[str, Any] = Field(..., description="Базовые данные по акции из MOEX securities.")
    details: dict[str, Any] | None = Field(
        default=None,
        description="Полный payload MOEX ISS по конкретной бумаге (/iss/securities/{SECID}.json).",
    )
    details_error: str | None = Field(
        default=None,
        description="Причина, почему не удалось получить details для бумаги.",
    )


class MoexSharesResponse(BaseModel):
    total: int = Field(..., description="Количество найденных акций.")
    with_details: bool = Field(..., description="Были ли загружены details по каждой акции.")
    items: list[MoexShareItem] = Field(..., description="Список акций и связанных с ними деталей.")


class MoexStoredShareItem(BaseModel):
    secid: str
    shortname: str | None = None
    security_name: str | None = None
    isin: str | None = None
    regnumber: str | None = None
    boardid: str | None = None
    primary_boardid: str | None = None
    sectype: str | None = None
    group_name: str | None = None
    is_traded: bool | None = None
    is_active: bool
    security_payload: dict[str, Any]
    details_payload: dict[str, Any] | None = None
    details_error: str | None = None
    last_synced_at: Any

    model_config = {"from_attributes": True}


class MoexStoredSharesResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[MoexStoredShareItem]


class MoexSyncResponse(BaseModel):
    synced: int = Field(..., description="Сколько акций было обработано и сохранено.")
    with_details: bool = Field(..., description="Были ли загружены details перед сохранением.")
