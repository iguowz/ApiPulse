"""
MemoryService —— 4 层记忆管理，基于 ReMe (agentscope-ai/ReMe) v0.3.1

将 ReMe 的 compaction / summarization / semantic-search 能力接入 ApiPulse，
按生命周期分为 4 层：
  L1 (长期记忆) : data/memory/long_term/  + MongoDB l1_memories，跨项目，永久保留
  L2 (项目记忆) : ReMe file-based + vector store，按 project_id 隔离，项目生命周期
  L3 (会话记忆) : ReMe 对话摘要 + MongoDB l3_memories，30 天保留
  L4 (对话记忆) : ReMe in-memory 上下文 + Redis，仅活跃会话期间

与现有 knowledge/service.py 互补：
  - knowledge/service.py 管理结构化 API 模式知识（字段约定、断言模式等）
  - memory_service 管理对话上下文记忆（摘要、压缩、语义检索）

注：ReMeLight v0.3.1.6 所有核心方法均为 async，无需 ThreadPoolExecutor。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase
from redis.asyncio import Redis

from config.settings import Settings

# ── 北京时区工具 ──────────────────────────────────────────────
def _beijing_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)

# ── dict ↔ agentscope.Msg 转换桥 ────────────────────────────
# ReMeLight 的 compact_memory / summary_memory / pre_reasoning_hook 等
# 均要求 agentscope.message.Msg 入参，而 ApiPulse 内部统一使用 dict。
# 此处提供双向转换，name 字段回退为 role。

def _dict_to_msg(d: dict[str, Any]) -> "Msg":
    from agentscope.message import Msg
    return Msg(
        name=d.get("name", d.get("role", "user")),
        role=d.get("role", "user"),
        content=d.get("content", "") or "",
    )

def _msg_to_dict(m: "Msg") -> dict[str, Any]:
    return {
        "role": getattr(m, "role", "user") or "user",
        "content": getattr(m, "content", "") or "",
    }

# ── Redis key 约定 ────────────────────────────────────────────

L4_PREFIX = "mem:l4"       # conversation-level: mem:l4:{user_id}:{session_id}


class MemoryService:
    """ReMe 封装：统一 4 层记忆的读写、压缩、检索。"""

    def __init__(self, db: AsyncIOMotorDatabase, redis: Redis, settings: Settings):
        self._db = db
        self._redis = redis
        self._settings = settings
        self._reme: Any = None               # ReMeLight 实例（start 后可用）
        self._working_dir = "data/memory"
        self._available: bool = False        # LLM 配置可用标志（无 key 时降级）

    # ── 生命周期 ──────────────────────────────────────────────

    async def start(self) -> None:
        """初始化 ReMeLight 与工作目录。

        LLM API key 未配置时 ReMe 仍可启动（vector search 不需要 LLM），
        但 compact/summarize 等需要 LLM 的方法会降级返回原值。
        """
        Path(self._working_dir).mkdir(parents=True, exist_ok=True)
        # 创建各层子目录
        for sub in ("long_term", "project", "sessions", "tool_results"):
            Path(self._working_dir, sub).mkdir(exist_ok=True)

        try:
            from reme.reme_light import ReMeLight
            # ReMeLight 内部 loguru.bind 缺少 request_id extra，补充默认值避免崩溃
            import loguru as _loguru
            _loguru.logger.configure(extra={"request_id": "--------"})

            self._reme = ReMeLight(
                working_dir=self._working_dir,
                llm_api_key=self._settings.openai_api_key or None,
                llm_base_url=self._settings.openai_base_url or None,
                enable_load_env=False,
            )
            await self._reme.start()
            self._available = bool(self._settings.openai_api_key)
            logger.info("MemoryService started (ReMeLight, llm_available={})", self._available)
        except Exception as e:
            logger.warning("MemoryService: ReMeLight init failed ({}), memory features degraded", e)
            self._available = False

    async def close(self) -> bool:
        """关闭 ReMeLight。"""
        if self._reme:
            try:
                ok = await self._reme.close()
                self._reme = None
                logger.info("MemoryService closed (ok={})", ok)
                return ok
            except Exception as e:
                logger.warning("MemoryService close error: {}", e)
                self._reme = None
        return True

    # ── L1 长期记忆（跨项目，永久）───────────────────────────

    async def get_l1(self, key: str) -> str | None:
        """读取 L1 长期记忆条目（按 key）。"""
        doc = await self._db["l1_memories"].find_one({"key": key})
        return doc.get("content") if doc else None

    async def set_l1(self, key: str, content: str, *, source: str = "system",
                     tags: list[str] | None = None) -> None:
        """写入/更新 L1 长期记忆条目。key 唯一，写入即覆盖。"""
        now = _beijing_now()
        doc = {
            "key": key, "content": content, "source": source,
            "tags": tags or [], "updated_at": now,
        }
        existing = await self._db["l1_memories"].find_one({"key": key})
        if existing:
            doc["created_at"] = existing.get("created_at", now)
            doc["confirmed_count"] = existing.get("confirmed_count", 0)
            await self._db["l1_memories"].replace_one({"key": key}, doc)
        else:
            doc["created_at"] = now
            doc["confirmed_count"] = 0
            await self._db["l1_memories"].insert_one(doc)

    async def list_l1(self, skip: int = 0, limit: int = 50, *, api_id: str | None = None) -> tuple[list[dict], int]:
        """分页列出 L1 记忆，可选按 api_id 过滤（匹配 tags 中的 api:UUID）。"""
        filt: dict[str, Any] = {}
        if api_id:
            filt["tags"] = f"api:{api_id}"
        total = await self._db["l1_memories"].count_documents(filt)
        cursor = self._db["l1_memories"].find(filt).sort("updated_at", -1).skip(skip).limit(limit)
        items = await cursor.to_list(limit)
        for item in items:
            item.pop("_id", None)
        return items, total

    async def delete_l1(self, key: str) -> bool:
        """删除 L1 长期记忆条目（按 key）。"""
        result = await self._db["l1_memories"].delete_one({"key": key})
        return result.deleted_count > 0

    async def promote_to_l1(self, key: str, content: str, *, source: str = "auto") -> None:
        """将 L2 确认超过阈值的条目提升为 L1。"""
        existing = await self._db["l1_memories"].find_one({"key": key})
        if existing:
            confirmed = existing.get("confirmed_count", 0) + 1
            await self._db["l1_memories"].update_one(
                {"key": key},
                {"$set": {"content": content, "confirmed_count": confirmed, "updated_at": _beijing_now()}},
            )
        else:
            await self.set_l1(key, content, source=source)

    # ── L2 项目记忆（按 project_id 隔离）────────────────────

    async def record_l2(self, project_id: str, entry_type: str, title: str, content: str,
                        *, tags: list[str] | None = None, source: str = "system") -> str:
        """写入 L2 项目记忆条目（MongoDB + 文件）。"""
        entry_id = str(uuid.uuid4())
        now = _beijing_now()
        doc = {
            "id": entry_id, "project_id": project_id, "type": entry_type,
            "title": title, "content": content, "tags": tags or [],
            "source": source, "confirmed_count": 0,
            "created_at": now, "updated_at": now,
        }
        await self._db["l2_memories"].insert_one(doc)
        # 写入项目记忆文件（供 ReMe memory_search 检索）
        project_file = Path(self._working_dir, "project", f"{project_id}.md")
        self._append_markdown_section(project_file, title, content, tags or [])
        return entry_id

    async def search_l2(self, project_id: str, query: str, limit: int = 10, *, api_id: str | None = None) -> list[dict]:
        """在 L2 项目记忆中检索（MongoDB 关键词过滤），可选按 api_id 过滤。
        获取项目下全部 L2 文档后做关键词评分，避免只取近期文档导致旧匹配条目被截断。"""
        filt: dict[str, Any] = {"project_id": project_id}
        if api_id:
            filt["tags"] = f"api:{api_id}"
        cursor = self._db["l2_memories"].find(filt).sort("updated_at", -1)
        docs = await cursor.to_list(length=None)
        for doc in docs:
            doc.pop("_id", None)
        if not query:
            return docs[:limit]

        query_lower = query.lower()
        scored: list[tuple[int, dict]] = []
        for doc in docs:
            score = 0
            if query_lower in doc.get("title", "").lower():
                score += 3
            if query_lower in doc.get("content", "").lower():
                score += 2
            for tag in doc.get("tags", []):
                if query_lower in tag.lower():
                    score += 1
            if score > 0:
                scored.append((score, doc))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [doc for _, doc in scored[:limit]]

    async def delete_l2(self, entry_id: str) -> bool:
        """删除 L2 项目记忆条目。"""
        result = await self._db["l2_memories"].delete_one({"id": entry_id})
        return result.deleted_count > 0

    async def confirm_l2(self, project_id: str, entry_id_or_title: str) -> int:
        """确认 L2 条目（用户 accept 时调用）；达阈值 3 自动提升到 L1。"""
        filt: dict[str, Any] = {"project_id": project_id}
        if entry_id_or_title and len(entry_id_or_title) == 36:
            filt["id"] = entry_id_or_title
        else:
            filt["title"] = entry_id_or_title

        result = await self._db["l2_memories"].find_one_and_update(
            filt,
            {"$inc": {"confirmed_count": 1}, "$set": {"updated_at": _beijing_now()}},
            return_document=True,
        )
        if not result:
            return 0
        count = result.get("confirmed_count", 0)
        if count >= 3:
            await self.promote_to_l1(
                key=f"project:{project_id}:{result.get('type', '')}:{result.get('title', '')}",
                content=result.get("content", ""), source="auto_promote",
            )
        return count

    # ── L3 会话记忆（按 session_id 隔离，30 天保留）─────────

    async def record_l3(self, session_id: str, project_id: str, summary: str,
                        *, user_id: str = "anonymous", tags: list[str] | None = None) -> None:
        """写入 L3 会话记忆条目（MongoDB + 文件）。"""
        now = _beijing_now()
        doc = {
            "id": str(uuid.uuid4()), "session_id": session_id,
            "project_id": project_id, "user_id": user_id,
            "summary": summary, "tags": tags or [],
            "created_at": now, "expires_at": now + timedelta(days=30),
        }
        await self._db["l3_memories"].insert_one(doc)
        session_file = Path(self._working_dir, "sessions", f"{session_id}.md")
        self._append_markdown_section(session_file, "会话摘要", summary, tags or [])

    async def search_l3(self, project_id: str, query: str, *,
                        user_id: str | None = None, api_id: str | None = None, limit: int = 10) -> list[dict]:
        """检索 L3 会话记忆（仅有效期内的条目），可选按 api_id 过滤。"""
        now = _beijing_now()
        filt: dict[str, Any] = {"project_id": project_id, "expires_at": {"$gte": now}}
        if user_id:
            filt["user_id"] = user_id
        if api_id:
            filt["tags"] = f"api:{api_id}"

        # 获取项目下全部有效 L3 文档后评分，避免只取近期文档导致旧匹配条目被截断
        cursor = self._db["l3_memories"].find(filt).sort("created_at", -1)
        docs = await cursor.to_list(length=None)
        for doc in docs:
            doc.pop("_id", None)
        if not query:
            return docs[:limit]

        query_lower = query.lower()
        scored: list[tuple[int, dict]] = []
        for doc in docs:
            score = 0
            if query_lower in doc.get("summary", "").lower():
                score += 3
            for tag in doc.get("tags", []):
                if query_lower in tag.lower():
                    score += 1
            if score > 0:
                scored.append((score, doc))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [doc for _, doc in scored[:limit]]

    # ── L4 对话记忆（Redis，仅活跃会话）─────────────────────

    async def get_l4(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
        """读取 L4 对话上下文（Redis JSON）。"""
        key = f"{L4_PREFIX}:{user_id}:{session_id}"
        raw = await self._redis.get(key)
        return json.loads(raw) if raw else []

    async def set_l4(self, user_id: str, session_id: str, messages: list[dict[str, Any]],
                     *, ttl: int = 86400) -> None:
        """写入 L4 对话上下文至 Redis（默认 TTL 24h，最多 40 条）。"""
        key = f"{L4_PREFIX}:{user_id}:{session_id}"
        payload = json.dumps(messages[-40:], ensure_ascii=False)
        await self._redis.setex(key, ttl, payload)

    async def delete_l4(self, user_id: str, session_id: str) -> bool:
        """删除 L4 对话上下文。"""
        key = f"{L4_PREFIX}:{user_id}:{session_id}"
        return await self._redis.delete(key) > 0

    async def extend_l4_ttl(self, user_id: str, session_id: str, ttl: int = 86400) -> bool:
        """延长 L4 过期时间（每次对话活跃时调用）。"""
        key = f"{L4_PREFIX}:{user_id}:{session_id}"
        if await self._redis.exists(key):
            await self._redis.expire(key, ttl)
            return True
        return False

    # ── ReMe 核心能力封装（均为 async，直接 await）──────────

    async def compact_context(
        self,
        messages: list[dict[str, Any]],
        *,
        previous_summary: str = "",
        max_input_length: float = 131072,
        compact_ratio: float = 0.7,
        language: str = "zh",
    ) -> str:
        """调用 ReMe compact_memory 压缩消息列表为结构化摘要。

        解决痛点：ai_chat.py:115 的 crude history[-N:] 截断丢弃早期上下文。
        """
        if not self._available or not self._reme or not messages:
            return previous_summary

        msgs = [_dict_to_msg(m) for m in messages if m.get("content")]
        if not msgs:
            return previous_summary

        try:
            result = await self._reme.compact_memory(
                messages=msgs,
                previous_summary=previous_summary,
                max_input_length=max_input_length,
                compact_ratio=compact_ratio,
                language=language,
            )
            if isinstance(result, dict):
                return result.get("summary", str(result))
            return result or previous_summary
        except Exception as e:
            logger.error("compact_context failed: {}", e)
            return previous_summary

    async def summarize_session(
        self,
        messages: list[dict[str, Any]],
        *,
        language: str = "zh",
        timezone_str: str | None = "Asia/Shanghai",
    ) -> str:
        """调用 ReMe summary_memory 生成 6 字段结构化会话摘要。"""
        if not self._available or not self._reme or not messages:
            return ""

        msgs = [_dict_to_msg(m) for m in messages if m.get("content")]
        if not msgs:
            return ""

        try:
            return await self._reme.summary_memory(
                messages=msgs, language=language, timezone=timezone_str,
            ) or ""
        except Exception as e:
            logger.error("summarize_session failed: {}", e)
            return ""

    async def semantic_search(
        self,
        query: str,
        *,
        max_results: int = 5,
        min_score: float = 0.1,
    ) -> list[dict[str, Any]]:
        """调用 ReMe memory_search 进行语义+BM25 混合检索。"""
        if not self._reme or not query:
            return []

        try:
            response = await self._reme.memory_search(
                query=query, max_results=max_results, min_score=min_score,
            )
            # ToolResponse → list[dict]
            content = getattr(response, "content", None)
            if content is None:
                return []
            if isinstance(content, str):
                try:
                    parsed = json.loads(content)
                    return parsed if isinstance(parsed, list) else [{"text": content}]
                except json.JSONDecodeError:
                    return [{"text": content}]
            if isinstance(content, list):
                return content
            return []
        except Exception as e:
            logger.error("semantic_search failed: {}", e)
            return []

    async def pre_reasoning(
        self,
        messages: list[dict[str, Any]],
        *,
        system_prompt: str = "",
        compressed_summary: str = "",
        language: str = "zh",
        max_input_length: float = 131072,
        compact_ratio: float = 0.7,
    ) -> tuple[list[dict[str, Any]], str]:
        """调用 ReMe pre_reasoning_hook 执行完整前置推理流水线。

        解决痛点：
        - ai_chat.py:115 的 crude 截断 → 用 compact_memory 智能压缩
        - 缺乏跨会话记忆 → memory_search 注入历史记忆
        """
        if not self._available or not self._reme or not messages:
            return messages, compressed_summary

        msgs = [_dict_to_msg(m) for m in messages if m.get("content")]
        if not msgs:
            return messages, compressed_summary

        try:
            result_msgs, summary = await self._reme.pre_reasoning_hook(
                messages=msgs,
                system_prompt=system_prompt,
                compressed_summary=compressed_summary,
                language=language,
                max_input_length=max_input_length,
                compact_ratio=compact_ratio,
            )
            result_dicts = [_msg_to_dict(m) for m in result_msgs]
            return result_dicts, summary or compressed_summary
        except Exception as e:
            logger.error("pre_reasoning failed: {}", e)
            return messages, compressed_summary

    async def compact_tool_results(
        self,
        messages: list[dict[str, Any]],
        *,
        old_max_bytes: int = 3000,
        recent_max_bytes: int = 102400,
    ) -> list[dict[str, Any]]:
        """调用 ReMe compact_tool_result 优化工具调用结果。

        近期结果保留完整（~100KB），旧结果自动截断至摘要（~3KB）。
        解决痛点：ai_chat.py:406 的 content[:12000] 硬截断。
        """
        if not self._available or not self._reme or not messages:
            return messages

        msgs = [_dict_to_msg(m) for m in messages]
        try:
            result_msgs = await self._reme.compact_tool_result(
                messages=msgs,
                old_max_bytes=old_max_bytes,
                recent_max_bytes=recent_max_bytes,
            )
            return [_msg_to_dict(m) for m in result_msgs]
        except Exception as e:
            logger.error("compact_tool_results failed: {}", e)
            return messages

    # ── 记忆检索聚合（跨 L1+L2+L3）───────────────────────────

    async def retrieve(
        self,
        project_id: str,
        query: str,
        *,
        user_id: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """聚合检索 L1+L2+L3 记忆 + ReMe 语义检索。"""
        results: dict[str, Any] = {"l1": [], "l2": [], "l3": [], "semantic": []}

        # L1 关键词匹配
        l1_docs = await self._db["l1_memories"].find({}).to_list(limit * 2)
        if query:
            ql = query.lower()
            results["l1"] = [
                {"key": d["key"], "content": d["content"], "tags": d.get("tags", [])}
                for d in l1_docs
                if ql in d.get("content", "").lower() or ql in d.get("key", "").lower()
            ][:limit]

        # L2 项目记忆
        results["l2"] = await self.search_l2(project_id, query, limit=limit)

        # L3 会话记忆
        results["l3"] = await self.search_l3(project_id, query, user_id=user_id, limit=limit)

        # ReMe 语义检索
        results["semantic"] = await self.semantic_search(query, max_results=limit)

        return results

    # ── 会话生命周期 ──────────────────────────────────────────

    async def end_session(self, user_id: str, session_id: str, project_id: str) -> str:
        """关闭会话：L4 → summarise → L3 → 清理 L4。"""
        messages = await self.get_l4(user_id, session_id)
        if not messages:
            await self.delete_l4(user_id, session_id)
            return ""

        summary = await self.summarize_session(messages)
        if summary:
            await self.record_l3(session_id, project_id, summary, user_id=user_id)
        await self.delete_l4(user_id, session_id)
        return summary

    async def save_session_to_l3(
        self, user_id: str, session_id: str, project_id: str, messages: list[dict[str, Any]],
    ) -> str:
        """保存会话快照到 L3 但不删除 L4，用于 AI 对话过程中持续记录。

        优先使用 ReMe 生成结构化摘要，不可用时回退为提取首条用户消息作为标题。
        每次调用会更新同 session_id 的已有 L3 记录（upsert），避免重复条目。
        """
        if not messages:
            return ""

        # 1. 尝试 ReMe 摘要
        summary = await self.summarize_session(messages)

        # 2. 回退：取首条用户消息截断
        if not summary:
            for m in messages:
                if m.get("role") == "user" and m.get("content"):
                    summary = m["content"][:200]
                    break
            if not summary:
                summary = "(empty conversation)"

        # 3. Upsert L3：同一 session_id + project_id 只保留最新一条，避免跨项目冲突
        existing = await self._db["l3_memories"].find_one({"session_id": session_id, "project_id": project_id})
        if existing:
            # 更新摘要时会话仍活跃 → 重置 expires_at 为 30 天后，避免活跃会话因 TTL 过期被清理
            await self._db["l3_memories"].update_one(
                {"session_id": session_id, "project_id": project_id},
                {"$set": {"summary": summary, "updated_at": _beijing_now(), "expires_at": _beijing_now() + timedelta(days=30)}},
            )
        else:
            await self.record_l3(session_id, project_id, summary, user_id=user_id)

        return summary

    # ── 内部工具 ──────────────────────────────────────────────

    @staticmethod
    def _append_markdown_section(filepath: Path, title: str, content: str, tags: list[str]) -> None:
        """追加 Markdown 章节到文件。"""
        exists = filepath.exists()
        tag_line = f"tags: [{', '.join(tags)}]" if tags else ""
        now = _beijing_now().strftime("%Y-%m-%d %H:%M")
        section = f"\n---\n## {title}\n*{now}*  {tag_line}\n\n{content}\n"
        try:
            with open(filepath, "a", encoding="utf-8") as f:
                if not exists:
                    f.write(f"# Memory File\n*Created: {now}*\n")
                f.write(section)
        except OSError as e:
            logger.warning("Failed to write memory file {}: {}", filepath, e)
