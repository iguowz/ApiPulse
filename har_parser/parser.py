"""
HAR 解析服务 v2
1. bytes → 单接口 ApiDSL 列表
2. 批量 insert_many + 确认 ack
3. 成功 → 推 AI 队列 / 失败 → 隔离区
4. 支持 project_id 注入、静态资源过滤、base64 响应体解码
"""
from __future__ import annotations

import base64
import hashlib
import inspect
import io
import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any
from urllib.parse import urlparse, parse_qs

from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase
from redis.asyncio import Redis

from models.dsl import ApiDSL, RequestDSL, ResponseDSL, HttpMethod, ParseStatus, BodyType
from har_parser.quarantine import QuarantineStore
from services.ai_job_service import AiJobService

# ijson 用于大文件流式解析，避免 json.loads 将整个 HAR 加载为 Python dict 造成内存翻倍
try:
    import ijson
    _HAS_IJSON = True
except ImportError:
    _HAS_IJSON = False

AI_ANALYZE_QUEUE = "queue:ai_analyze"

# 超过此阈值的 HAR 文件使用流式解析（逐 entry yield，批量处理）
_STREAMING_THRESHOLD = 10 * 1024 * 1024  # 10 MB
# 流式解析时每批处理的 entry 数量
_STREAMING_BATCH_SIZE = 200

# 过滤掉静态资源，只保留 API 请求
_SKIP_EXTENSIONS = {
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".ico", ".woff", ".woff2", ".ttf", ".map", ".webp",
}
_SKIP_CONTENT_TYPES = {
    "text/html", "text/css", "application/javascript",
    "image/", "font/", "video/", "audio/",
}


class HarParseError(Exception):
    pass


# ── 过滤判断 ──────────────────────────────────────────────

def _should_skip(entry: dict) -> bool:
    """判断是否为静态资源请求，跳过非 API 条目"""
    req = entry.get("request", {})
    url = req.get("url", "")
    parsed = urlparse(url)
    ext = "." + parsed.path.rsplit(".", 1)[-1].lower() if "." in parsed.path else ""
    if ext in _SKIP_EXTENSIONS:
        # 文件扩展名命中静态资源列表（.js/.css/.png等）→ 非 API 请求，跳过
        return True
    resp_content_type = ""
    # 防御：HAR 中 headers 字段可能为 null（None），or [] 确保迭代不报错
    for h in (entry.get("response") or {}).get("headers") or []:
        if h.get("name", "").lower() == "content-type":
            # 找到 Content-Type 头 → 提取值用于后续类型判断
            resp_content_type = h.get("value", "").lower()
            break
    for skip_ct in _SKIP_CONTENT_TYPES:
        if resp_content_type.startswith(skip_ct):
            # Content-Type 命中静态资源类型（text/html/image/font/video等）→ 跳过
            return True
    # 非静态资源（API 请求）→ 保留
    return False


# 按域名/URL关键字过滤：导入时仅保留匹配的条目
def _should_filter_entry(url: str, filter_host: str | None, filter_url: str | None) -> bool:
    """返回 True 表示应跳过（不匹配过滤条件）"""
    if not filter_host and not filter_url:
        # 无过滤条件 → 不过滤任何条目
        return False
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    # 域名过滤：netloc 必须包含 filter_host 子串（忽略大小写），否则跳过
    if filter_host and filter_host.lower() not in host:
        return True
    # URL 关键字过滤：完整 URL 必须包含 filter_url 子串（忽略大小写），否则跳过
    if filter_url and filter_url.lower() not in url.lower():
        return True
    # 通过所有过滤条件 → 保留
    return False


# 按项目级别域名白名单/黑名单过滤
def _should_filter_by_domain(url: str, allowlist: list[str], blocklist: list[str]) -> bool:
    """返回 True 表示应跳过该条目
    规则：白名单非空时仅放行匹配域名；黑名单中的域名始终拒绝；
          白名单为空+黑名单非空时，放行所有非黑名单域名
    """
    if not allowlist and not blocklist:
        # 无域名过滤配置 → 不过滤
        return False
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    # 白名单非空：仅放行匹配白名单的域名（子串匹配，忽略大小写）
    if allowlist:
        if not any(pattern.lower() in host for pattern in allowlist):
            # 域名不在白名单中 → 跳过
            return True
    # 黑名单：始终拒绝匹配的域名（子串匹配，忽略大小写）
    if blocklist:
        if any(pattern.lower() in host for pattern in blocklist):
            # 域名在黑名单中 → 跳过
            return True
    # 通过域名过滤 → 保留
    return False


# ── 解析工具函数 ──────────────────────────────────────────

def _headers_to_dict(headers: list[dict] | None) -> dict[str, str]:
    # 防御：HAR 中 headers 字段可能为 null（None），导致迭代报错
    if not headers:
        return {}
    return {
        h["name"]: h["value"]
        for h in headers
        if h.get("name") and not h["name"].startswith(":")  # 过滤 HTTP/2 伪头（:authority/:method/:path等）
    }


def _parse_body(post_data: dict | None) -> tuple[Any, str]:
    if not post_data:
        # 无请求体 → NONE 类型
        return None, BodyType.NONE
    mime = post_data.get("mimeType", "")
    text = post_data.get("text", "")
    if "json" in mime:
        # JSON 请求体：尝试解析为 dict，失败则回退为原始文本（可能被截断或格式异常）
        try:
            return json.loads(text), BodyType.JSON
        except (json.JSONDecodeError, TypeError):
            return text, BodyType.TEXT
    if "multipart" in mime:
        # multipart/form-data：从 params 列表中提取 name→value 映射
        params = post_data.get("params", [])
        return {p["name"]: p.get("value", "") for p in params}, BodyType.MULTIPART
    if "x-www-form-urlencoded" in mime or "form" in mime:
        # 表单编码：优先使用 params 列表，备选从 text 解析 query 字符串
        params = post_data.get("params", [])
        if params:
            return {p["name"]: p.get("value", "") for p in params}, BodyType.FORM
        try:
            parsed = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(text).items()}
            return parsed, BodyType.FORM
        except Exception:
            # parse_qs 解析失败 → 回退为原始文本
            return text, BodyType.TEXT
    if "xml" in mime:
        # XML 请求体：保留为原始文本（不做 XML→dict 转换，保持原始输入）
        return text, BodyType.XML
    # 未识别的 MIME 类型 → 保留原始文本或 None
    return text or None, BodyType.TEXT


def _parse_response_body(content: dict) -> Any:
    mime = content.get("mimeType", "")
    text = content.get("text", "")
    encoding = content.get("encoding", "")

    if not text:
        # 空响应体（如 204 No Content）→ None
        return None

    # base64 编码（二进制响应，如图片/压缩数据）
    if encoding == "base64":
        try:
            decoded = base64.b64decode(text).decode("utf-8", errors="replace")
            text = decoded
        except Exception:
            # base64 解码失败（损坏数据）→ 标记为二进制无法解析
            return "[binary response]"

    if "json" in mime:
        # JSON 响应体：尝试解析为 dict/list，失败则回退为原始文本
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text
    # 非 JSON 响应（text/html 等）→ 返回原始文本
    return text


def _make_hash(method: str, url: str, body: Any) -> str:
    """去重指纹：method + url（不含 query）+ body 结构"""
    parsed = urlparse(url)
    url_no_query = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    raw = f"{method}|{url_no_query}|{json.dumps(body, sort_keys=True, default=str)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _entry_to_dsl(entry: dict, source_har: str, project_id: str) -> ApiDSL:
    req = entry.get("request", {})
    resp = entry.get("response", {})

    method = req.get("method", "GET").upper()
    raw_url = req.get("url", "")
    parsed = urlparse(raw_url)

    # query params（从 URL 解析，HAR queryString 字段仅作参考）
    query_params: dict[str, Any] = {}
    for k, v in parse_qs(parsed.query).items():
        query_params[k] = v[0] if len(v) == 1 else v

    body, body_type = _parse_body(req.get("postData"))
    resp_body = _parse_response_body(resp.get("content", {}))
    latency = int(entry.get("time", 0))

    request_dsl = RequestDSL(
        method=HttpMethod(method),
        url=raw_url,
        path=parsed.path,
        query_params=query_params,
        headers=_headers_to_dict(req.get("headers", [])),
        body=body,
        body_type=body_type,
    )
    response_dsl = ResponseDSL(
        status_code=resp.get("status", 0),
        headers=_headers_to_dict(resp.get("headers", [])),
        body=resp_body,
        latency_ms=latency,
    )
    source_hash = _make_hash(method, raw_url, body)
    name = f"{method} {parsed.path}" if parsed.path else raw_url[:80]

    return ApiDSL(
        id=str(uuid.uuid4()),
        name=name,
        source_har=source_har,
        source_hash=source_hash,
        request=request_dsl,
        response=response_dsl,
        parse_status=ParseStatus.SUCCESS,
        project_id=project_id,
        created_at=datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None),
        updated_at=datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None),
    )


# ── 主服务 ────────────────────────────────────────────────

class HarParserService:
    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        redis: Redis,
        quarantine: QuarantineStore,
        project_id: str = "default",
        filter_host: str | None = None,
        filter_url: str | None = None,
        # 项目级别域名白名单/黑名单（从 Project 配置读取）
        domain_allowlist: list[str] | None = None,
        domain_blocklist: list[str] | None = None,
    ):
        self._db = db
        self._redis = redis
        self._quarantine = quarantine
        self._project_id = project_id
        # 导入时按域名/URL 关键字过滤，仅保留匹配条目
        self._filter_host = filter_host
        self._filter_url = filter_url
        # 项目级别域名白名单/黑名单过滤
        self._domain_allowlist = domain_allowlist or []
        self._domain_blocklist = domain_blocklist or []
        self._col = db["api_dsls"]
        # 差异检测服务（可选注入，不存在时跳过差异检测）
        self._diff_service = None

    def set_diff_service(self, diff_service):
        """注入差异检测服务，用于导入后自动对比已分析 API 的字段变化"""
        self._diff_service = diff_service

    async def parse_and_save(self, filename: str, content: bytes) -> dict[str, Any]:
        file_size = len(content)
        logger.info("HAR parse start: {} (project={}, size={}MB)", filename, self._project_id, file_size / 1024 / 1024)

        # 大文件（>10MB）使用 ijson 流式解析，避免 json.loads 将整个 HAR 加载为 Python dict 导致内存翻倍
        if _HAS_IJSON and file_size > _STREAMING_THRESHOLD:
            return await self._parse_streaming(filename, content)

        # ── 小文件路径：一次性 json.loads ──
        # 1. 解析 JSON 结构
        try:
            har = json.loads(content.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            # JSON 格式错误或编码异常 → 整个文件移入隔离区（无法部分恢复）
            await self._handle_failure(filename, content, f"JSON decode error: {e}")
            return {"status": "failed", "reason": str(e), "filename": filename}

        entries = har.get("log", {}).get("entries", [])
        if not entries:
            # 空 HAR（无任何请求记录）→ 隔离（无有效数据可解析）
            await self._handle_failure(filename, content, "No entries in HAR")
            return {"status": "failed", "reason": "no entries", "filename": filename}

        # 2. 过滤静态资源 + 域名/URL过滤 + 项目域名白名单/黑名单 + 逐条解析
        success_dsls: list[ApiDSL] = []
        skipped_static = 0
        skipped_filter = 0  # 被域名/URL 过滤跳过的条目数
        skipped_domain = 0  # 被项目域名白名单/黑名单过滤跳过的条目数
        failed_entries: list[dict] = []

        for i, entry in enumerate(entries):
            if _should_skip(entry):
                # 静态资源（JS/CSS/图片等）→ 计数后跳过
                skipped_static += 1
                continue
            req_url = entry.get("request", {}).get("url", "")
            if _should_filter_entry(req_url, self._filter_host, self._filter_url):
                # 未通过域名/URL 关键字过滤 → 跳过（导入时指定了 filter_host/filter_url）
                skipped_filter += 1
                continue
            if _should_filter_by_domain(req_url, self._domain_allowlist, self._domain_blocklist):
                # 未通过项目级域名白名单/黑名单 → 跳过
                skipped_domain += 1
                continue
            try:
                dsl = _entry_to_dsl(entry, filename, self._project_id)
                success_dsls.append(dsl)
            except Exception as e:
                # 单条解析失败不影响其他条目 → 记录日志继续
                logger.warning("Entry[{}] parse failed: {}", i, e)
                failed_entries.append({"index": i, "error": str(e)})

        if not success_dsls:
            # 所有条目被过滤或解析失败 → 无有效数据，移入隔离区
            await self._handle_failure(filename, content, "No valid API entries after filtering")
            return {
                "status": "failed",
                "reason": "no valid api entries",
                "filename": filename,
                "skipped_static": skipped_static,
                "skipped_filter": skipped_filter,
                "skipped_domain": skipped_domain,
                "failed_count": len(failed_entries),
            }

        # 3. 去重（基于 source_hash 与已有 API 比对）
        new_dsls = await self._dedup(success_dsls)
        logger.info(
            "HAR {}: total={} static_skip={} filter_skip={} domain_skip={} parsed={} new={} dup={} fail={}",
            filename, len(entries), skipped_static, skipped_filter, skipped_domain, len(success_dsls),
            len(new_dsls), len(success_dsls) - len(new_dsls), len(failed_entries),
        )

        inserted_ids: list[str] = []
        if new_dsls:
            # 4. 写入 MongoDB（确认 ack 后原始文件自动丢弃，内存中的 content 不再引用）
            inserted_ids = await self._write_to_mongo(new_dsls)
            logger.info("HAR {} → MongoDB OK, original HAR discarded", filename)
            # 5. 推送 AI 分析队列（触发自动文档/断言生成）
            await self._push_ai_queue(inserted_ids)
            # 6. 差异检测：对比新导入 API 与已分析 API 的字段变化（非阻塞）
            if self._diff_service:
                try:
                    diff_ids = await self._diff_service.detect_for_batch(inserted_ids, self._project_id)
                    if diff_ids:
                        logger.info("HAR {} → diff detection: {} diffs found", filename, len(diff_ids))
                except Exception as e:
                    # 差异检测失败不阻塞导入流程
                    logger.warning("HAR diff detection failed (non-blocking): {}", e)
        # 无新 API（全部去重命中）→ 跳过写入/推送/差异检测，直接返回成功

        return {
            "status": "success",
            "filename": filename,
            "project_id": self._project_id,
            "total_entries": len(entries),
            "skipped_static": skipped_static,
            "skipped_filter": skipped_filter,
            "skipped_domain": skipped_domain,
            "new_apis": len(new_dsls),
            "skipped_duplicates": len(success_dsls) - len(new_dsls),
            "failed_entries": len(failed_entries),
            "api_ids": inserted_ids,
        }

    # ── 流式解析（大文件路径）──────────────────────────────
    # 使用 ijson 逐 entry yield，批量处理，避免 json.loads 将整个 HAR dict 加载到内存
    async def _parse_streaming(self, filename: str, content: bytes) -> dict[str, Any]:
        """ijson 流式解析 HAR，逐批过滤→转换→去重→写入→推送 AI 队列"""
        stream = io.BytesIO(content)
        total_entries = 0
        skipped_static = 0
        skipped_filter = 0
        skipped_domain = 0
        failed_entries: list[dict] = []
        all_inserted_ids: list[str] = []
        total_new = 0
        total_dup = 0

        batch: list[dict] = []

        # 逐批收集 entry 再批量处理（ijson 本身是流式的，避免持有所有 entry）
        async def _process_batch(entries: list[dict]) -> tuple[int, int]:
            """处理一批 entry：过滤→转 DSL→去重→写库→推队列，返回 (new_count, dup_count)"""
            nonlocal skipped_static, skipped_filter, skipped_domain, failed_entries, all_inserted_ids

            dsls: list[ApiDSL] = []
            for idx, entry in enumerate(entries):
                if _should_skip(entry):
                    # 静态资源 → 跳过
                    skipped_static += 1
                    continue
                req_url = entry.get("request", {}).get("url", "")
                if _should_filter_entry(req_url, self._filter_host, self._filter_url):
                    # 未通过域名/URL 关键字过滤 → 跳过
                    skipped_filter += 1
                    continue
                if _should_filter_by_domain(req_url, self._domain_allowlist, self._domain_blocklist):
                    # 未通过项目级域名白名单/黑名单 → 跳过
                    skipped_domain += 1
                    continue
                try:
                    dsls.append(_entry_to_dsl(entry, filename, self._project_id))
                except Exception as e:
                    # 单条解析失败不阻塞整批 → 记录日志继续
                    logger.warning("Streaming entry parse failed: {}", e)
                    failed_entries.append({"error": str(e)})

            if not dsls:
                # 该批全部被过滤或解析失败 → 跳过后续处理
                return 0, 0

            new_dsls = await self._dedup(dsls)
            dup = len(dsls) - len(new_dsls)

            if new_dsls:
                # 有新增 API（去重后）→ 写库 + 推 AI 队列 + 差异检测
                ids = await self._write_to_mongo(new_dsls)
                all_inserted_ids.extend(ids)
                await self._push_ai_queue(ids)
                # 差异检测：对比新导入 API 与已分析 API 的字段变化（非阻塞）
                if self._diff_service:
                    try:
                        diff_ids = await self._diff_service.detect_for_batch(ids, self._project_id)
                        if diff_ids:
                            logger.info("Stream batch diff detection: {} diffs", len(diff_ids))
                    except Exception as e:
                        # 差异检测失败不阻塞导入流程
                        logger.warning("Stream batch diff detection failed (non-blocking): {}", e)
            # 无新 API（全部去重命中）→ 跳过写入/推送

            return len(new_dsls), dup

        try:
            # ijson.items 流式解析 log.entries 数组，逐个 yield entry dict
            entry_iter = ijson.items(stream, "log.entries.item", use_float=True)
            for entry in entry_iter:
                total_entries += 1
                batch.append(entry)
                # 每满一批即处理，避免内存堆积（200条/批）
                if len(batch) >= _STREAMING_BATCH_SIZE:
                    n, d = await _process_batch(batch)
                    total_new += n
                    total_dup += d
                    batch.clear()

            # 处理剩余不足一批的 entry（最后一批<200条）
            if batch:
                n, d = await _process_batch(batch)
                total_new += n
                total_dup += d

        except ijson.JSONError as e:
            # 非标准 HAR JSON 结构（如缺少 log.entries 字段）→ 移入隔离区
            logger.error("Streaming parse JSON error [{}]: {}", filename, e)
            await self._handle_failure(filename, content, f"Streaming JSON error: {e}")
            return {"status": "failed", "reason": str(e), "filename": filename}
        except Exception as e:
            # 流式解析意外错误（如 IO 异常）→ 移入隔离区
            logger.error("Streaming parse unexpected error [{}]: {}", filename, e)
            await self._handle_failure(filename, content, f"Streaming error: {e}")
            return {"status": "failed", "reason": str(e), "filename": filename}

        if total_new == 0 and total_entries == 0:
            # HAR 为空（无任何 entry）→ 移入隔离区
            await self._handle_failure(filename, content, "No entries in HAR (streaming)")
            return {"status": "failed", "reason": "no entries", "filename": filename}

        logger.info(
            "HAR(stream) {}: total={} static_skip={} filter_skip={} domain_skip={} new={} dup={} fail={}",
            filename, total_entries, skipped_static, skipped_filter, skipped_domain,
            total_new, total_dup, len(failed_entries),
        )

        return {
            "status": "success",
            "filename": filename,
            "project_id": self._project_id,
            "total_entries": total_entries,
            "skipped_static": skipped_static,
            "skipped_filter": skipped_filter,
            "skipped_domain": skipped_domain,
            "new_apis": total_new,
            "skipped_duplicates": total_dup,
            "failed_entries": len(failed_entries),
            "api_ids": all_inserted_ids,
            "streamed": True,
        }

    # ── 内部方法 ──────────────────────────────────────────

    async def _dedup(self, dsls: list[ApiDSL]) -> list[ApiDSL]:
        """基于 source_hash 去重：查询已存在的 hash，过滤掉重复 API"""
        hashes = [d.source_hash for d in dsls]
        existing = set(
            await self._col.distinct("source_hash", {"project_id": self._project_id, "source_hash": {"$in": hashes}})
        )
        # 只保留数据库中不存在的 hash → 新 API
        return [d for d in dsls if d.source_hash not in existing]

    async def _write_to_mongo(self, dsls: list[ApiDSL]) -> list[str]:
        docs = [d.model_dump() for d in dsls]
        try:
            result = await self._col.insert_many(docs, ordered=False)
            if len(result.inserted_ids) != len(docs):
                # 部分写入失败（如并发去重时唯一索引冲突）→ 记录差异但不阻塞
                logger.warning(
                    "Partial insert: expected {} got {}",
                    len(docs), len(result.inserted_ids),
                )
            ids = [d.id for d in dsls[:len(result.inserted_ids)]]
            logger.info("Inserted {} API DSLs", len(result.inserted_ids))
            return ids
        except Exception as e:
            # source_hash 唯一索引 + 并发场景可能触发 DuplicateKeyError
            # ordered=False 时部分文档可能已成功写入，通过 distinct 回查实际入库 ID
            logger.warning("Bulk insert partial failure (concurrent dedup?): {}", e)
            inserted_hashes = set(await self._col.distinct(
                "source_hash", {"project_id": self._project_id, "source_hash": {"$in": [d.source_hash for d in dsls]}}
            ))
            # 从原 DSL 列表中筛选出已成功写入的 ID（并发时部分文档可能已被其他进程写入）
            ids = [d.id for d in dsls if d.source_hash in inserted_hashes]
            logger.info("Recovered {} inserted IDs after partial failure", len(ids))
            return ids

    async def _push_ai_queue(self, api_ids: list[str]) -> None:
        ai_job_service = AiJobService(self._db)
        # pipeline 内单个操作无需 await，仅 execute() 需要 await；避免冗余协程开销
        async with self._redis.pipeline(transaction=False) as pipe:
            for api_id in api_ids:
                now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
                job_id = f"ai_analyze:{api_id}:{uuid.uuid4().hex[:8]}"
                payload = {
                    "api_id": api_id,
                    "project_id": self._project_id,
                    "job_id": job_id,
                    "status": "queued",
                    "ts": now.isoformat(),
                }
                rpush_result = pipe.rpush(
                    AI_ANALYZE_QUEUE,
                    json.dumps(payload, ensure_ascii=False),
                )
                # redis.asyncio 的 pipeline 命令在不同版本/测试 mock 下可能返回 pipeline 或 coroutine。
                # 这里兼容两种返回值，避免真实运行和单测任一侧出现未 await 的队列写入。
                if inspect.isawaitable(rpush_result):
                    await rpush_result
                await ai_job_service.mark_queued(
                    job_id=job_id,
                    type="ai_analyze",
                    project_id=self._project_id,
                    source="har_import",
                    target_ids=[api_id],
                    queue_key=AI_ANALYZE_QUEUE,
                    payload=payload,
                )
            await pipe.execute()
        logger.info("Pushed {} tasks → {}", len(api_ids), AI_ANALYZE_QUEUE)

    async def _handle_failure(self, filename: str, content: bytes, reason: str) -> None:
        logger.error("HAR parse FAILED [{}]: {}", filename, reason)
        try:
            # 将原始 HAR 文件保存到隔离区供后续排查
            path = await self._quarantine.save(filename, content, reason)
            logger.warning("Quarantined → {}", path)
        except Exception as e:
            # 隔离区保存失败不阻塞主流程（文件已丢失，无恢复手段）
            logger.error("Quarantine failed: {}", e)
