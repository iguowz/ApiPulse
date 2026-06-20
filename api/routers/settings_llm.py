"""
Settings / LLM 配置路由
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.database import get_db
from config.settings import get_settings
from api.state import _build_llm_presets
from services.llm_config_service import (
    TASK_ROUTE_TYPES,
    mask_api_key,
    sanitize_task_routes,
    serialize_llm_config,
)
# 使用 import api.state 而非 from import，确保读取到 startup() 后注入的实例
import api.state as _state

router = APIRouter(tags=["Settings"])


@router.get("/settings/llm")
async def get_llm_config(db: AsyncIOMotorDatabase = Depends(get_db)):
    """获取 LLM 配置（DB 优先，fallback .env）以及模型预设列表"""
    db_config = await db["settings"].find_one({"key": "llm_config"})
    s = get_settings()
    current = serialize_llm_config(db_config)
    return {
        "config": current,
        "presets": _build_llm_presets(),
        "task_types": TASK_ROUTE_TYPES,
        "configured": bool(db_config and (db_config.get("api_key") or db_config.get("base_url"))) or bool(s.openai_api_key),
    }


@router.put("/settings/llm")
async def update_llm_config(
    data: dict[str, Any] = Body(...),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """保存 LLM 配置到 MongoDB 并刷新运行时客户端"""
    api_key = data.get("api_key", "").strip()
    base_url = data.get("base_url", "").strip()
    model = data.get("model", "gpt-4o-mini").strip()
    provider = data.get("provider", "custom").strip()
    temperature = float(data.get("temperature", get_settings().openai_temperature))
    max_tokens = int(data.get("max_tokens", get_settings().openai_max_tokens))
    stream = bool(data.get("stream", get_settings().llm_stream_enabled))
    task_routes = sanitize_task_routes(data.get("task_routes"))

    # 本地大模型(Ollama/LM Studio/llama.cpp)不需要 api_key
    local_presets = {"ollama", "lmstudio", "llamacpp"}
    if not api_key and provider not in local_presets:
        raise HTTPException(400, "api_key is required")
    if not base_url:
        raise HTTPException(400, "base_url is required")

    config_doc = {
        "key": "llm_config",
        "provider": provider,
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
        "task_routes": task_routes,
        "updated_at": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None).isoformat(),
    }
    await db["settings"].update_one(
        {"key": "llm_config"},
        {"$set": config_doc},
        upsert=True,
    )

    # 通知运行时所有 LLM worker 刷新客户端；单个失败不影响配置保存。
    for service_name in ("_ai_analyzer", "_diff_evaluator", "_failure_diagnoser", "_alert_analyzer"):
        service = getattr(_state, service_name, None)
        if service and hasattr(service, "refresh_client"):
            try:
                await service.refresh_client()
            except Exception as e:
                logger.warning("Failed to refresh {} LLM client: {}", service_name, e)

    # 脱敏返回
    return {
        "status": "saved",
        "provider": provider,
        "model": model,
        "api_key": mask_api_key(api_key),
        "task_routes": list(task_routes.keys()),
    }


@router.post("/settings/llm/test")
async def test_llm_connection(
    data: dict[str, Any] = Body(...),
):
    """测试 LLM 连接：用给定配置发送简短请求，验证 key / base_url / model 是否可用"""
    api_key = data.get("api_key", "").strip()
    base_url = data.get("base_url", "").strip()
    model = data.get("model", "gpt-4o-mini").strip()
    provider = data.get("provider", "custom").strip()

    # 本地大模型(Ollama/LM Studio/llama.cpp)不需要 api_key
    local_presets = {"ollama", "lmstudio", "llamacpp"}
    if not api_key and provider not in local_presets:
        raise HTTPException(400, "api_key is required")
    if not base_url:
        raise HTTPException(400, "base_url is required")

    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key or "not-needed", base_url=base_url)
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "回复 OK"}],
            temperature=0,
            max_tokens=10,
            timeout=15,
        )
        content = resp.choices[0].message.content if resp.choices else ""
        return {
            "success": True,
            "message": f"连接成功，模型回复: {content[:50]}",
            "model": model,
        }
    except Exception as e:
        err_msg = str(e)
        if len(err_msg) > 300:
            err_msg = err_msg[:300] + "…"
        return {
            "success": False,
            "message": err_msg,
            "model": model,
        }


@router.post("/settings/llm/models")
async def discover_llm_models(
    data: dict[str, Any] = Body(...),
):
    """模型发现：调用 base_url/v1/models 获取可用模型列表，支持本地大模型服务"""
    base_url = data.get("base_url", "").strip()
    api_key = data.get("api_key", "").strip()

    if not base_url:
        raise HTTPException(400, "base_url is required")

    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key or "not-needed", base_url=base_url)
    try:
        models = await client.models.list(timeout=10)
        model_ids = sorted([m.id for m in models.data])
        return {"success": True, "models": model_ids, "base_url": base_url}
    except Exception as e:
        return {"success": False, "message": str(e)[:300], "base_url": base_url}
