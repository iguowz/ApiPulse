"""
LLM 运行时配置解析。

支持全局 llm_config + task_routes 覆盖；没有任务级配置时保持现有全局/.env 行为。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from openai import AsyncOpenAI

from config.settings import get_settings


TASK_ROUTE_TYPES = [
    "doc",
    "asserts",
    "scenario",
    "data_template",
    "monitor",
    "diff",
    "diagnose",
    "chat",
    "alert",
]

TASK_TYPE_ALIASES = {
    "ai_analyze_doc": "doc",
    "ai_analyze_asserts": "asserts",
    "diff_eval": "diff",
    "diff_evaluate": "diff",
    "diagnose_failure": "diagnose",
    "alert_analyze": "alert",
}


@dataclass
class LlmRuntimeConfig:
    provider: str
    api_key: str
    base_url: str
    model: str
    temperature: float
    max_tokens: int
    stream: bool
    task_type: str = ""

    def build_client(self) -> AsyncOpenAI:
        s = get_settings()
        return AsyncOpenAI(
            api_key=self.api_key or "not-needed",
            base_url=self.base_url,
            timeout=httpx.Timeout(s.openai_timeout, connect=10.0),
        )


def normalize_task_type(task_type: str) -> str:
    raw = (task_type or "").strip()
    return TASK_TYPE_ALIASES.get(raw, raw)


def _default_max_tokens(task_type: str) -> int:
    s = get_settings()
    defaults = {
        "doc": s.openai_max_tokens_doc,
        "asserts": s.openai_max_tokens_asserts,
        "scenario": s.openai_max_tokens_scenario,
        "data_template": s.openai_max_tokens_asserts,
        "monitor": s.openai_max_tokens_scenario,
        "diff": s.openai_max_tokens_diff_eval,
        "diagnose": s.openai_max_tokens,
        "chat": s.openai_max_tokens,
        "alert": 1024,
    }
    return int(defaults.get(task_type, s.openai_max_tokens))


def _has_runtime_config(config: dict[str, Any]) -> bool:
    return bool(config.get("api_key") or config.get("base_url"))


def _task_routes(config: dict[str, Any]) -> dict[str, Any]:
    routes = config.get("task_routes") or {}
    return routes if isinstance(routes, dict) else {}


def resolve_llm_config(db_config: dict[str, Any] | None = None, task_type: str = "") -> LlmRuntimeConfig:
    """解析任务级 LLM 配置；任务未配置时沿用全局 DB/.env。"""
    s = get_settings()
    db_config = db_config or {}
    task_type = normalize_task_type(task_type)
    use_db = _has_runtime_config(db_config)
    base_provider = db_config.get("provider", "custom") if use_db else "openai"
    base_api_key = db_config.get("api_key", "") if use_db else s.openai_api_key
    base_url = db_config.get("base_url", s.openai_base_url) if use_db else s.openai_base_url
    base_model = db_config.get("model", s.openai_model) if use_db else s.openai_model
    base_temperature = db_config.get("temperature", s.openai_temperature) if use_db else s.openai_temperature
    base_max_tokens = db_config.get("max_tokens", _default_max_tokens(task_type)) if use_db else _default_max_tokens(task_type)
    base_stream = db_config.get("stream", s.llm_stream_enabled) if use_db else s.llm_stream_enabled

    route = _task_routes(db_config).get(task_type) if task_type else None
    route = route if isinstance(route, dict) and route.get("enabled", True) else {}
    provider = route.get("provider", base_provider)
    api_key = route.get("api_key", base_api_key)
    return LlmRuntimeConfig(
        provider=provider,
        api_key=api_key,
        base_url=route.get("base_url", base_url),
        model=route.get("model", base_model),
        temperature=float(route.get("temperature", base_temperature)),
        max_tokens=int(route.get("max_tokens", base_max_tokens)),
        stream=bool(route.get("stream", base_stream)),
        task_type=task_type,
    )


def serialize_llm_config(db_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """给设置页返回脱敏后的全局配置与任务路由。"""
    db_config = db_config or {}
    global_cfg = resolve_llm_config(db_config)
    routes: dict[str, Any] = {}
    for task_type in TASK_ROUTE_TYPES:
        route = (_task_routes(db_config).get(task_type) or {})
        if not isinstance(route, dict):
            route = {}
        resolved = resolve_llm_config(db_config, task_type)
        routes[task_type] = {
            "enabled": bool(route.get("enabled", False)),
            "provider": route.get("provider", ""),
            "base_url": route.get("base_url", ""),
            "model": route.get("model", ""),
            "temperature": route.get("temperature", ""),
            "max_tokens": route.get("max_tokens", ""),
            "stream": route.get("stream", ""),
            "effective": {
                "provider": resolved.provider,
                "base_url": resolved.base_url,
                "model": resolved.model,
                "temperature": resolved.temperature,
                "max_tokens": resolved.max_tokens,
                "stream": resolved.stream,
            },
        }
    return {
        "provider": global_cfg.provider,
        "api_key": mask_api_key(global_cfg.api_key),
        "base_url": global_cfg.base_url,
        "model": global_cfg.model,
        "temperature": global_cfg.temperature,
        "max_tokens": global_cfg.max_tokens,
        "stream": global_cfg.stream,
        "task_routes": routes,
    }


def mask_api_key(api_key: str) -> str:
    if not api_key:
        return ""
    if len(api_key) > 8:
        return api_key[:4] + "****" + api_key[-4:]
    return "****"


def sanitize_task_routes(raw_routes: Any) -> dict[str, Any]:
    """保存前清洗任务路由，避免未知 task_type 或空配置写入。"""
    if not isinstance(raw_routes, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for task_type in TASK_ROUTE_TYPES:
        route = raw_routes.get(task_type) or {}
        if not isinstance(route, dict) or not route.get("enabled"):
            continue
        item: dict[str, Any] = {"enabled": True}
        for key in ("provider", "api_key", "base_url", "model"):
            value = str(route.get(key) or "").strip()
            if value:
                item[key] = value
        if route.get("temperature") not in (None, ""):
            item["temperature"] = float(route["temperature"])
        if route.get("max_tokens") not in (None, ""):
            item["max_tokens"] = int(route["max_tokens"])
        if route.get("stream") not in (None, ""):
            item["stream"] = bool(route["stream"])
        if any(k in item for k in ("provider", "api_key", "base_url", "model", "temperature", "max_tokens", "stream")):
            cleaned[task_type] = item
    return cleaned
