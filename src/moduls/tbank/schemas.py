from typing import Any

from pydantic import BaseModel, Field, model_validator


class TBankShareItem(BaseModel):
    instrument: dict[str, Any]


class TBankSharesResponse(BaseModel):
    total: int = Field(..., description="Количество акций, полученных из T-Bank API")
    instrument_status: str = Field(..., description="Примененный фильтр instrumentStatus")
    instrument_exchange: str = Field(..., description="Примененный фильтр instrumentExchange")
    items: list[TBankShareItem]


class TBankStoredShareItem(BaseModel):
    id: int
    figi: str
    instrument_type: str = "share"
    ticker: str | None = None
    class_code: str | None = None
    isin: str | None = None
    instrument_name: str | None = None
    currency: str | None = None
    exchange: str | None = None
    country_of_risk: str | None = None
    buy_available: bool | None = None
    sell_available: bool | None = None
    api_trade_available: bool | None = None
    short_enabled: bool | None = None
    real_exchange: str | None = None
    exchange_display: str | None = None
    lot: int | None = None
    sector: str | None = None
    nominal: str | None = None
    is_active: bool
    last_synced_at: Any
    last_price: Any = None
    last_price_captured_at_msk: Any = None
    trading_open: bool | None = None
    session_opened_at_msk: Any = None
    session_closed_at_msk: Any = None
    next_session_opened_at_msk: Any = None
    next_session_closed_at_msk: Any = None
    day_open_price: Any = None
    day_close_price: Any = None
    day_change_percent: Any = None
    year_change_percent: Any = None
    trading_status: str | None = None
    limit_order_available: bool | None = None
    market_order_available: bool | None = None

    model_config = {"from_attributes": True}


class TBankStoredSharesResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[TBankStoredShareItem]


class TBankSyncResponse(BaseModel):
    synced: int = Field(..., description="Сколько акций было сохранено в базу данных")
    prices_saved: int = Field(..., description="Сколько цен акций было сохранено в историю")
    instrument_status: str = Field(..., description="Примененный фильтр instrumentStatus")
    instrument_exchange: str = Field(..., description="Примененный фильтр instrumentExchange")
    instrument_types: list[str] = Field(default_factory=lambda: ["share"], description="Типы синхронизированных инструментов")
    trading_open: bool = Field(..., description="Открыты ли торги на MOEX в текущий момент")
    skipped: bool = Field(
        default=False,
        description="True, если синхронизация была пропущена после предпроверки",
    )
    skip_reason: str | None = Field(
        default=None,
        description="Причина пропуска синхронизации при закрытых торгах",
    )


class TBankTaskEnqueueResponse(BaseModel):
    task_id: str
    queue_name: str


class TradingDirection(str):
    BUY = "ORDER_DIRECTION_BUY"
    SELL = "ORDER_DIRECTION_SELL"


class TBankOrderCreateRequest(BaseModel):
    telegram_user_id: int = Field(..., ge=1)
    figi: str = Field(..., min_length=1)
    quantity_lots: int = Field(..., ge=1)
    direction: str = Field(..., examples=["ORDER_DIRECTION_BUY", "ORDER_DIRECTION_SELL"])
    order_type: str = Field(
        ...,
        examples=["ORDER_TYPE_LIMIT", "ORDER_TYPE_MARKET", "ORDER_TYPE_BESTPRICE"],
    )
    price: str | None = Field(default=None, description="Обязательная для LIMIT, не нужна для MARKET/BESTPRICE")
    confirm_margin_trade: bool | None = Field(default=None)


class TBankOrderCancelRequest(BaseModel):
    telegram_user_id: int = Field(..., ge=1)
    order_id: str = Field(..., min_length=1)


class TBankStopOrderCreateRequest(BaseModel):
    telegram_user_id: int = Field(..., ge=1)
    figi: str = Field(..., min_length=1)
    quantity_lots: int = Field(..., ge=1)
    direction: str = Field(..., examples=["STOP_ORDER_DIRECTION_BUY", "STOP_ORDER_DIRECTION_SELL"])
    stop_order_type: str = Field(
        ...,
        examples=["STOP_ORDER_TYPE_STOP_LOSS", "STOP_ORDER_TYPE_STOP_LIMIT", "STOP_ORDER_TYPE_TAKE_PROFIT"],
    )
    stop_price: str = Field(..., min_length=1)
    price: str | None = Field(default=None, description="Лимитная цена исполнения (для StopLimit/TakeProfit)")
    expiration_type: str = Field(default="STOP_ORDER_EXPIRATION_TYPE_GOOD_TILL_CANCEL")
    confirm_margin_trade: bool | None = Field(default=None)

    @model_validator(mode="after")
    def validate_prices(self) -> "TBankStopOrderCreateRequest":
        if self.stop_order_type == "STOP_ORDER_TYPE_STOP_LIMIT" and self.price is None:
            raise ValueError("price is required for STOP_ORDER_TYPE_STOP_LIMIT")
        return self


class TBankStopOrderCancelRequest(BaseModel):
    telegram_user_id: int = Field(..., ge=1)
    stop_order_id: str = Field(..., min_length=1)


class TBankTradingActionResponse(BaseModel):
    ok: bool = True
    details: dict[str, Any]


class TBankAllowedSectorsResponse(BaseModel):
    sectors: list[str]


class TBankUserCredentialsUpsertRequest(BaseModel):
    telegram_user_id: int = Field(..., ge=1)
    tbank_token: str = Field(..., min_length=10)
    tbank_account_id: str = Field(..., min_length=3)


class TBankUserCredentialsStatusResponse(BaseModel):
    telegram_user_id: int
    configured: bool
    tbank_account_id: str | None = None


class TBankFavoriteShareRequest(BaseModel):
    telegram_user_id: int = Field(..., ge=1)
    figi: str = Field(..., min_length=1)


class TBankFavoriteSharesResponse(BaseModel):
    telegram_user_id: int
    total: int
    figies: list[str]


class TBankBotAccessRequest(BaseModel):
    telegram_user_id: int = Field(..., ge=1)
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None


class TBankBotAccessActionRequest(BaseModel):
    telegram_user_id: int = Field(..., ge=1)
    admin_telegram_user_id: int = Field(..., ge=1)


class TBankBotAccessStatusResponse(BaseModel):
    telegram_user_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    status: str
    is_allowed: bool
    requested_at: Any = None
    approved_at: Any = None
    revoked_at: Any = None
    approved_by: int | None = None
    revoked_by: int | None = None


class TBankBotAccessListResponse(BaseModel):
    total: int
    items: list[TBankBotAccessStatusResponse]


class TBankMonitorCreateRequest(BaseModel):
    telegram_user_id: int = Field(..., ge=1)
    figi: str = Field(..., min_length=1)
    interval_seconds: int = Field(..., ge=5, le=86400)
    threshold_percent: str = Field(..., min_length=1)
    threshold_rub: str = Field(..., min_length=1)
    base_price: str = Field(..., min_length=1)
    ticker: str | None = None
    instrument_name: str | None = None


class TBankMonitorToggleRequest(BaseModel):
    telegram_user_id: int = Field(..., ge=1)
    monitor_id: int = Field(..., ge=1)
    is_active: bool


class TBankMonitorDeleteRequest(BaseModel):
    telegram_user_id: int = Field(..., ge=1)
    monitor_id: int = Field(..., ge=1)


class TBankMonitorRebaseRequest(BaseModel):
    telegram_user_id: int = Field(..., ge=1)
    monitor_id: int = Field(..., ge=1)


class TBankMonitorUpdateThresholdsRequest(BaseModel):
    telegram_user_id: int = Field(..., ge=1)
    monitor_id: int = Field(..., ge=1)
    threshold_percent: str = Field(..., min_length=1)
    threshold_rub: str = Field(..., min_length=1)


class TBankMonitorItem(BaseModel):
    id: int
    telegram_user_id: int
    figi: str
    ticker: str | None = None
    instrument_name: str | None = None
    interval_seconds: int
    threshold_percent: Any
    threshold_rub: Any
    base_price: Any
    is_active: bool
    last_checked_at_msk: Any = None
    last_notified_at_msk: Any = None


class TBankMonitorListResponse(BaseModel):
    total: int
    items: list[TBankMonitorItem]


class TBankMonitorTriggeredEvent(BaseModel):
    monitor_id: int
    figi: str
    ticker: str | None = None
    instrument_name: str | None = None
    current_price: str
    base_price: str
    change_percent: str
    change_rub: str
    threshold_percent: str
    threshold_rub: str
    triggered_at_msk: str


class TBankMonitorCheckResponse(BaseModel):
    checked: int
    triggered: int
    events: list[TBankMonitorTriggeredEvent]


class TBankMonitorTriggeredGlobalEvent(TBankMonitorTriggeredEvent):
    telegram_user_id: int


class TBankMonitorGlobalCheckResponse(BaseModel):
    checked_users: int
    checked_monitors: int
    triggered: int
    events: list[TBankMonitorTriggeredGlobalEvent]


class TBankPortfolioRequest(BaseModel):
    telegram_user_id: int = Field(..., ge=1)


class TBankPortfolioAnalysisRequest(BaseModel):
    telegram_user_id: int = Field(..., ge=1)
    horizon: str = Field(..., examples=["1d", "7d", "1m", "1y"])


class TBankPortfolioAnalysisResponse(BaseModel):
    horizon: str
    horizon_label: str
    generated_at_msk: str
    model: str
    report: str
    portfolio: dict[str, Any]


class TBankOperationsRequest(BaseModel):
    telegram_user_id: int = Field(..., ge=1)
    days: int = Field(default=30, ge=1, le=365)
