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

    # P0: 本地推理型模型（如 Qwen3.8-9B-GGUF 经 unsloth/llama.cpp 服务）默认把输出放 reasoning_content，
    # content 为空。置 True 时在请求体里带 chat_template_kwargs.enable_thinking=false 关闭推理，
    # 让结构化 JSON 直接落到 content，供 parse_structured_output 读取。默认 False（不破坏其它模型）。
    llm_disable_thinking: bool = False

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

    # ── P0 质量门 / 分级代审 配置（Q1-Q4 改造） ──────────────────
    # 依赖发现
    dependency_enabled: bool = True          # 是否启用数据驱动依赖发现
    dependency_min_confidence: float = 0.6   # 依赖边进入图的置信度阈值
    dependency_batch_size: int = 50          # 静态挖掘单批 API 数
    dependency_dynamic_probe: bool = True    # 用真实响应样本校验候选边字段路径(近似动态证据)
    # 质量门
    quality_gate_enabled: bool = True
    quality_gate_self_check: bool = True     # Gate1 语义自检
    quality_gate_vote: bool = False          # Gate2 多模型一致性（省成本默认关）
    quality_gate_vote_models: int = 2        # Gate2 参与模型数
    # 试跑
    trial_run_enabled: bool = True           # Gate3 试跑自我验证
    trial_allow_write: bool = False          # 是否允许试跑写操作 API（默认只读）
    trial_timeout_s: int = 60                # 单场景试跑超时
    # 分级代审
    auto_review_enabled: bool = False        # 总开关，灰度
    auto_review_min_confidence: float = 0.7  # 自动通过最低置信度
    auto_review_trial_required: bool = True  # 自动通过是否要求试跑通过
    auto_review_reject_threshold: float = 0.3  # 自动拒绝（坏产物）置信度上限
    # 规模化分片
    cluster_batch_max_apis: int = 30         # 单次场景生成最多 API 数（分片）
    cluster_max_workers: int = 2             # 集群 worker 并发上限

    @property
    def cors_origins_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
