"""
项目模型 —— 多项目隔离的基本单元
"""
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel, Field, model_validator


class Project(BaseModel):
    id: str = ""
    name: str
    slug: str = ""
    description: str = ""
    # 域名过滤：白名单非空时仅放行匹配域名，黑名单中的域名始终拒绝
    domain_allowlist: list[str] = []
    domain_blocklist: list[str] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))

    @model_validator(mode="after")
    def _auto_slug(self):
        """slug 为空时从 name 自动生成，避免前端遗漏 slug 导致存储空值"""
        if not self.slug and self.name:
            # 中文/特殊字符 → 拼音首字母或留空处理：降级为 name 的字母数字缩写
            slug = re.sub(r"[^a-zA-Z0-9]+", "_", self.name.lower()).strip("_")
            if not slug:
                slug = self.name[:20]
            self.slug = slug
        return self
