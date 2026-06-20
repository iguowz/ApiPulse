"""
MongoDB / Redis 连接管理（Motor + redis.asyncio）
提供 FastAPI 依赖注入使用的 get_db、get_redis 及 shutdown 时的 close_connections
"""
from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from redis.asyncio import Redis

from config.settings import get_settings

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None
_redis: Redis | None = None


async def get_db() -> AsyncIOMotorDatabase:
    global _client, _db
    if _db is None:
        s = get_settings()
        _client = AsyncIOMotorClient(s.mongo_uri)
        _db = _client[s.mongo_db]
    return _db


async def get_redis() -> Redis:
    global _redis
    if _redis is None:
        s = get_settings()
        _redis = Redis.from_url(s.redis_url, decode_responses=True)
    return _redis


async def close_connections():
    global _client, _db, _redis
    if _redis:
        # aclose() 正确排空待处理命令后关闭连接；close() 可能丢失未发送数据
        await _redis.aclose()
        _redis = None
    if _client:
        _client.close()
        _client = None
        _db = None
