"""
ReMe 记忆系统数据模型 —— 从 AI 分析结果中提取可复用知识。
独立于 dsl.py，通过 project_id 关联项目。
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from enum import Enum
from pydantic import BaseModel, Field


# ── 记忆类型枚举 ──────────────────────────────────────────

class KnowledgeType(str, Enum):
    FIELD_PATTERN = "field_pattern"          # 字段命名/语义约定
    ASSERTION_PATTERN = "assertion_pattern"  # 可复用断言规则
    DOC_PATTERN = "doc_pattern"              # 文档编写约定
    SCENARIO_PATTERN = "scenario_pattern"    # 场景流程模式


# ── 记忆条目模型 ──────────────────────────────────────────

class KnowledgeEntry(BaseModel):
    id: str = ""                                    # uuid4
    project_id: str = "default"
    type: KnowledgeType
    title: str                                      # 简短标题，如 "token 字段约定"
    content: str                                    # 详细内容，markdown 格式
    tags: list[str] = Field(default_factory=list)   # 检索标签
    source_api_ids: list[str] = Field(default_factory=list)  # 来源 API（合并时去重追加）
    source_hash: str = ""                           # (project_id + type + title) 的 md5，用于去重合并
    usage_count: int = 0                            # 被检索引用次数
    upvote_count: int = 0                           # 点赞数
    downvote_count: int = 0                         # 点踩数
    confidence: float = 0.5                         # 0.05~1.0，首次 0.5，合并递增，反馈微调
    updated_by: str = "system"                      # "system" | "user" → 审计追踪
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
    )


# ── 请求体模型 ────────────────────────────────────────────

class KnowledgeUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    tags: list[str] | None = None


class KnowledgeFeedback(BaseModel):
    action: str  # "upvote" | "downvote"
