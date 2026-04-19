from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot_service.models import ShareViewItem

PAGE_SIZE = 8
CB_PREFIX_PAGE = "shp"
CB_PREFIX_DETAILS = "shd"
CB_BACK_TO_LIST = "shb"
CB_REFRESH = "shr"
CB_SEARCH = "shs"
CB_MODE_ALL = "sha"
CB_MODE_FAVORITES = "shf"
CB_FAVORITE_TOGGLE_PREFIX = "shv"


def shares_list_keyboard(
    items: list[ShareViewItem],
    page: int,
    total_pages: int,
    mode: str = "all",
    has_query: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="\u041f\u043e\u0438\u0441\u043a", callback_data=CB_SEARCH),
            InlineKeyboardButton(text="\u0418\u0437\u0431\u0440\u0430\u043d\u043d\u043e\u0435", callback_data=CB_MODE_FAVORITES),
            InlineKeyboardButton(text="\u0412\u0441\u0435", callback_data=CB_MODE_ALL),
        ]
    ]

    for item in items:
        title = f"{item.ticker} - {item.name}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=_truncate(title, max_len=55),
                    callback_data=f"{CB_PREFIX_DETAILS}:{item.figi}",
                )
            ]
        )

    nav_row: list[InlineKeyboardButton] = []
    if page > 1:
        nav_row.append(InlineKeyboardButton(text="\u041d\u0430\u0437\u0430\u0434", callback_data=f"{CB_PREFIX_PAGE}:{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"\u0421\u0442\u0440. {page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton(text="\u0412\u043f\u0435\u0440\u0451\u0434", callback_data=f"{CB_PREFIX_PAGE}:{page + 1}"))
    rows.append(nav_row)

    refresh_text = "\u041e\u0431\u043d\u043e\u0432\u0438\u0442\u044c"
    if mode == "search" and has_query:
        refresh_text = "\u041e\u0431\u043d\u043e\u0432\u0438\u0442\u044c \u043f\u043e\u0438\u0441\u043a"
    elif mode == "favorites":
        refresh_text = "\u041e\u0431\u043d\u043e\u0432\u0438\u0442\u044c \u0438\u0437\u0431\u0440\u0430\u043d\u043d\u043e\u0435"
    rows.append([InlineKeyboardButton(text=refresh_text, callback_data=CB_REFRESH)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def share_details_keyboard(return_page: int, is_favorite: bool) -> InlineKeyboardMarkup:
    favorite_text = (
        "\u0412 \u0438\u0437\u0431\u0440\u0430\u043d\u043d\u043e\u0435"
        if not is_favorite
        else "\u0423\u0431\u0440\u0430\u0442\u044c \u0438\u0437 \u0438\u0437\u0431\u0440\u0430\u043d\u043d\u043e\u0433\u043e"
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=favorite_text, callback_data=f"{CB_FAVORITE_TOGGLE_PREFIX}:{int(is_favorite)}")],
            [InlineKeyboardButton(text="\u041d\u0430\u0437\u0430\u0434 \u043a \u0441\u043f\u0438\u0441\u043a\u0443", callback_data=f"{CB_BACK_TO_LIST}:{return_page}")],
            [InlineKeyboardButton(text="\u041e\u0431\u043d\u043e\u0432\u0438\u0442\u044c", callback_data=CB_REFRESH)],
        ]
    )


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return f"{text[: max_len - 1]}…"
