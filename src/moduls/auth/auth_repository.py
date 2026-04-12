from sqlalchemy import func, select

from src.moduls.auth.auth_user import AuthUser
from src.moduls.base.base import BaseRepository


class AuthUserRepository(BaseRepository):
    model = AuthUser

    async def get_by_username(self, username: str) -> AuthUser | None:
        stmt = select(self.model).where(func.lower(self.model.username) == username.lower())
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def count_users(self) -> int:
        stmt = select(func.count(self.model.id))
        result = await self.session.execute(stmt)
        return int(result.scalar_one())
