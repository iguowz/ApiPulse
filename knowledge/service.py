"""
ReMe 记忆系统服务层
- 多维检索评分（关键词/标签/路径/字段）
- Prompt 预算控制（max 6 条, ~500 tokens）
- LLM 记忆提取与去重合并
- 用户反馈置信度调整
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Callable

from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase

from models.knowledge import KnowledgeEntry, KnowledgeType

# ── 记忆提取 Prompt ───────────────────────────────────────

_EXTRACT_SYSTEM = """\
你是 API 知识管理专家。根据已完成的 API 文档和断言规则，提取可复用的模式知识。

提取类型：
- field_pattern: 字段命名/语义约定（如 token→JWT认证头、code→业务状态码）
- assertion_pattern: 可复用断言规则（如"列表接口必须断言$.data.total"）
- doc_pattern: 文档编写约定（如"RESTful删除接口响应为{code,message}"）
- scenario_pattern: 场景流程模式（如"CRUD标准流程:创建→查询→更新→删除"）

每项输出格式：{"type":"...", "title":"简短标题", "content":"详细说明", "tags":["关键词"]}
仅输出 JSON 数组，不要 markdown 围栏。"""

_EXTRACT_USER = """\
项目={project_id}
路径={method} {path}
文档={summary}
参数={params}
响应字段={response_fields}
断言={asserts}"""


class KnowledgeService:
    def __init__(self, db: AsyncIOMotorDatabase, ws_manager=None):
        self._db = db
        self._col = db["knowledge_entries"]
        self._ws = ws_manager

    # ── CRUD ──────────────────────────────────────────────

    async def list_entries(
        self, project_id: str = "default", type: str | None = None,
        search: str | None = None, skip: int = 0, limit: int = 30,
    ) -> tuple[list[dict], int]:
        """分页列出知识条目，支持类型筛选和关键词搜索"""
        filt: dict[str, Any] = {"project_id": project_id}
        if type and type in [t.value for t in KnowledgeType]:
            # 类型筛选仅接受合法的 KnowledgeType 枚举值，无效类型忽略（等价于不过滤）
            filt["type"] = type
        if search:
            # 用 MongoDB text 搜索 title 和 content
            filt["$text"] = {"$search": search}

        total = await self._col.count_documents(filt)
        cursor = self._col.find(filt)
        # text 搜索时按相关性排序，否则按更新时间降序
        if search:
            cursor = cursor.sort([("score", {"$meta": "textScore"})])
        else:
            cursor = cursor.sort([("updated_at", -1)])
        items = await cursor.skip(skip).limit(limit).to_list(length=limit)
        # 将 MongoDB ObjectId 转为字符串，便于 JSON 序列化
        for item in items:
            if "_id" in item:
                item["_id"] = str(item["_id"])
        return items, total

    async def get_entry(self, entry_id: str) -> dict | None:
        """获取单条记忆详情"""
        doc = await self._col.find_one({"id": entry_id})
        if doc and "_id" in doc:
            # MongoDB ObjectId → 字符串，便于 JSON 序列化返回前端
            doc["_id"] = str(doc["_id"])
        # doc 为 None（不存在）→ 返回 None
        return doc

    async def upsert_entry(self, data: dict) -> str:
        """
        按 source_hash 去重插入/更新记忆条目。
        存在同 hash 条目时合并 source_api_ids 和 tags，提升置信度。
        返回条目 id。
        """
        source_hash = data.get("source_hash", "")
        if not source_hash:
            # 无预计算 hash（如从 LLM 提取直接传入）→ 基于 project+type+title 生成
            source_hash = _make_hash(data.get("project_id", "default"), data.get("type", ""), data.get("title", ""))
            data["source_hash"] = source_hash

        existing = await self._col.find_one({"source_hash": source_hash})
        if existing:
            # 合并：追加 source_api_ids、合并 tags、提升置信度
            merged_api_ids = list(set(existing.get("source_api_ids", []) + data.get("source_api_ids", [])))
            merged_tags = list(set(existing.get("tags", []) + data.get("tags", [])))
            new_confidence = min(1.0, existing.get("confidence", 0.5) + 0.1)
            await self._col.update_one(
                {"source_hash": source_hash},
                {"$set": {
                    "source_api_ids": merged_api_ids,
                    "tags": merged_tags,
                    "confidence": new_confidence,
                    "updated_at": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None),
                }},
            )
            return existing["id"]

        # 新条目
        entry_id = data.get("id") or str(uuid.uuid4())
        data["id"] = entry_id
        data.setdefault("confidence", 0.5)
        data.setdefault("usage_count", 0)
        data.setdefault("upvote_count", 0)
        data.setdefault("downvote_count", 0)
        data.setdefault("updated_by", "system")
        now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        data.setdefault("created_at", now)
        data.setdefault("updated_at", now)
        await self._col.insert_one(data)
        return entry_id

    async def update_entry(self, entry_id: str, data: dict) -> bool:
        """用户手动更新记忆条目"""
        update: dict[str, Any] = {"updated_at": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None), "updated_by": "user"}
        if "title" in data and data["title"] is not None:
            # 标题变更 → 更新 title 并重新计算 source_hash（hash 依赖 title）
            update["title"] = data["title"]
            doc = await self._col.find_one({"id": entry_id})
            if doc:
                # doc 存在 → 用新的 title 重新生成 hash（project_id+type+新title）
                update["source_hash"] = _make_hash(doc["project_id"], doc["type"], data["title"])
            # doc 不存在（被并发删除）→ 跳过 hash 更新
        if "content" in data and data["content"] is not None:
            # 内容变更 → 更新 content 字段
            update["content"] = data["content"]
        if "tags" in data and data["tags"] is not None:
            # 标签变更 → 更新 tags 列表
            update["tags"] = data["tags"]
        result = await self._col.update_one({"id": entry_id}, {"$set": update})
        # modified_count > 0：确有修改；==0：entry 不存在或无变更
        return result.modified_count > 0

    async def delete_entry(self, entry_id: str) -> bool:
        """删除单条记忆"""
        result = await self._col.delete_one({"id": entry_id})
        return result.deleted_count > 0

    async def batch_delete(self, ids: list[str]) -> int:
        """批量删除记忆条目，返回删除数量"""
        result = await self._col.delete_many({"id": {"$in": ids}})
        return result.deleted_count

    # ── 多维检索（分析前调用） ────────────────────────────

    async def retrieve(
        self, project_id: str, context: dict, limit: int = 8,
    ) -> list[dict]:
        """
        根据分析上下文检索相关记忆，5 因子加权评分：
        - 关键词匹配 35%
        - 标签重叠 25%
        - 路径相似 20%
        - 字段匹配 10%
        - 语义相似 10%（context.summary 与 entry 内容的词汇重叠度）
        最低分阈值 0.12，结果按 composite_score * usage_boost * time_decay 排序。
        检索成功后对被检索条目 usage_count += 1。
        """
        # 构建搜索关键词：路径分词 + 方法 + summary
        path = context.get("path", "")
        method = context.get("method", "")
        summary = context.get("summary", "")
        # 去除路径参数占位符 {xxx} 后按 / 分词，扩大无占位符的词元覆盖面
        import re
        path_terms = [t for t in re.sub(r"\{[^}]+\}", "", path).split("/") if t]
        context_tags = set(path_terms + ([method.lower()] if method else []))
        context_fields = set(context.get("response_body_keys", []))

        # 从 DB 获取候选条目（项目下所有，按更新时间取最近 100 条，confidence >= 0.15）
        candidates = await self._col.find({"project_id": project_id, "confidence": {"$gte": 0.15}}).sort([("updated_at", -1)]).limit(100).to_list(length=100)

        search_text = " ".join(path_terms) + " " + summary
        scored = []
        for entry in candidates:
            score = _compute_score(entry, search_text, context_tags, context_fields, summary)
            if score >= 0.12:  # 略低于原阈值，语义因子可能带来新的匹配
                scored.append((entry, score))

        # 排序：composite_score * usage_boost * time_decay
        import math
        now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        HALF_LIFE_DAYS = 30

        def _sort_key(item):
            entry, score = item
            usage = entry.get("usage_count", 0)
            boost = 1 + 0.1 * math.log(usage + 1)
            # 时间衰减：30天半衰期，越旧权重越低
            updated_at = entry.get("updated_at")
            if isinstance(updated_at, datetime):
                # 是 datetime 类型 → 计算精确天数；根据有无时区信息选择不同计算方式（避免 tzinfo 相减报错）
                age_days = (now - updated_at.replace(tzinfo=None)).total_seconds() / 86400 if updated_at.tzinfo is None else (now - updated_at).total_seconds() / 86400
            else:
                # 无时间戳或格式非法 → 视为 90 天旧数据，权重极低
                age_days = 90
            decay = 0.5 ** (age_days / HALF_LIFE_DAYS)
            return score * boost * decay

        scored.sort(key=_sort_key, reverse=True)
        results = [e for e, _ in scored[:limit]]

        # 检索成功后 usage_count += 1（任何检索动作均为"使用"反馈，用于 boost 计算）
        if results:
            # results 非空 → 有匹配条目 → 增加使用计数并同步本地缓存
            ids = [e["id"] for e in results]
            await self._col.update_many(
                {"id": {"$in": ids}},
                {"$inc": {"usage_count": 1}},
            )
            for e in results:
                e["usage_count"] = e.get("usage_count", 0) + 1
        # results 为空 → 无匹配条目，不执行任何计数操作

        # 清理 _id（MongoDB ObjectId → 字符串，便于 JSON 序列化）
        for item in results:
            if "_id" in item:
                # 存在 MongoDB 原生 _id → 转为字符串
                item["_id"] = str(item["_id"])
        return results

    # ── 格式化（prompt 预算控制） ─────────────────────────

    @staticmethod
    def format_context(entries: list[dict], max_tokens: int = 600) -> str:
        """
        将记忆条目格式化为可注入 system prompt 的文本。
        按置信度降序排列，最多 6 条，每条 content 截断至 150 字符，总预算 ~500 tokens。
        显示置信度百分比和条目年龄（天）。
        """
        if not entries:
            # 无记忆条目 → 返回空字符串，prompt 中不注入任何内容
            return ""

        # 按置信度降序排列（高置信度条目优先注入 prompt）
        sorted_entries = sorted(entries, key=lambda e: e.get("confidence", 0), reverse=True)

        TYPE_ICON = {
            "field_pattern": "🔵",
            "assertion_pattern": "🟠",
            "doc_pattern": "🟢",
            "scenario_pattern": "🟣",
        }
        now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        lines = []
        for e in sorted_entries[:6]:
            icon = TYPE_ICON.get(e.get("type", ""), "⚪")
            title = e.get("title", "")
            content = e.get("content", "")
            if len(content) > 150:
                # content 过长（>150字符）→ 截断至 147 字符 + "..."，控制 prompt 长度
                content = content[:147] + "..."
            # content 未超长 → 原样保留
            confidence = e.get("confidence", 0.5)
            # 计算条目年龄（天数），用于提示信息的时效性
            updated_at = e.get("updated_at")
            if isinstance(updated_at, datetime):
                # 是 datetime → 计算精确天数
                if updated_at.tzinfo is not None:
                    # 有时区信息 → 先剥离时区再计算天数差
                    age_days = (now - updated_at.replace(tzinfo=None)).days
                else:
                    # 无时区信息 → 直接计算天数差
                    age_days = (now - updated_at).days
            else:
                # 非 datetime 类型（无时间戳）→ 视为 90 天旧数据
                age_days = 90
            # 年轻条目（<7天）加🆕标记，过于陈旧（>90天）加📜标记，中间无标记
            age_hint = "🆕" if age_days < 7 else ("📜" if age_days > 90 else "")
            lines.append(f"{icon} {title}: {content} (置信度: {confidence * 100:.0f}%{age_hint})")
        return "\n".join(lines)

    # ── 记忆提取 ──────────────────────────────────────────

    async def extract_from_api(self, api_id: str, call_llm: Callable) -> int:
        """
        从单个已分析 API 提取可复用知识。
        call_llm: 异步函数 (user_prompt, system_prompt) -> str
        返回新创建的条目数。失败时返回 0 不阻塞主流程。
        """
        # 获取 API 文档
        doc = await self._db["api_dsls"].find_one({"id": api_id})
        if not doc:
            # API 文档不存在（可能已被删除）→ 返回 0，不阻塞主流程
            logger.warning("extract_from_api: API {} not found", api_id)
            return 0

        api_doc = doc.get("doc") or {}
        request = doc.get("request") or {}
        asserts = doc.get("asserts") or []

        project_id = doc.get("project_id", "default")
        method = request.get("method", "")
        path = request.get("path", "")
        summary = api_doc.get("summary", "")
        params = [p.get("name", "") for p in api_doc.get("params", [])]
        response_fields = [p.get("name", "") for p in api_doc.get("response_fields", [])]
        asserts_str = json.dumps([
            {"field": a.get("field", ""), "operator": a.get("operator", ""), "description": a.get("description", "")}
            for a in asserts
        ], ensure_ascii=False)

        # 调用 LLM 提取知识
        try:
            raw = await call_llm(
                _EXTRACT_USER.format(
                    project_id=project_id, method=method, path=path,
                    summary=summary, params=json.dumps(params, ensure_ascii=False),
                    response_fields=json.dumps(response_fields, ensure_ascii=False),
                    asserts=asserts_str,
                ),
                _EXTRACT_SYSTEM,
            )
            if not raw:
                # LLM 返回空字符串（模型拒绝回答或返回空）→ 返回 0
                logger.warning("extract_from_api: LLM returned empty for {}", api_id)
                return 0
            items = _safe_parse_json(raw)
            if not isinstance(items, list):
                # 解析结果是 dict 或非标量 → dict 包装为单元素列表继续处理，其他丢弃
                items = [items] if isinstance(items, dict) else []
            # items 是 list → 直接使用
        except Exception as e:
            # LLM 调用或 JSON 解析异常 → 返回 0，不阻塞主流程
            logger.warning("extract_from_api: LLM failed for {}: {}", api_id, e)
            return 0

        created = 0
        for item in items:
            if not isinstance(item, dict):
                # 非 dict 条目（如字符串、None）→ 跳过
                continue
            item_type = item.get("type", "")
            # 校验 type 必须是 4 种已知 KnowledgeType 之一，非法类型丢弃
            valid_types = {t.value for t in KnowledgeType}
            if item_type not in valid_types:
                # type 不在合法集合中 → 跳过该条目
                continue
            title = item.get("title", "").strip()
            content = item.get("content", "").strip()
            if not title or not content:
                # title 或 content 为空 → 无法作为有效知识条目，跳过
                continue

            entry_data = {
                "project_id": project_id,
                "type": item_type,
                "title": title,
                "content": content,
                "tags": item.get("tags", []),
                "source_api_ids": [api_id],
                "source_hash": _make_hash(project_id, item_type, title),
            }
            try:
                await self.upsert_entry(entry_data)
                created += 1
            except Exception as e:
                logger.warning("extract_from_api: failed to upsert entry: {}", e)

        logger.info("extract_from_api: {} entries created from {}", created, api_id)
        return created

    async def batch_extract(self, project_id: str, call_llm: Callable) -> dict:
        """
        批量提取项目下所有已分析 API 的记忆。
        分批处理（每批 3 个并发），通过 WebSocket 广播进度。
        完成后自动触发 consolidate。
        """
        # 查询项目下所有 analysis_status == DONE 的 API
        docs = await self._db["api_dsls"].find({
            "project_id": project_id,
            "analysis_status": "done",
        }).to_list(length=1000)
        if not docs:
            # 项目下无已完成分析的 API → 直接返回零结果
            return {"created": 0, "merged": 0, "total_processed": 0}

        api_ids = [d["id"] for d in docs]
        total = len(api_ids)
        created_total = 0
        merged_total = 0

        # 分批处理，每批 3 个并发
        batch_size = 3
        for i in range(0, total, batch_size):
            batch = api_ids[i:i + batch_size]
            tasks = [self.extract_from_api(aid, call_llm) for aid in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, int):
                    # 正常返回：int 为创建的条目数 → 累加
                    created_total += r
                elif isinstance(r, Exception):
                    # 任务异常（LLM调用失败等）→ 记录警告后跳过，不阻塞其他批次
                    logger.warning("batch_extract: task error: {}", r)

            processed = min(i + batch_size, total)
            # WebSocket 广播进度
            await self._broadcast("ai_analysis", {
                "type": "extract_progress",
                "processed": processed,
                "total": total,
            })

        # 自动触发合并去重（consolidate 失败不影响主流程，仅记录警告）
        try:
            consol_result = await self.consolidate(project_id)
            merged_total = consol_result.get("before", 0) - consol_result.get("after", 0)
        except Exception as e:
            # consolidate 异常 → 记录警告后继续，不阻塞 auto_merge
            logger.warning("batch_extract: consolidate failed: {}", e)

        # 自动触发置信度提升（≥3 个来源 API 的条目提升到 0.9+）
        promoted = 0
        try:
            auto_result = await self.auto_merge(project_id)
            promoted = auto_result.get("promoted", 0)
        except Exception as e:
            # auto_merge 异常 → 记录警告后继续，不阻塞完成广播
            logger.warning("batch_extract: auto_merge failed: {}", e)

        # 广播完成
        await self._broadcast("ai_analysis", {
            "type": "extract_done",
            "created": created_total,
            "merged": merged_total,
            "promoted": promoted,
        })

        logger.info("batch_extract: created={} merged={} total={}", created_total, merged_total, total)
        return {"created": created_total, "merged": merged_total, "total_processed": total}

    # ── 记忆合并 ──────────────────────────────────────────

    async def consolidate(self, project_id: str) -> dict:
        """
        合并相似记忆条目：按 (project_id, type) 分组，组内比较 title 编辑距离。
        满足合并条件的条目：保留置信度较高者，合并 source_api_ids 和 tags。
        """
        entries = await self._col.find({"project_id": project_id}).to_list(length=500)
        before = len(entries)
        if before < 2:
            # 条目数不足 2 条 → 无合并可能，直接返回
            return {"before": before, "after": before, "merged_pairs": []}

        # 按 (project_id, type) 分组
        groups: dict[str, list[dict]] = {}
        for e in entries:
            key = f"{e.get('project_id', '')}:{e.get('type', '')}"
            groups.setdefault(key, []).append(e)

        merged_pairs = []
        ids_to_delete: set[str] = set()

        for group_entries in groups.values():
            if len(group_entries) < 2:
                # 组内只有 1 条条目 → 无需合并，跳过
                continue
            # 组内两两比较
            n = len(group_entries)
            for i in range(n):
                if group_entries[i]["id"] in ids_to_delete:
                    # 当前条目已在先前比较中被标记删除 → 跳过
                    continue
                for j in range(i + 1, n):
                    if group_entries[j]["id"] in ids_to_delete:
                        # 待比较条目已被标记删除 → 跳过
                        continue
                    a, b = group_entries[i], group_entries[j]
                    dist = _levenshtein(
                        _normalize_title(a.get("title", "")),
                        _normalize_title(b.get("title", "")),
                    )
                    if dist < 3:
                        # title 编辑距离 < 3（高度相似）→ 触发合并
                        # 保留置信度较高者（置信度相同时保留前者）
                        if a.get("confidence", 0) >= b.get("confidence", 0):
                            keeper, removed = a, b
                        else:
                            keeper, removed = b, a

                        # 合并到 keeper
                        merged_api_ids = list(set(keeper.get("source_api_ids", []) + removed.get("source_api_ids", [])))
                        merged_tags = list(set(keeper.get("tags", []) + removed.get("tags", [])))
                        new_confidence = min(1.0, max(keeper.get("confidence", 0), removed.get("confidence", 0)) + 0.05)
                        # content 保留较长的（更丰富的描述优于简短描述）
                        new_content = keeper.get("content", "")
                        if len(removed.get("content", "")) > len(new_content):
                            # removed 的 content 更长 → 使用 removed 的内容
                            new_content = removed["content"]
                        # removed content 不长于 keeper → 保留 keeper 原 content

                        await self._col.update_one(
                            {"id": keeper["id"]},
                            {"$set": {
                                "source_api_ids": merged_api_ids,
                                "tags": merged_tags,
                                "confidence": new_confidence,
                                "content": new_content,
                                "usage_count": keeper.get("usage_count", 0) + removed.get("usage_count", 0),
                                "updated_at": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None),
                            }},
                        )
                        ids_to_delete.add(removed["id"])
                        merged_pairs.append((keeper["id"], removed["id"]))

        # 删除被合并的条目
        if ids_to_delete:
            # 有被合并条目需要删除 → 批量删除
            await self._col.delete_many({"id": {"$in": list(ids_to_delete)}})
        # ids_to_delete 为空 → 无合并发生，跳过删除

        after = before - len(ids_to_delete)

        # WebSocket 广播合并完成
        await self._broadcast("ai_analysis", {
            "type": "consolidate_done",
            "before": before,
            "after": after,
        })

        logger.info("consolidate: {} → {} entries (merged {} pairs)", before, after, len(merged_pairs))
        return {"before": before, "after": after, "merged_pairs": merged_pairs}

    # ── 自动提升置信度 ────────────────────────────────────

    async def auto_merge(self, project_id: str) -> dict:
        """
        当同一模式出现在 3 个以上不同 API 中时，自动提升为高置信度知识。
        扫描所有 source_api_ids 数量 >= 3 的条目，将 confidence 提升到 0.9+。
        返回 {promoted: N, skipped: M}。
        """
        entries = await self._col.find({
            "project_id": project_id,
            "confidence": {"$lt": 0.9},
        }).to_list(length=500)

        promoted = 0
        skipped = 0
        for e in entries:
            api_ids = e.get("source_api_ids", [])
            if len(api_ids) >= 3:
                # 至少有 3 个不同 API 证实该模式 → 提升为高置信度（0.9+），来源越多置信度越高
                new_confidence = min(1.0, 0.9 + 0.03 * min(len(api_ids) - 3, 5))
                await self._col.update_one(
                    {"id": e["id"]},
                    {"$set": {
                        "confidence": new_confidence,
                        "updated_at": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None),
                    }},
                )
                promoted += 1
            else:
                # API 来源 < 3 → 证据不足，暂不提升置信度
                skipped += 1

        if promoted > 0:
            # 至少提升了 1 条条目 → 记录，promoted == 0 时不记录（避免无效日志）
            logger.info("auto_merge: promoted {} entries to high confidence in project {}", promoted, project_id)
        return {"promoted": promoted, "skipped": skipped}

    # ── 用户反馈 ──────────────────────────────────────────

    async def submit_feedback(self, entry_id: str, action: str, meta: dict | None = None) -> dict | None:
        """
        处理用户反馈：
        - upvote: confidence += 0.05, upvote_count += 1
        - downvote: confidence -= 0.15, downvote_count += 1
        - implicit: 用户编辑 AI 生成内容时的隐式反馈（小幅调整 + 记录修改详情）
        返回 {confidence_before, confidence_after, entry_id}
        meta 为隐式反馈的附加信息（edit_field, old_value, new_value, api_id）
        """
        doc = await self._col.find_one({"id": entry_id})
        if not doc:
            # 条目不存在（可能已被删除）→ 返回 None，前端提示用户
            return None

        confidence_before = doc.get("confidence", 0.5)
        if action == "upvote":
            # 用户点赞 → 置信度小幅提升（+0.05），upvote_count 和 usage_count 各 +1
            confidence_after = min(1.0, confidence_before + 0.05)
            await self._col.update_one(
                {"id": entry_id},
                {"$set": {"confidence": confidence_after}, "$inc": {"upvote_count": 1, "usage_count": 1}},
            )
        elif action == "downvote":
            # 用户点踩 → 置信度较大降低（-0.15），downvote_count +1，usage_count 不变
            confidence_after = max(0.05, confidence_before - 0.15)
            await self._col.update_one(
                {"id": entry_id},
                {"$set": {"confidence": confidence_after}, "$inc": {"downvote_count": 1}},
            )
        elif action == "implicit":
            # 隐式反馈：用户手动修改了 AI 生成的字段，小幅降低置信度（-0.02）
            # 修改幅度越大，置信度降低越多（但保留用户纠正意图的正向信号）
            confidence_after = max(0.1, confidence_before - 0.02)
            await self._col.update_one(
                {"id": entry_id},
                {
                    "$set": {"confidence": confidence_after},
                    "$inc": {"usage_count": 1},
                    # 记录隐式反馈详情（追加到数组，最多保留 20 条）
                    "$push": {
                        "feedback_history": {
                            "$each": [{
                                "action": "implicit",
                                "ts": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None).isoformat(),
                                **(meta or {}),
                            }],
                            "$slice": -20,  # 只保留最近 20 条
                        },
                    },
                } if meta else {"$set": {"confidence": confidence_after}, "$inc": {"usage_count": 1}},
                # meta 非空 → 同时写入反馈历史（含修改详情）；meta 为空 → 仅调整置信度+usage
            )
        else:
            # 未知 action → 返回 None，前端可据此判断操作无效
            return None

        return {"confidence_before": confidence_before, "confidence_after": confidence_after, "entry_id": entry_id}

    # ── 辅助 ──────────────────────────────────────────────

    async def _broadcast(self, key: str, data: dict) -> None:
        """安全广播，无 ws_manager 时静默跳过"""
        if key == "ai_analysis" or key.startswith("ai_analysis:"):
            data.setdefault("project_id", "default")
            data.setdefault("job_id", data.get("job_id") or data.get("entry_id") or "")
            data.setdefault("type", "knowledge")
            data.setdefault("status", "done")
            if key == "ai_analysis" and data.get("project_id"):
                key = f"ai_analysis:{data['project_id']}"
        if self._ws is not None:
            # ws_manager 存在 → 尝试广播
            try:
                await self._ws.broadcast(key, data)
            except Exception as e:
                # 广播异常（网络断开等）→ 记录警告，不向上抛出（广播失败不应影响业务）
                logger.warning("Knowledge WS broadcast failed: {}", e)
        # ws_manager 为 None → 静默跳过，无需广播


# ── 模块级工具函数 ────────────────────────────────────────

def _make_hash(project_id: str, entry_type: str, title: str) -> str:
    """生成 source_hash: md5(project_id + type + title)"""
    raw = f"{project_id}:{entry_type}:{title}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _normalize_title(title: str) -> str:
    """规范化标题用于比较：去空格、小写"""
    return title.strip().lower()


def _levenshtein(s1: str, s2: str) -> int:
    """计算两个字符串的编辑距离 (Levenshtein distance)"""
    if len(s1) < len(s2):
        # 确保 s1 是较长的字符串，s2 是较短的（DP 矩阵用短串作列更高效）
        s1, s2 = s2, s1
    if not s2:
        # s2 为空字符串 → 编辑距离 = s1 的长度（全删除）
        return len(s1)

    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            # 插入/删除/替换 三种操作取最小代价
            curr.append(min(
                prev[j + 1] + 1,      # 删除
                curr[j] + 1,           # 插入
                prev[j] + (0 if c1 == c2 else 1),  # 替换
            ))
        prev = curr
    return prev[-1]


def _compute_score(
    entry: dict, search_text: str,
    context_tags: set, context_fields: set,
    summary: str = "",
) -> float:
    """
    计算记忆条目与当前分析上下文的匹配分数。
    5 因子加权：关键词 35% + 标签重叠 25% + 路径相似 20% + 字段匹配 10% + 语义相似 10%
    """
    score = 0.0

    # 1. 关键词匹配 (35%)：检查 search_text 中的词是否出现在 title + content 中
    title = entry.get("title", "").lower()
    content = entry.get("content", "").lower()
    combined = title + " " + content
    if search_text:
        # search_text 非空 → 提取有效词元（长度>1）计算命中率
        terms = [t for t in search_text.lower().split() if len(t) > 1]
        if terms:
            # 有有效词元 → 计算命中率并加权
            hits = sum(1 for t in terms if t in combined)
            score += 0.35 * (hits / len(terms))
        # terms 为空（无长度>1的词）→ 此因子得 0
    # search_text 为空 → 此因子得 0

    # 2. 标签重叠 (25%)：Jaccard 相似度
    entry_tags = set(t.lower() for t in entry.get("tags", []))
    if context_tags and entry_tags:
        # 双方都有标签 → 计算 Jaccard 相似度（交集/并集）
        jaccard = len(context_tags & entry_tags) / max(1, len(context_tags | entry_tags))
        score += 0.25 * jaccard
    # 任一方无标签 → 此因子得 0

    # 3. 路径相似 (20%)：context path 分词与 entry tags 的匹配率
    path_tags = {t for t in entry_tags if "/" not in t and len(t) > 1}
    context_no_slash = {t for t in context_tags if "/" not in t and len(t) > 1}
    if context_no_slash and path_tags:
        # 双方都有非路径标签 → 计算匹配率
        match_rate = len(context_no_slash & path_tags) / max(1, len(context_no_slash))
        score += 0.2 * match_rate
    # 任一方无有效标签 → 此因子得 0

    # 4. 字段匹配 (10%)：context 中 response_body_keys 与 entry tags 的交集率
    if context_fields and entry_tags:
        # 双方都有字段/标签 → 计算交集率
        field_match = len(context_fields & entry_tags) / max(1, len(context_fields))
        score += 0.1 * field_match
    # 任一方为空 → 此因子得 0

    # 5. 语义相似 (10%)：summary 与 entry content/title 的词汇重叠度
    # 解决无标签匹配但语义相关的情况（如 "用户登录" ↔ "auth/token"）
    if summary:
        # summary 非空 → 对 summary 和 entry 内容分词（去重为集合，避免长文本主导）
        import re
        summary_words = set(re.findall(r'\w+', summary.lower()))
        entry_words = set(re.findall(r'\w+', combined))
        if summary_words and entry_words:
            # 双方都有有效词 → 计算语义重叠度，放大低重叠信号后加权
            overlap = len(summary_words & entry_words)
            semantic_sim = overlap / max(1, len(summary_words))
            # 词汇重叠度较低时给予适当加分（即使少量重叠也说明相关）
            score += 0.1 * min(1.0, semantic_sim * 2)
        # summary_words 或 entry_words 为空 → 此因子得 0
    # summary 为空 → 此因子得 0

    return score


def _safe_parse_json(text: str) -> Any:
    # 委托给共享工具函数，处理 LLM 非标准 JSON（trailing commas、Python literals 等）
    from ai_analyzer.utils import safe_parse_json
    return safe_parse_json(text)
