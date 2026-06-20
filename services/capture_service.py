"""
抓包服务层 —— mitmproxy 实时抓包 ingest 管线
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any
from urllib.parse import urlparse

from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase

from har_parser.parser import _should_filter_by_domain
from models.dsl import ApiDSL, BodyType, HttpMethod, ParseStatus, RequestDSL, ResponseDSL
from services.ai_job_service import AiJobService


class CaptureService:
    """抓包 ingest 业务逻辑：过滤 → 去重 → 构建 DSL → 入库存 → 推队列"""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        # 差异检测服务（可选注入，不存在时跳过差异检测）
        self._diff_service = None

    def set_diff_service(self, diff_service):
        """注入差异检测服务，用于抓包后自动对比已分析 API 的字段变化"""
        self._diff_service = diff_service

    async def ingest(
        self,
        payload: dict[str, Any],
        capture_state: dict[str, Any],
        capture_lock,
        redis,
    ) -> dict[str, Any]:
        """处理单条 mitmproxy 抓包记录"""
        project_id = payload.get("project_id", "default")
        method = payload.get("method", "GET").upper()
        raw_url = payload.get("url", "")
        parsed = urlparse(raw_url)

        # 按域名/URL 关键字过滤（capture_state 中的动态过滤条件）
        async with capture_lock:
            _filter_host = capture_state.get("filter_host")
            _filter_url = capture_state.get("filter_url")
        if _filter_host and _filter_host.lower() not in parsed.netloc.lower():
            return {"status": "filtered"}
        if _filter_url and _filter_url.lower() not in raw_url.lower():
            return {"status": "filtered"}

        # 项目级别域名白名单/黑名单过滤
        proj = await self.db["projects"].find_one(
            {"id": project_id}, {"domain_allowlist": 1, "domain_blocklist": 1},
        )
        if proj:
            allowlist = proj.get("domain_allowlist", []) or []
            blocklist = proj.get("domain_blocklist", []) or []
            if _should_filter_by_domain(raw_url, allowlist, blocklist):
                return {"status": "filtered", "reason": "domain_policy"}

        # 构建请求 DSL
        from urllib.parse import parse_qs as _pqs
        query_params: dict[str, Any] = {}
        for k, v in _pqs(parsed.query).items():
            query_params[k] = v[0] if len(v) == 1 else v

        request_dsl = RequestDSL(
            method=HttpMethod(method),
            url=raw_url,
            path=parsed.path if parsed.path else payload.get("path", "/"),
            query_params=query_params,
            headers=payload.get("request_headers", {}),
            body=payload.get("request_body"),
            body_type=payload.get("body_type", BodyType.NONE),
        )
        response_dsl = ResponseDSL(
            status_code=payload.get("status_code", 0),
            headers=payload.get("response_headers", {}),
            body=payload.get("response_body"),
            latency_ms=payload.get("latency_ms", 0),
        )

        # 去重指纹（与 har_parser/parser.py _make_hash 一致）
        url_no_query = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        raw = f"{method}|{url_no_query}|{json.dumps(payload.get('request_body'), sort_keys=True, default=str)}"
        source_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]

        # 检查是否已存在
        existing = await self.db["api_dsls"].find_one({"project_id": project_id, "source_hash": source_hash}, {"id": 1})
        if existing:
            async with capture_lock:
                capture_state["last_ingest_at"] = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None).isoformat()
            return {"status": "duplicate", "api_id": existing["id"]}

        api_id = str(uuid.uuid4())
        now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        name = f"{method} {parsed.path}" if parsed.path else raw_url[:80]

        api_dsl = ApiDSL(
            id=api_id,
            name=name,
            source_har=f"mitmproxy://{datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%dT%H%M%S')}",
            source_hash=source_hash,
            request=request_dsl,
            response=response_dsl,
            parse_status=ParseStatus.SUCCESS,
            project_id=project_id,
            created_at=now,
            updated_at=now,
        )
        try:
            await self.db["api_dsls"].insert_one(api_dsl.model_dump())
        except Exception:
            # source_hash 唯一索引 + 并发 ingest：可能已被另一线程写入，回退为查出现有 ID
            existing = await self.db["api_dsls"].find_one({"project_id": project_id, "source_hash": source_hash}, {"id": 1})
            if existing:
                async with capture_lock:
                    capture_state["last_ingest_at"] = now.isoformat()
                return {"status": "duplicate", "api_id": existing["id"]}
            raise

        # 推送 AI 分析队列，同时持久化 job 便于生产队列页追踪抓包来源任务。
        job_id = f"ai_analyze:{api_id}:{uuid.uuid4().hex[:8]}"
        task = {"api_id": api_id, "project_id": project_id, "job_id": job_id, "status": "queued", "ts": now.isoformat()}
        await redis.rpush("queue:ai_analyze", json.dumps(task, ensure_ascii=False))
        await AiJobService(self.db).mark_queued(
            job_id=job_id,
            type="ai_analyze",
            project_id=project_id,
            source="capture",
            target_ids=[api_id],
            queue_key="queue:ai_analyze",
            payload=task,
        )

        async with capture_lock:
            capture_state["ingested_count"] += 1
            capture_state["last_ingest_at"] = now.isoformat()

        # 差异检测：对比新抓包 API 与已分析 API 的字段变化
        if self._diff_service:
            try:
                diff_id = await self._diff_service.detect_and_record(
                    parsed.path if parsed.path else payload.get("path", "/"),
                    method, api_id, project_id,
                )
                if diff_id:
                    logger.info("Capture diff detected: {} → {}", name, diff_id)
            except Exception as e:
                logger.warning("Capture diff detection failed (non-blocking): {}", e)

        logger.info("Capture ingested: {} → {}", name, api_id)
        return {"status": "ingested", "api_id": api_id, "name": name}
