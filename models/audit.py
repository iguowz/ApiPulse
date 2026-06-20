"""
操作审计日志模型 —— 记录关键操作（创建/修改/删除/执行/导入），支持追溯
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from enum import Enum

from pydantic import BaseModel, Field


class AuditAction(str, Enum):
    """审计操作类型"""
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    BATCH_DELETE = "batch_delete"
    EXECUTE = "execute"
    BATCH_EXECUTE = "batch_execute"
    IMPORT = "import"
    EXPORT = "export"
    TOGGLE = "toggle"          # 开关（监控启用/禁用、抓包开关）
    ANALYZE = "analyze"        # AI 分析请求
    GENERATE = "generate"      # AI 生成场景
    EXTRACT = "extract"        # 知识提取（单个 API）
    BATCH_EXTRACT = "batch_extract"  # 批量知识提取


class AuditResource(str, Enum):
    """审计资源类型"""
    API = "api"
    SCENARIO = "scenario"
    MONITOR = "monitor"
    KNOWLEDGE = "knowledge"
    CAPTURE = "capture"
    HAR = "har"
    TEMPLATE = "template"      # 数据工厂模板
    DATASET = "dataset"        # 数据工厂沉淀数据集
    ENVIRONMENT = "environment"
    PROJECT = "project"
    USER = "user"
    ALERT_CHANNEL = "alert_channel"
    DIFF_ALERT = "diff_alert"       # 导入差异告警


class AuditLog(BaseModel):
    """操作审计日志文档，存入 MongoDB audit_logs 集合"""
    id: str = ""
    user_id: str = ""          # 操作人 ID（JWT sub）
    username: str = ""         # 操作人用户名
    action: AuditAction
    resource: AuditResource
    resource_id: str = ""      # 被操作资源 ID
    resource_name: str = ""    # 被操作资源名称（便于列表展示）
    detail: str = ""           # 简要描述，如 "创建场景 '登录流程测试'"
    ip: str = ""               # 客户端 IP
    project_id: str = "default"
    extra: dict | None = None  # 额外详情（如批量操作的 ID 数量）
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))
