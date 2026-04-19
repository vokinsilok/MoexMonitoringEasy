from dataclasses import dataclass


@dataclass(slots=True)
class ShareViewItem:
    id: int
    figi: str
    ticker: str
    name: str
    currency: str
    exchange: str
    lot: int | None
    nominal: str | None
    country_of_risk: str
    buy_available: bool | None
    sell_available: bool | None
    api_trade_available: bool | None
    short_enabled: bool | None
    isin: str | None
    class_code: str | None
    sector: str | None
    last_price: str | None
    last_price_captured_at_msk: str | None
    trading_open: bool | None
    session_opened_at_msk: str | None
    session_closed_at_msk: str | None
    next_session_opened_at_msk: str | None
    next_session_closed_at_msk: str | None
    day_open_price: str | None
    day_close_price: str | None
    day_change_percent: str | None
    year_change_percent: str | None
