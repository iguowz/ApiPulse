"""
数据库重置脚本 —— 清除 MongoDB 数据库和 Redis 数据，用于开发环境重置。

用法:
    cd <project_root>
    python scripts/reset_db.py

执行后重启服务，api/routes.py 的 startup 事件会自动重建索引、默认管理员和默认项目。
"""

from __future__ import annotations

import asyncio
import sys
import os

# 将项目根目录加入 Python 路径，确保 config 模块可导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motor.motor_asyncio import AsyncIOMotorClient
import redis.asyncio as aioredis
from config.settings import get_settings


async def reset() -> None:
    settings = get_settings()

    # ── 重置 MongoDB ──
    print(f"[MongoDB] 连接 {settings.mongo_uri} ...")
    mongo_client = AsyncIOMotorClient(settings.mongo_uri)
    db_name = settings.mongo_db
    await mongo_client.drop_database(db_name)
    print(f"[MongoDB] 已删除数据库 '{db_name}'")

    # ── 重置 Redis ──
    print(f"[Redis] 连接 {settings.redis_url} ...")
    redis_client = aioredis.from_url(settings.redis_url)
    await redis_client.flushdb()
    print("[Redis] 已清空当前数据库")
    await redis_client.close()

    print("\n✓ 数据库重置完成。重启服务后将自动重建索引、默认管理员和默认项目。")


if __name__ == "__main__":
    asyncio.run(reset())
