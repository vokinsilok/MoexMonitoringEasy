import redis.asyncio as redis


class RedisManager:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.redis = None

    async def connect(self):
        self.redis = redis.Redis(host=self.host, port=self.port)
        await self.redis.ping()

    async def set(self, key: str, value: str, expire: int = None):
        if self.redis is None:
            raise RuntimeError("Redis is not connected")
        if expire:
            await self.redis.set(key, value, ex=expire)
        else:
            await self.redis.set(key, value)

    async def get(self, key: str):
        if self.redis is None:
            raise RuntimeError("Redis is not connected")
        return await self.redis.get(key)

    async def delete(self, key: str):
        if self.redis is None:
            raise RuntimeError("Redis is not connected")
        await self.redis.delete(key)

    async def rpush(self, key: str, *values: str):
        if self.redis is None:
            raise RuntimeError("Redis is not connected")
        if not values:
            return 0
        return int(await self.redis.rpush(key, *values))

    async def close(self):
        if self.redis:
            await self.redis.aclose()
            self.redis = None

    async def clear(self):
        if self.redis is None:
            raise RuntimeError("Redis is not connected")
        await self.redis.flushdb()
