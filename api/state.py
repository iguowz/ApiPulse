"""
共享状态模块 —— 从 routes.py 提取的全局变量和服务实例

所有路由模块通过导入本模块访问共享状态，启动时由 main app 初始化。
"""
from __future__ import annotations

import asyncio
from typing import Any

from config.settings import get_settings
from har_parser.quarantine import QuarantineStore

_settings = get_settings()

# ── 服务实例（启动时注入） ──────────────────────────────────
_quarantine = QuarantineStore()
_monitor_service: Any = None  # MonitorService | None
_ai_analyzer: Any = None      # AiAnalyzerService | None
_knowledge_service: Any = None  # KnowledgeService | None
_ai_worker_task: asyncio.Task | None = None
_diff_evaluator: Any = None       # DiffEvaluatorService | None
_diff_eval_task: asyncio.Task | None = None
_failure_diagnoser: Any = None    # FailureDiagnoserService | None
_diagnose_task: asyncio.Task | None = None
# P1-2: AI 告警分析服务
_alert_analyzer: Any = None       # AlertAnalyzerService | None
_alert_analyze_task: asyncio.Task | None = None

# ── 抓包状态（内存，单进程） ─────────────────────────────────
_capture_state: dict[str, Any] = {
    "enabled": False, "ingested_count": 0, "last_ingest_at": None,
    "filter_host": None, "filter_url": None,
}
_capture_lock = asyncio.Lock()

# ── WebSocket 管理器 ────────────────────────────────────────
from api.ws_manager import _ws  # noqa: E402


# ── LLM 模型预设 ────────────────────────────────────────────
def _build_llm_presets() -> list[dict[str, Any]]:
    """构建 LLM 模型预设列表（本地模型 base_url 由 settings.local_llm_host 动态构建）"""
    host = _settings.local_llm_host
    return [
        {
            "id": "openai",
            "name": "OpenAI",
            "base_url": "https://api.openai.com/v1",
            "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
        },
        {
            "id": "deepseek",
            "name": "DeepSeek",
            "base_url": "https://api.deepseek.com/v1",
            "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
        },
        {
            "id": "ollama",
            "name": "Ollama",
            "base_url": f"http://{host}:11434/v1",
            "models": ["llama3", "qwen2", "gemma2", "mistral"],
        },
        {
            "id": "lmstudio",
            "name": "LM Studio",
            "base_url": f"http://{host}:1234/v1",
            "models": ["local-model"],
        },
        {
            "id": "llamacpp",
            "name": "llama.cpp",
            "base_url": f"http://{host}:8080/v1",
            "models": ["local-model"],
        },
        {
            "id": "custom",
            "name": "自定义",
            "base_url": "",
            "models": [],
        },
    ]
