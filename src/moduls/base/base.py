from typing import List

from pydantic import BaseModel
from sqlalchemy import select, insert, update, delete


class BaseRepository:
    model = None
    schema = None

    def __init__(self, session):
        self.session = session

    async def get(self, id):
        result_stmt = select(self.model).where(self.model.id == id)
        result = await self.session.execute(result_stmt)
        return result.scalar()

    async def get_all(self):
        result_stmt = select(self.model)
        result = await self.session.execute(result_stmt)
        return result.unique().scalars().all()

    async def get_by_filter(self, **filter_by):
        result_stmt = select(self.model).filter_by(**filter_by)
        result = await self.session.execute(result_stmt)
        return result.scalar()

    async def get_by_steam_ids(self, steam_ids: list):
        stmt = select(self.model).where(self.model.steam_id.in_(steam_ids))
        result = await self.session.execute(stmt)
        return result.scalars().unique().all()

    async def get_many_by_filter(self, **filter_by):
        result_stmt = select(self.model).filter_by(**filter_by)
        result = await self.session.execute(result_stmt)
        return result.unique().scalars().all()

    async def create(self, obj: BaseModel):
        new_obj = insert(self.model).values(**obj.model_dump()).returning(self.model)
        result = await self.session.execute(new_obj)
        await self.session.commit()
        return result.scalar()

    async def put(self, data: BaseModel, **filter_by):
        update_stmt = (
            update(self.model)
            .filter_by(**filter_by)
            .values(**data.model_dump())
            .returning(self.model)
        )
        update_obj = await self.session.execute(update_stmt)
        obj = update_obj.scalar()
        await self.session.commit()
        return obj

    async def put_many(self, data: List[BaseModel]):
        update_stmt = []
        for i, obj in enumerate(data, start=1):
            update_stmt.append(
                update(self.model).filter_by(id=i).values(**obj.model_dump()).returning(self.model)
            )
        update_obj = await self.session.execute(update_stmt)
        obj = update_obj.scalar()
        return obj

    async def patch(self, data: BaseModel, **filter_by):
        update_data = {k: v for k, v in data.dict(exclude_unset=True).items() if v is not None}
        update_stmt = (
            update(self.model).filter_by(**filter_by).values(**update_data).returning(self.model)
        )
        update_obj = await self.session.execute(update_stmt)
        await self.session.commit()
        return update_obj.scalar()

    async def delete_obj(self, **filter_by):
        delete_stmt = delete(self.model).filter_by(**filter_by).returning(self.model)
        delete_obj = await self.session.execute(delete_stmt)
        await self.session.commit()
        return delete_obj.scalar()
