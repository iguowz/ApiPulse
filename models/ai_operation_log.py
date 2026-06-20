"""
AI 操作日志模型 —— 记录 AI 分析/场景生成操作的成败详情，支持追溯
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AiOperationType(str, Enum):
    """AI 操作类型"""
    ANALYZE = "analyze"                  # AI 文档+断言分析
    ANALYZE_DOC = "analyze_doc"          # 仅 AI 文档分析
    ANALYZE_ASSERTS = "analyze_asserts"  # 仅 AI 断言分析
    SCENARIO = "scenario"                # AI 场景生成


class AiOperationStatus(str, Enum):
    """AI 操作状态"""
    SUCCESS = "success"
    FAILED = "failed"


class AiOperationLog(BaseModel):
    """AI 操作日志文档，存入 MongoDB ai_operation_logs 集合"""
    id: str = ""
    type: AiOperationType
    status: AiOperationStatus
    api_ids: list[str] = Field(default_factory=list)       # 涉及的 API ID 列表
    scenario_ids: list[str] = Field(default_factory=list)   # 生成的场景 ID（仅成功时可能有值）
    message: str = ""                  # 人类可读摘要
    error: str = ""                    # 失败详情
    model: str = ""                    # 实际使用模型，便于排查模型差异导致的输出波动
    base_url: str = ""                 # LLM 服务地址，用于区分云端/本地模型
    duration_ms: int = 0               # 操作总耗时，定位慢请求/本地模型性能问题
    llm_calls: int = 0                 # 本次操作触发的 LLM 调用次数
    input_chars: int = 0               # 输入字符数，粗略定位 prompt 过大问题
    output_chars: int = 0              # 输出字符数，粗略定位输出截断/异常变短问题
    estimated_input_tokens: int = 0    # 估算输入 token，真实 usage 不可用时仍可做成本趋势
    estimated_output_tokens: int = 0   # 估算输出 token
    extra: dict[str, Any] = Field(default_factory=dict)     # 场景类型、force、子任务耗时等扩展信息
    project_id: str = "default"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))
