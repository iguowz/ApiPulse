"""
Bot 消息编排服务 —— Phase 3 Bot 集成。

接收来自企微/QQ/飞书 Webhook 的消息，复用 AI 对话核心逻辑，
处理消息路由、确认流、会话管理，并通过平台 API 发送回复。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, AsyncGenerator

import httpx
from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.database import get_redis
from config.settings import get_settings
from models.bot_config import BotConfig, BotPlatform

# Redis key 前缀
BOT_SESSION_PREFIX = "bot_session"     # bot_session:{platform}:{user_id}
BOT_PENDING_PREFIX = "bot_pending"     # bot_pending:{platform}:{user_id}
BOT_SESSION_TTL = 1800                  # 30 分钟
BOT_PENDING_TTL = 300                   # 5 分钟

# 确认/取消关键词
_CONFIRM_KEYWORDS = {"确认", "confirm", "yes", "是", "ok", "y", "执行"}
_CANCEL_KEYWORDS = {"取消", "cancel", "no", "否", "n", "不执行"}


class BotMessageService:
    """Bot 消息编排：接收消息 → 提取 AI 核心逻辑 → 聚合结果 → 发回平台"""

    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db

    async def process_message(
        self, bot_config: BotConfig, user_id: str, text: str,
    ) -> str | None:
        """处理一条 Bot 消息，返回回复文本（无回复时返回 None）。

        流程：
        1. 检查是否有待确认的 pending action → 消费之
        2. 新对话 → 调用 AI 核心逻辑
        3. 需要确认 → 存 pending 到 Redis
        4. 聚合结果 → 截断到 3000 字符
        """
        redis = await get_redis()
        text = text.strip()
        if not text:
            return None

        platform = bot_config.platform.value
        pending_key = f"{BOT_PENDING_PREFIX}:{platform}:{user_id}"

        # 步骤 1：检查是否消费 pending action
        if text in _CONFIRM_KEYWORDS or text in _CANCEL_KEYWORDS:
            pending_raw = await redis.get(pending_key)
            if pending_raw:
                pending = json.loads(pending_raw)
                await redis.delete(pending_key)
                if text in _CONFIRM_KEYWORDS:
                    return await self._execute_pending(bot_config, user_id, pending)
                else:
                    return f"已取消操作：{pending.get('summary', '未知操作')}"

        # 步骤 2：新对话 → 调用 AI 核心逻辑
        # 获取会话上下文
        session_key = f"{BOT_SESSION_PREFIX}:{platform}:{user_id}"
        session_raw = await redis.get(session_key)
        history: list[dict[str, Any]] = json.loads(session_raw) if session_raw else []

        # 构建虚拟用户信息（bypass JWT 但保留审计追踪）
        virtual_user = {
            "username": f"bot_{platform}:{user_id}",
            "role": "user",
            "project_id": bot_config.project_id,
        }

        # 调用 AI 对话核心逻辑（聚合结果）
        from api.routers.ai_chat import process_chat
        result_parts: list[str] = []
        pending_action: dict[str, Any] | None = None

        try:
            async for event in process_chat(
                db=self._db,
                user=virtual_user,
                user_id=virtual_user["username"],
                message=text,
                session_id=f"bot_{platform}_{user_id}",
                context={"project_id": bot_config.project_id},
                history=history,
            ):
                etype = event.get("type", "")
                if etype == "assistant_token":
                    result_parts.append(str(event.get("content", "")))
                elif etype == "confirm_required":
                    pending_action = event
                elif etype == "tool_result":
                    pass  # 工具结果已包含在 assistant_token 流中
        except Exception as exc:
            logger.error(f"Bot AI chat error: {exc}")
            return "抱歉，AI 处理出错，请稍后重试"

        text_result = "".join(result_parts).strip()

        # 步骤 3：需要确认 → 存 pending 到 Redis
        if pending_action:
            pending_data = {
                "action_id": pending_action.get("action_id", ""),
                "tool": pending_action.get("tool", ""),
                "summary": pending_action.get("summary", ""),
                "params": pending_action.get("params", {}),
                "project_id": bot_config.project_id,
            }
            await redis.setex(pending_key, BOT_PENDING_TTL, json.dumps(pending_data, ensure_ascii=False))
            confirm_hint = f"\n\n---\n回复「确认」执行此操作，回复「取消」放弃（5 分钟内有效）"
            text_result += confirm_hint

        if not text_result:
            return None

        # 步骤 4：截断到 3000 字符（IM 平台限制）
        if len(text_result) > 3000:
            text_result = text_result[:2997] + "..."

        # 保存会话历史
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": text_result})
        # 保留最近 20 轮
        history = history[-40:]
        await redis.setex(session_key, BOT_SESSION_TTL, json.dumps(history, ensure_ascii=False))

        return text_result

    async def _execute_pending(
        self, bot_config: BotConfig, user_id: str, pending: dict[str, Any],
    ) -> str:
        """执行已确认的 pending action"""
        action_id = pending.get("action_id", "")
        tool = pending.get("tool", "")
        params = pending.get("params", {})
        project_id = pending.get("project_id", bot_config.project_id)

        virtual_user = {
            "username": f"bot_{bot_config.platform.value}:{user_id}",
            "role": "user",
            "project_id": project_id,
        }

        try:
            from api.routers.ai_chat import _execute_write_tool, _pending_actions
            # 复用确认端点的执行逻辑
            if action_id and action_id in _pending_actions:
                stored = _pending_actions[action_id]
                result = await _execute_write_tool(
                    db=self._db,
                    user=virtual_user,
                    context={"project_id": project_id},
                    name=stored["tool"],
                    args=stored["args"],
                )
                del _pending_actions[action_id]
                if result.get("error"):
                    return f"操作失败：{result['error']}"
                return f"操作完成：{result.get('message', 'success')}"
            else:
                # pending 已过期
                return "该操作已过期（超过 5 分钟），请重新发起"
        except Exception as exc:
            logger.error(f"Bot pending execution error: {exc}")
            return f"操作执行出错：{exc}"

    async def send_reply(
        self, platform: BotPlatform, config: BotConfig, content: str,
    ) -> bool:
        """通过平台 API 发送回复消息"""
        if not content:
            return False
        try:
            if platform == BotPlatform.WECOM:
                return await self._send_wecom(config, content)
            elif platform == BotPlatform.FEISHU:
                return await self._send_feishu(config, content)
            elif platform == BotPlatform.QQ:
                return await self._send_qq(config, content)
        except Exception as exc:
            logger.error(f"Bot send_reply error ({platform}): {exc}")
            return False
        return False

    async def _send_wecom(self, config: BotConfig, content: str) -> bool:
        """企业微信群机器人 Webhook 发送消息"""
        webhook_url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={config.app_secret}"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json={
                "msgtype": "text",
                "text": {"content": content},
            })
            ok = resp.status_code == 200 and resp.json().get("errcode") == 0
            if not ok:
                logger.warning(f"WeCom send failed: {resp.text}")
            return ok

    async def _send_feishu(self, config: BotConfig, content: str) -> bool:
        """飞书自定义机器人 Webhook 发送消息"""
        webhook_url = f"https://open.feishu.cn/open-apis/bot/v2/hook/{config.app_secret}"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json={
                "msg_type": "text",
                "content": {"text": content},
            })
            ok = resp.status_code == 200 and resp.json().get("code") == 0
            if not ok:
                logger.warning(f"Feishu send failed: {resp.text}")
            return ok

    async def _send_qq(self, config: BotConfig, content: str) -> bool:
        """QQ 机器人发送消息（通过 QQ Bot API）"""
        if not config.app_secret:
            return False
        # QQ Bot 需要主动推送，群聊场景通常使用 Webhook 回调
        # 此处仅记录，实际实现需要 QQ Bot SDK
        logger.info(f"QQ Bot reply (not sent): {content[:100]}")
        return True
