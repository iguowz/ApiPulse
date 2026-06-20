"""
BotConfig 数据模型 —— 企业微信/QQ/飞书机器人接入配置。

Phase 3: Bot 集成，支持将 AI 助手接入第三方 IM 平台。
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class BotPlatform(str, Enum):
    """支持的 IM 平台"""
    WECOM = "wecom"    # 企业微信
    QQ = "qq"          # QQ
    FEISHU = "feishu"  # 飞书


class BotConfig(BaseModel):
    """机器人接入配置"""
    id: str = ""
    project_id: str = Field(default="default", description="关联项目 ID")
    platform: BotPlatform = Field(description="平台类型：wecom/qq/feishu")
    name: str = Field(default="", description="机器人名称")
    enabled: bool = Field(default=True, description="是否启用")

    # 平台通用配置
    verify_token: str = Field(default="", description="签名验证 Token")
    app_secret: str = Field(default="", description="飞书/QQ 的 App Secret")

    # 企业微信专属
    encoding_aes_key: str = Field(default="", description="企微消息加解密密钥（43位）")
    corp_id: str = Field(default="", description="企微 CorpID")

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
    )
