"""
GenerationVersion 数据模型 —— AI 生成内容的版本化存储。

Phase 1 核心变更：AI 分析器不再直接覆盖 ApiDSL/ScenarioDSL，
而是创建 GenerationVersion (status=pending_review)，由用户审核后
通过 accept/accept_partial/reject 决定是否应用到目标 DSL。
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class GenerationSource(str, Enum):
    """AI 生成来源，用于追溯生成入口和审核上下文。"""
    ANALYZER = "analyzer"
    AI_CHAT = "ai_chat"
    DIFF_EVALUATOR = "diff_evaluator"
    DATA_FACTORY = "data_factory"
    MANUAL_EDIT = "manual_edit"


class GenerationType(str, Enum):
    """AI 生成内容类型"""
    DOC = "doc"            # API 文档 (ApiDoc)
    ASSERTS = "asserts"    # 断言规则 (list[AssertRule])
    SCENARIO = "scenario"  # 场景用例 (ScenarioDSL)
    DATA_TEMPLATE = "data_template"  # P1-1: 数据模板 (DataTemplate)，AI 增强字段推断结果
    MONITOR = "monitor"    # 巡检监控配置 (MonitorDSL)，AI 生成后待审核落库
    CHAT_SUGGESTION = "chat_suggestion"  # AI Chat 无法解析为标准DSL时的降级类型，存用户可见建议文本


class GenerationStatus(str, Enum):
    """审核状态流转：pending_review → accepted | partially_accepted | rejected"""
    PENDING_REVIEW = "pending_review"          # 待人工审核
    ACCEPTED = "accepted"                      # 已接受，已应用到目标 DSL
    PARTIALLY_ACCEPTED = "partially_accepted"  # 部分接受（仅应用了部分字段）
    REJECTED = "rejected"                      # 已拒绝（记录原因，隐式反馈写入 knowledge）


class GenerationVersion(BaseModel):
    """AI 生成内容的版本记录 —— 每次 LLM 调用产出对应一条记录"""
    id: str = ""                                            # MongoDB ObjectId 字符串
    api_id: str                                             # 关联的 API ID（场景生成时可为空，通过 api_ids 关联）
    type: GenerationType                                    # 生成内容类型
    status: GenerationStatus = GenerationStatus.PENDING_REVIEW  # 审核状态
    content: dict[str, Any] = Field(default_factory=dict)   # AI 生成的完整内容快照（文档/断言/场景的 model_dump）
    summary: str = ""                                       # AI 生成摘要（前端列表展示用，~100字）
    model: str = ""                                         # 使用的 LLM 模型名
    latency_ms: int = 0                                     # LLM 调用耗时（毫秒）
    input_tokens: int = 0                                   # 输入 token 估算
    output_tokens: int = 0                                  # 输出 token 估算
    prompt: str = ""                                        # LLM 调用时的 user prompt（调试追溯用）
    api_ids: list[str] = Field(default_factory=list)        # 场景生成时关联的多个 API ID
    project_id: str = "default"                             # 所属项目
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))
    reviewed_at: datetime | None = None                     # 审核时间
    reviewer_id: str | None = None                          # 审核人用户名
    review_feedback: str | None = None                      # 审核反馈（拒绝原因 / 部分接受说明）
    source: GenerationSource = GenerationSource.ANALYZER    # 生成来源
    job_id: str = ""                                        # 长任务/队列任务 ID，用于追踪进度
