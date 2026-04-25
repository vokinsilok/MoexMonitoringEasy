ADMIN_TELEGRAM_IDS: set[int] = {
    570843200,
    248280244,
}


def is_admin_user(telegram_user_id: int | None) -> bool:
    if telegram_user_id is None:
        return False
    return int(telegram_user_id) in ADMIN_TELEGRAM_IDS

