from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "backend/.env"), extra="ignore")

    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db:  str = "api_quality"

    redis_url: str = "redis://localhost:6379/0"

    minio_endpoint:           str = "localhost:9000"
    minio_access_key:         str = "minioadmin"
    minio_secret_key:         str = "minioadmin"
    minio_bucket_quarantine:  str = "har-quarantine"

    openai_api_key:  str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model:    str = "gpt-4o-mini"
    openai_temperature: float = 0.1
    openai_max_tokens:  int   = 4096
    openai_timeout:     float = 120.0   # LLM 请求超时秒数（含 connect）

    # 按任务类型细粒度控制 max_tokens（不同任务输出长度不同）
    openai_max_tokens_doc:      int = 3000   # 文档生成
    openai_max_tokens_asserts:  int = 3000   # 断言生成
    openai_max_tokens_scenario: int = 4096   # 场景生成
    openai_max_tokens_diff_eval: int = 2048  # 差异评估（输出 JSON 较短）

    # P0-4: AI 流式输出开关。
    # 启用后 analyze/generate 任务通过 stream=True 逐 chunk 返回，WS 实时广播 ai_chunk 事件，
    # 前端打字机效果展示生成进度。设为 False 兼容不支持流式的本地模型（如部分 Ollama 版本）。
    llm_stream_enabled: bool = True

    # CORS：逗号分隔，生产环境应配置具体域名
    # 示例：CORS_ORIGINS=https://aqp.example.com,https://admin.example.com
    cors_origins: str = "*"

    # 本地大模型主机地址：Docker Desktop for Mac 需设为 host.docker.internal
    # 若在宿主机直接运行则用 localhost；Linux Docker 可用 172.17.0.1 或 host.docker.internal
    local_llm_host: str = "localhost"

    app_env:   str = "development"
    log_level: str = "INFO"

    # 速率限制：基于客户端 IP 的请求频率控制，防止 API 滥用
    rate_limit_enabled: bool = True       # 是否启用速率限制
    rate_limit_max_requests: int = 120    # 每个时间窗口内最大请求数
    rate_limit_window_s: int = 60         # 时间窗口大小（秒）

    # API Key 认证：非空时启用，所有请求需携带 X-API-Key 头
    # 开发环境默认不启用，生产环境应设置强随机字符串
    api_key: str = ""

    # JWT 密钥：用于签发和验证用户登录 token
    # 开发环境有默认值，生产环境应设置为强随机字符串
    jwt_secret: str = "apipulse-jwt-secret-change-in-production"
    # SQL 数据源密码加密密钥；生产环境必须配置强随机值，未配置时回退 jwt_secret。
    sql_secret_key: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
