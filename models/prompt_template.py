"""
Prompt 模板模型 —— P1-6 Prompt 版本化管理

解决问题：此前所有 system prompt 硬编码为 Python 字符串常量（analyzer.py:41/68/107），
改 prompt 要改代码重启，无法在线 A/B 或回滚。

设计：
- task_type 标识任务类型（doc/asserts/scenario/data_template/monitor/diff_eval/diagnose/extract/alert）
- 同一 task_type 可有多个版本，仅一个 active=True
- 支持灰度：按 project_id 隔离不同版本（可选，默认全局）
- 兜底：DB 无记录时回退到代码内默认 prompt，保证可用性
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from pydantic import BaseModel, Field


class PromptTemplate(BaseModel):
    id: str = ""
    task_type: str            # doc / asserts / scenario / data_template / monitor / diff_eval / diagnose / extract / alert
    name: str = ""            # 版本名称（如 "v1-strict" / "v2-lenient"）
    content: str              # prompt 全文
    version: int = 1          # 版本号，自增
    active: bool = False      # 是否当前激活版本（同 task_type 仅一个 active）
    project_id: str = ""      # 空表示全局，非空表示项目级灰度
    description: str = ""     # 变更说明
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None))
    created_by: str = ""      # 创建人


# task_type 合法值（与 analyzer 的 prompt 常量一一对应）
PROMPT_TASK_TYPES: list[str] = [
    "doc",          # 文档生成
    "asserts",      # 断言生成
    "scenario",     # 场景生成
    "data_template",# 数据模板增强
    "monitor",      # 巡检配置生成
    "diff_eval",    # 差异评估
    "diagnose",     # 失败诊断
    "extract",      # 记忆提取
    "alert",        # 告警分析
]
