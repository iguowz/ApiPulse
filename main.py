import os
import sys
from loguru import logger
from config.settings import get_settings

s = get_settings()

# 设置默认 extra 值：request_id 基线为 "--------"，中间件通过 contextualize 覆盖
logger.remove()
logger.configure(extra={"request_id": "--------"})
logger.add(
    sys.stdout,
    level=s.log_level,
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<yellow>{extra[request_id]}</yellow> | "
        "<cyan>{name}</cyan>:<cyan>{line}</cyan> | {message}"
    ),
    colorize=True,
)
# 确保日志目录存在，避免新部署时 FileNotFoundError
os.makedirs("logs", exist_ok=True)
logger.add(
    "logs/app.log",
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {extra[request_id]} | {name}:{line} | {message}",
    rotation="00:00",      # 每天午夜轮转，按日期打包
    retention="14 days",
    compression="zip",
    enqueue=True,   # 异步写，不阻塞事件循环
)

from api.routes import app  # noqa: E402

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=(s.app_env == "development"),
        log_config=None,   # 使用 loguru，禁用 uvicorn 默认日志
    )
