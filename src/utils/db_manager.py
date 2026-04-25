from contextlib import asynccontextmanager


class DBManager:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def __aenter__(self):
        self.session = self.session_factory()
        from src.moduls.tbank.repository import (
            TBankBotAccessUserRepository,
            TBankFavoriteShareRepository,
            TBankPriceMonitorRepository,
            TBankShareRepository,
            TBankUserCredentialRepository,
        )

        self.tbank_share = TBankShareRepository(self.session)
        self.tbank_user_credential = TBankUserCredentialRepository(self.session)
        self.tbank_favorite_share = TBankFavoriteShareRepository(self.session)
        self.tbank_bot_access_user = TBankBotAccessUserRepository(self.session)
        self.tbank_price_monitor = TBankPriceMonitorRepository(self.session)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            await self.session.rollback()
        await self.session.close()

    async def commit_db(self):
        await self.session.commit()

    @asynccontextmanager
    async def transaction(self):
        """Контекстный менеджер для атомарных транзакций.

        Пример использования:
        ```python
        async with db.transaction():
            # Все операции в этом блоке будут выполнены атомарно
            await db.player.update_player(player_data)
            await db.fraction.update_fraction(fraction_data)
        ```

        Если внутри блока возникнет исключение, все изменения будут отменены.
        Если блок выполнится успешно, изменения будут зафиксированы.
        """
        try:
            yield
            await self.session.commit()
        except Exception as e:
            await self.session.rollback()
            raise e
