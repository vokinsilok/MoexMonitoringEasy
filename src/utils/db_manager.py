from contextlib import asynccontextmanager


class DBManager:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def __aenter__(self):
        self.session = self.session_factory()
        from src.moduls.auth.access_repository import AccessRepository
        from src.moduls.auth.auth_repository import AuthUserRepository
        from src.moduls.moex.moex_repository import MoexShareRepository

        self.access = AccessRepository(self.session)
        self.auth_user = AuthUserRepository(self.session)
        self.moex_share = MoexShareRepository(self.session)
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
