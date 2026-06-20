"""
API DSL 服务层 —— 查询构建、执行编排、断言管理
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any
from urllib.parse import urlparse

from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase

from dag_engine.engine import DagExecutionEngine
from models.dsl import ApiDSL, ApiDoc, AssertRule, BodyType, Environment, HttpMethod, ParamDoc, ParseStatus, RequestDSL, ResponseDSL
from services.ai_job_service import AiJobService


class ApiService:
    """API DSL 业务逻辑，不依赖 HTTP 层"""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    # ── 查询 ─────────────────────────────────────────────────

    async def list_apis(
        self,
        project_id: str = "default",
        analysis_status: str | None = None,
        tag: str | None = None,
        method: str | None = None,
        search: str | None = None,
        source: str | None = None,
        status_code_min: int | None = None,
        status_code_max: int | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        skip: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """构建查询条件、排序并分页返回 API 列表"""
        q: dict[str, Any] = {"project_id": project_id}
        if analysis_status:
            q["analysis_status"] = analysis_status
        if tag:
            q["tags"] = tag
        if method:
            q["request.method"] = method.upper()
        if search:
            q["$or"] = [
                {"name": {"$regex": search, "$options": "i"}},
                {"request.path": {"$regex": search, "$options": "i"}},
            ]
        # 来源过滤：source_har 字段模糊匹配（如 har://, mitmproxy://）
        if source:
            q["source_har"] = {"$regex": source, "$options": "i"}
        # 状态码范围过滤
        if status_code_min is not None or status_code_max is not None:
            sc: dict[str, Any] = {}
            if status_code_min is not None:
                sc["$gte"] = status_code_min
            if status_code_max is not None:
                sc["$lte"] = status_code_max
            q["response.status_code"] = sc

        # 排序：只允许已知字段，防止注入
        sort_field_map = {
            "created_at": "created_at",
            "updated_at": "updated_at",
            "name": "name",
            "status_code": "response.status_code",
        }
        mongo_sort_field = sort_field_map.get(sort_by, "created_at")
        direction = -1 if sort_order == "desc" else 1

        # find() 与 count_documents() 互不依赖，并行执行减少延迟
        docs_task = self.db["api_dsls"].find(q, {"_id": 0}).sort(mongo_sort_field, direction).skip(skip).limit(limit).to_list(limit)
        count_task = self.db["api_dsls"].count_documents(q)
        docs, total = await asyncio.gather(docs_task, count_task)
        await self._attach_quality_profiles(docs)
        return {"total": total, "items": docs}

    async def get_api(self, api_id: str) -> dict[str, Any]:
        """获取单个 API DSL 文档"""
        doc = await self.db["api_dsls"].find_one({"id": api_id}, {"_id": 0})
        if doc:
            await self._attach_quality_profiles([doc])
        return doc

    async def _attach_quality_profiles(self, docs: list[dict[str, Any]]) -> None:
        """为 API 查询结果追加质量评分，帮助前端直接展示测试资产缺口。"""
        if not docs:
            return

        api_ids = [d.get("id") for d in docs if d.get("id")]
        scenario_counts = {api_id: 0 for api_id in api_ids}
        if api_ids:
            cursor = self.db["scenarios"].aggregate([
                {"$match": {"steps.api_id": {"$in": api_ids}}},
                {"$unwind": "$steps"},
                {"$match": {"steps.api_id": {"$in": api_ids}}},
                {"$group": {"_id": {"api_id": "$steps.api_id", "scenario_id": "$id"}}},
                {"$group": {"_id": "$_id.api_id", "count": {"$sum": 1}}},
            ])
            async for item in cursor:
                scenario_counts[item["_id"]] = item.get("count", 0)

        for doc in docs:
            doc["quality"] = self._build_quality_profile(
                doc,
                scenario_count=scenario_counts.get(doc.get("id"), 0),
            )

    @staticmethod
    def _build_quality_profile(doc: dict[str, Any], scenario_count: int = 0) -> dict[str, Any]:
        """按文档、断言、场景、响应样本和 AI 状态计算 0-100 质量分。"""
        api_doc = doc.get("doc") or {}
        asserts = doc.get("asserts") or []
        response = doc.get("response") or {}
        analysis_status = doc.get("analysis_status") or "idle"

        suggestions: list[str] = []

        # 文档分支：解决接口说明不可读问题；按摘要、描述、参数、响应字段拆分，便于定位缺口。
        doc_score = 0
        if api_doc.get("summary"):
            doc_score += 8
        else:
            suggestions.append("add_summary")
        if api_doc.get("description"):
            doc_score += 6
        else:
            suggestions.append("add_description")
        if api_doc.get("params"):
            doc_score += 7
        else:
            suggestions.append("document_params")
        if api_doc.get("response_fields"):
            doc_score += 9
        else:
            suggestions.append("document_response_fields")

        # 断言分支：解决仅靠状态码无法发现业务回归的问题；业务断言越多，分值越高。
        assert_count = len(asserts)
        business_asserts = [
            a for a in asserts
            if (a.get("field") or "") not in ("status_code", "$response_time_ms")
        ]
        assert_score = min(18, assert_count * 4)
        if business_asserts:
            assert_score += min(12, len(business_asserts) * 3)
        else:
            suggestions.append("add_business_asserts")

        # 场景分支：解决单接口通过但业务链路缺测的问题；关联场景存在即可先给基础覆盖分。
        scenario_score = 10 if scenario_count > 0 else 0
        if scenario_count == 0:
            suggestions.append("generate_scenario")

        # 响应样本分支：解决没有可对比基线时无法生成可靠文档/断言的问题。
        response_score = 0
        if response.get("status_code"):
            response_score += 4
        else:
            suggestions.append("capture_status_code")
        if response.get("body") is not None:
            response_score += 6
        else:
            suggestions.append("capture_response_body")

        # AI 状态分支：解决资产未完成 AI 初始化的问题；失败时优先提示重试。
        analysis_applied = analysis_status in ("applied", "done")
        analysis_score = 20 if analysis_applied else 0
        if analysis_status == "failed":
            suggestions.append("retry_ai_analysis")
        elif analysis_status == "pending_review":
            suggestions.append("review_ai_generation")
        elif not analysis_applied:
            suggestions.append("run_ai_analysis")

        score = max(0, min(100, doc_score + assert_score + scenario_score + response_score + analysis_score))
        if score >= 85:
            risk_level = "low"
        elif score >= 65:
            risk_level = "medium"
        elif score >= 40:
            risk_level = "high"
        else:
            risk_level = "critical"

        # 去重并保留顺序，避免同一缺口在前端重复出现。
        deduped_suggestions = list(dict.fromkeys(suggestions))
        return {
            "score": score,
            "risk_level": risk_level,
            "scenario_count": scenario_count,
            "breakdown": {
                "doc": doc_score,
                "asserts": assert_score,
                "scenario": scenario_score,
                "response": response_score,
                "analysis": analysis_score,
            },
            "suggestions": deduped_suggestions,
        }

    async def update_api(self, api_id: str, data: dict[str, Any]) -> bool:
        """更新 API DSL；返回是否匹配到文档"""
        data.pop("id", None)
        data.pop("source_hash", None)
        data["updated_at"] = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        r = await self.db["api_dsls"].update_one({"id": api_id}, {"$set": data})
        return r.matched_count > 0

    async def create_api(self, api: ApiDSL) -> dict[str, Any]:
        """
        P1-5b: 手动新建 API DSL。
        此前只能通过 HAR/抓包/cURL 导入，无法从零手写接口定义。
        用途：对接未抓到流量的接口、Mock 接口、内部系统接口的文档化。
        返回创建后的 api dict（含生成的 id）。
        """
        import uuid
        now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        if not api.id:
            api.id = str(uuid.uuid4())
        api.created_at = now
        api.updated_at = now
        # 手动新建标记 source_har 为 manual，便于区分导入来源
        if not api.source_har:
            api.source_har = "manual"
        # 修复: 手动创建的 API 未计算 source_hash，导致 (project_id, source_hash) 唯一索引冲突
        # 基于 method + url（不含 query）+ body 结构生成去重指纹，与 har_parser/_make_hash 一致
        if not api.source_hash:
            parsed = urlparse(api.request.url)
            url_no_query = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            raw = f"{api.request.method.value}|{url_no_query}|{json.dumps(api.request.body, sort_keys=True, default=str)}"
            api.source_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]
        await self.db["api_dsls"].insert_one(api.model_dump())
        return api.model_dump()

    async def delete_api(self, api_id: str) -> bool:
        """删除单个 API DSL；返回是否成功删除"""
        r = await self.db["api_dsls"].delete_one({"id": api_id})
        return r.deleted_count > 0

    async def batch_delete_apis(self, ids: list[str]) -> int:
        """批量删除 API DSL；返回删除数量"""
        r = await self.db["api_dsls"].delete_many({"id": {"$in": ids}})
        return r.deleted_count

    # ── OpenAPI 导入/导出 ────────────────────────────────────

    async def import_openapi(self, spec: dict[str, Any], project_id: str, source_name: str = "openapi.json") -> dict[str, Any]:
        """
        P1-5: 从 OpenAPI/Swagger JSON 导入 API DSL。
        解决此前只能通过 HAR/抓包/cURL/手工创建，无法直接复用已有接口契约的问题。
        """
        if not isinstance(spec, dict):
            raise ValueError("OpenAPI spec must be a JSON object")
        if not spec.get("paths"):
            raise ValueError("OpenAPI spec must contain paths")

        server_url = self._resolve_openapi_server(spec)
        now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        dsls: list[ApiDSL] = []
        failed: list[dict[str, str]] = []
        methods = {m.value.lower() for m in HttpMethod}

        for path, path_item in (spec.get("paths") or {}).items():
            if not isinstance(path_item, dict):
                continue
            # path 级 parameters 会被每个 operation 继承，覆盖同名字段时以 operation 为准。
            path_parameters = path_item.get("parameters") if isinstance(path_item.get("parameters"), list) else []
            for method_name, operation in path_item.items():
                if method_name.lower() not in methods or not isinstance(operation, dict):
                    continue
                try:
                    api = self._openapi_operation_to_api(
                        spec=spec,
                        method=method_name.upper(),
                        path=path,
                        operation=operation,
                        path_parameters=path_parameters,
                        server_url=server_url,
                        source_name=source_name,
                        project_id=project_id,
                        now=now,
                    )
                    dsls.append(api)
                except Exception as exc:
                    # 单个 operation 失败不阻断整个文件导入，便于用户先落地可用接口。
                    failed.append({"method": method_name.upper(), "path": str(path), "error": str(exc)[:300]})

        if not dsls:
            raise ValueError("No valid operations found in OpenAPI spec")

        hashes = [api.source_hash for api in dsls if api.source_hash]
        existing = set(await self.db["api_dsls"].distinct("source_hash", {"project_id": project_id, "source_hash": {"$in": hashes}}))
        new_dsls = [api for api in dsls if api.source_hash not in existing]
        inserted_ids: list[str] = []
        if new_dsls:
            docs = [api.model_dump() for api in new_dsls]
            result = await self.db["api_dsls"].insert_many(docs, ordered=False)
            inserted_ids = [api.id for api in new_dsls[:len(result.inserted_ids)]]

        return {
            "status": "success",
            "project_id": project_id,
            "source_name": source_name,
            "total_operations": len(dsls),
            "new_apis": len(inserted_ids),
            "skipped_duplicates": len(dsls) - len(new_dsls),
            "failed_operations": failed,
            "api_ids": inserted_ids,
        }

    async def export_openapi(self, ids: list[str], project_id: str) -> dict[str, Any]:
        """
        P1-5: 将选中的 API DSL 导出为 OpenAPI 3.0 JSON。
        解决接口资产只能在平台内查看，无法交给研发/网关/文档系统复用的问题。
        """
        if not ids:
            raise ValueError("ids must be a non-empty array")
        docs = await self.db["api_dsls"].find(
            {"id": {"$in": ids}, "project_id": project_id},
            {"_id": 0},
        ).sort("updated_at", -1).to_list(length=len(ids))
        spec: dict[str, Any] = {
            "openapi": "3.0.3",
            "info": {"title": f"ApiPulse Export - {project_id}", "version": "1.0.0"},
            "paths": {},
        }
        for doc in docs:
            # 单条脏数据不应破坏整份导出；跳过无法反序列化的 API。
            try:
                api = ApiDSL(**doc)
            except Exception:
                logger.warning("Failed to parse ApiDSL during OpenAPI export, skipping doc id=%s", doc.get("id", "?"))
                continue
            path = api.request.path or urlparse(api.request.url).path or "/"
            method = api.request.method.value.lower()
            spec["paths"].setdefault(path, {})[method] = self._api_to_openapi_operation(api)
        return {"openapi": spec, "exported": len(docs), "requested": len(ids), "project_id": project_id}

    @staticmethod
    def _resolve_openapi_server(spec: dict[str, Any]) -> str:
        """解析 OpenAPI 3 servers 或 Swagger 2 host/basePath，生成导入时的默认 URL 前缀。"""
        servers = spec.get("servers") or []
        if servers and isinstance(servers[0], dict) and servers[0].get("url"):
            return str(servers[0]["url"]).rstrip("/")
        host = spec.get("host")
        if host:
            scheme = (spec.get("schemes") or ["https"])[0]
            base_path = str(spec.get("basePath") or "").rstrip("/")
            return f"{scheme}://{host}{base_path}".rstrip("/")
        return ""

    @classmethod
    def _openapi_operation_to_api(
        cls,
        *,
        spec: dict[str, Any],
        method: str,
        path: str,
        operation: dict[str, Any],
        path_parameters: list[dict[str, Any]],
        server_url: str,
        source_name: str,
        project_id: str,
        now: datetime,
    ) -> ApiDSL:
        """把一个 OpenAPI operation 转为平台内部 ApiDSL。"""
        parameters = cls._merge_openapi_parameters(path_parameters, operation.get("parameters"))
        query_params = cls._params_to_examples(spec, parameters, "query")
        headers = cls._params_to_examples(spec, parameters, "header")
        body, body_type, content_type = cls._request_body_from_operation(spec, operation, parameters)
        if content_type:
            headers.setdefault("Content-Type", content_type)

        url = f"{server_url}{path}" if server_url else path
        response_status, response_body, response_headers = cls._response_from_operation(spec, operation)
        doc_params = [
            ParamDoc(
                name=str(p.get("name", "")),
                location=str(p.get("in", "")),
                type=cls._schema_type(cls._resolve_ref(spec, p.get("schema") or {})),
                required=bool(p.get("required")),
                description=str(p.get("description") or ""),
                example=cls._schema_example(cls._resolve_ref(spec, p.get("schema") or {})),
            )
            for p in parameters
            if p.get("name") and p.get("in")
        ]
        api_doc = ApiDoc(
            summary=str(operation.get("summary") or operation.get("operationId") or f"{method} {path}"),
            description=str(operation.get("description") or ""),
            params=doc_params,
            tags=[str(t) for t in operation.get("tags", []) if t],
            generated_at=now,
        )
        source_hash = cls._make_openapi_hash(method, path, operation)
        return ApiDSL(
            id=str(uuid.uuid4()),
            name=api_doc.summary or f"{method} {path}",
            source_har=source_name or "openapi.json",
            source_hash=source_hash,
            request=RequestDSL(
                method=HttpMethod(method),
                url=url,
                path=path,
                query_params=query_params,
                headers={str(k): str(v) for k, v in headers.items()},
                body=body,
                body_type=body_type,
            ),
            response=ResponseDSL(status_code=response_status, headers=response_headers, body=response_body),
            doc=api_doc,
            parse_status=ParseStatus.SUCCESS,
            project_id=project_id,
            created_at=now,
            updated_at=now,
            tags=api_doc.tags,
        )

    @staticmethod
    def _merge_openapi_parameters(path_params: list[dict[str, Any]], op_params: Any) -> list[dict[str, Any]]:
        """合并 path/operation 参数，operation 同名参数覆盖 path 级定义。"""
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        for raw in [*path_params, *(op_params if isinstance(op_params, list) else [])]:
            if not isinstance(raw, dict):
                continue
            key = (str(raw.get("in", "")), str(raw.get("name", "")))
            if key[0] and key[1]:
                merged[key] = raw
        return list(merged.values())

    @classmethod
    def _params_to_examples(cls, spec: dict[str, Any], parameters: list[dict[str, Any]], location: str) -> dict[str, Any]:
        """将 OpenAPI 参数转为可执行 override 默认值。"""
        result: dict[str, Any] = {}
        for param in parameters:
            if param.get("in") != location or not param.get("name"):
                continue
            # 参数显式 example/default 优先，缺省时按 schema type 给一个稳定占位值。
            schema = cls._resolve_ref(spec, param.get("schema") or {})
            value = param.get("example", schema.get("default", cls._schema_example(schema)))
            result[str(param["name"])] = value
        return result

    @classmethod
    def _request_body_from_operation(cls, spec: dict[str, Any], operation: dict[str, Any], parameters: list[dict[str, Any]]) -> tuple[Any, str, str]:
        """解析 requestBody/body parameter，返回 body 示例、平台 body_type 与 Content-Type。"""
        body_parameter = next((p for p in parameters if isinstance(p, dict) and p.get("in") == "body"), None)
        if body_parameter:
            schema = cls._resolve_ref(spec, body_parameter.get("schema") or {})
            return cls._schema_example(schema), BodyType.JSON, "application/json"

        form_params = [p for p in parameters if isinstance(p, dict) and p.get("in") == "formData" and p.get("name")]
        if form_params:
            # Swagger 2.0 用 formData 表达表单/multipart，按 consumes 判断具体 body_type。
            body = {
                str(p["name"]): p.get("example", cls._schema_example(cls._resolve_ref(spec, p.get("schema") or {"type": p.get("type", "string")})))
                for p in form_params
            }
            consumes = [str(c).lower() for c in (operation.get("consumes") or spec.get("consumes") or [])]
            content_type = "multipart/form-data" if any("multipart" in c for c in consumes) else "application/x-www-form-urlencoded"
            return body, BodyType.MULTIPART if "multipart" in content_type else BodyType.FORM, content_type

        request_body = cls._resolve_ref(spec, operation.get("requestBody") or {})
        content = request_body.get("content") if isinstance(request_body, dict) else None
        if not isinstance(content, dict) or not content:
            # 无 requestBody 表示 GET/DELETE 等无请求体操作，保持 none。
            return None, BodyType.NONE, ""

        content_type = cls._pick_content_type(content)
        media = content.get(content_type) or {}
        schema = cls._resolve_ref(spec, media.get("schema") or {})
        example = cls._media_example(media, schema)
        if "multipart/form-data" in content_type:
            return example, BodyType.MULTIPART, content_type
        if "x-www-form-urlencoded" in content_type or "form" in content_type:
            return example, BodyType.FORM, content_type
        if "xml" in content_type:
            return example if isinstance(example, str) else "", BodyType.XML, content_type
        if "json" in content_type or content_type.endswith("+json"):
            return example, BodyType.JSON, content_type
        return example, BodyType.TEXT, content_type

    @classmethod
    def _response_from_operation(cls, spec: dict[str, Any], operation: dict[str, Any]) -> tuple[int, Any, dict[str, str]]:
        """从 OpenAPI responses 中提取首个 2xx 响应样例。"""
        responses = operation.get("responses") or {}
        if not isinstance(responses, dict) or not responses:
            return 0, None, {}
        status_key = next((k for k in responses if str(k).startswith("2")), next(iter(responses)))
        try:
            status_code = int(str(status_key))
        except ValueError:
            status_code = 200 if str(status_key).lower() == "default" else 0
        response = cls._resolve_ref(spec, responses.get(status_key) or {})
        headers = {
            str(name): str((h or {}).get("example", ""))
            for name, h in (response.get("headers") or {}).items()
            if isinstance(h, dict)
        }
        content = response.get("content") or {}
        if not isinstance(content, dict) or not content:
            schema = cls._resolve_ref(spec, response.get("schema") or {})
            # Swagger 2.0 响应体直接挂在 responses.xxx.schema 上。
            return status_code, cls._schema_example(schema) if schema else None, headers
        content_type = cls._pick_content_type(content)
        media = content.get(content_type) or {}
        schema = cls._resolve_ref(spec, media.get("schema") or {})
        return status_code, cls._media_example(media, schema), headers

    @staticmethod
    def _pick_content_type(content: dict[str, Any]) -> str:
        """优先选择 JSON，再回退到第一个媒体类型。"""
        for key in content:
            lowered = str(key).lower()
            if "json" in lowered or lowered.endswith("+json"):
                return key
        return next(iter(content))

    @classmethod
    def _media_example(cls, media: dict[str, Any], schema: dict[str, Any]) -> Any:
        """从 media.example/examples/schema 中生成稳定样例。"""
        if "example" in media:
            return media["example"]
        examples = media.get("examples")
        if isinstance(examples, dict) and examples:
            first = next(iter(examples.values()))
            if isinstance(first, dict) and "value" in first:
                return first["value"]
        return cls._schema_example(schema)

    @classmethod
    def _resolve_ref(cls, spec: dict[str, Any], node: Any) -> dict[str, Any]:
        """解析本地 $ref，避免引入额外依赖；无法解析时返回原节点。"""
        if not isinstance(node, dict):
            return {}
        ref = node.get("$ref")
        if not ref or not isinstance(ref, str) or not ref.startswith("#/"):
            return node
        target: Any = spec
        for part in ref[2:].split("/"):
            # JSON Pointer 中 ~1 表示 /，~0 表示 ~。
            key = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or key not in target:
                return node
            target = target[key]
        if isinstance(target, dict):
            merged = {k: v for k, v in node.items() if k != "$ref"}
            return {**target, **merged}
        return node

    _schema_visiting: set[int] = set()  # 深度限制防 $ref 循环（OpenAPI 自引用场景）

    @classmethod
    def _schema_example(cls, schema: dict[str, Any], spec: dict[str, Any] | None = None, depth: int = 0) -> Any:
        """按 JSON Schema/OpenAPI Schema 生成一个最小可读示例。"""
        if not isinstance(schema, dict) or depth > 10:
            return None
        # 解析嵌套 $ref（支持 properties/items/allOf 中的引用，带深度限制防循环）
        if "$ref" in schema and spec is not None:
            schema = cls._resolve_ref(spec, schema)
        if "example" in schema:
            return schema["example"]
        if "default" in schema:
            return schema["default"]
        if schema.get("enum"):
            return schema["enum"][0]
        if schema.get("allOf"):
            merged: dict[str, Any] = {}
            for part in schema.get("allOf") or []:
                value = cls._schema_example(part if isinstance(part, dict) else {}, spec, depth + 1)
                if isinstance(value, dict):
                    merged.update(value)
            return merged
        if schema.get("oneOf") or schema.get("anyOf"):
            options = schema.get("oneOf") or schema.get("anyOf") or []
            return cls._schema_example(options[0] if options else {}, spec, depth + 1)

        schema_type = schema.get("type")
        if not schema_type and schema.get("properties"):
            schema_type = "object"
        if schema_type == "object":
            return {str(k): cls._schema_example(v if isinstance(v, dict) else {}, spec, depth + 1) for k, v in (schema.get("properties") or {}).items()}
        if schema_type == "array":
            return [cls._schema_example(schema.get("items") if isinstance(schema.get("items"), dict) else {}, spec, depth + 1)]
        defaults = {
            "integer": 0,
            "number": 0,
            "boolean": False,
            "string": "",
            "null": None,
        }
        return defaults.get(str(schema_type), None)

    @staticmethod
    def _schema_type(schema: dict[str, Any]) -> str:
        """提取 schema 类型，供导入后的文档参数展示。"""
        if not isinstance(schema, dict):
            return "string"
        if schema.get("type"):
            return str(schema["type"])
        if schema.get("properties"):
            return "object"
        if schema.get("items"):
            return "array"
        return "string"

    @staticmethod
    def _make_openapi_hash(method: str, path: str, operation: dict[str, Any]) -> str:
        """为 OpenAPI operation 生成稳定指纹，用于重复导入去重。"""
        raw = json.dumps({
            "method": method.upper(),
            "path": path,
            "operationId": operation.get("operationId"),
            "parameters": operation.get("parameters", []),
            "requestBody": operation.get("requestBody", {}),
        }, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def _api_to_openapi_operation(cls, api: ApiDSL) -> dict[str, Any]:
        """将单个 ApiDSL 转成 OpenAPI operation。"""
        operation: dict[str, Any] = {
            "summary": api.doc.summary or api.name,
            "description": api.doc.description or "",
            "tags": api.tags or api.doc.tags or [],
            "parameters": [],
            "responses": {
                str(api.response.status_code or 200): {
                    "description": "Response",
                    "content": {
                        "application/json": {
                            "schema": cls._schema_from_value(api.response.body),
                            "example": api.response.body,
                        },
                    },
                },
            },
        }
        for name, value in (api.request.query_params or {}).items():
            operation["parameters"].append({
                "name": name,
                "in": "query",
                "required": False,
                "schema": cls._schema_from_value(value),
                "example": value,
            })
        for name, value in (api.request.headers or {}).items():
            if str(name).lower() in {"host", "content-length"}:
                continue
            operation["parameters"].append({
                "name": name,
                "in": "header",
                "required": False,
                "schema": {"type": "string"},
                "example": value,
            })
        if api.request.body is not None and api.request.body_type != BodyType.NONE:
            content_type = cls._content_type_for_body_type(str(api.request.body_type))
            operation["requestBody"] = {
                "required": False,
                "content": {
                    content_type: {
                        "schema": cls._schema_from_value(api.request.body),
                        "example": api.request.body,
                    },
                },
            }
        return operation

    @classmethod
    def _schema_from_value(cls, value: Any) -> dict[str, Any]:
        """从样例值推断 OpenAPI schema。"""
        if isinstance(value, dict):
            return {
                "type": "object",
                "properties": {str(k): cls._schema_from_value(v) for k, v in value.items()},
            }
        if isinstance(value, list):
            return {"type": "array", "items": cls._schema_from_value(value[0]) if value else {}}
        if isinstance(value, bool):
            return {"type": "boolean"}
        if isinstance(value, int):
            return {"type": "integer"}
        if isinstance(value, float):
            return {"type": "number"}
        if value is None:
            return {"nullable": True}
        return {"type": "string"}

    @staticmethod
    def _content_type_for_body_type(body_type: str) -> str:
        """平台 body_type 到 OpenAPI media type 的映射。"""
        mapping = {
            BodyType.JSON.value: "application/json",
            BodyType.FORM.value: "application/x-www-form-urlencoded",
            BodyType.MULTIPART.value: "multipart/form-data",
            BodyType.XML.value: "application/xml",
            BodyType.TEXT.value: "text/plain",
        }
        return mapping.get(str(body_type), "application/json")

    # ── 执行 ─────────────────────────────────────────────────

    async def run_single_api(
        self,
        api_id: str,
        redis,
        override_params: dict[str, Any] | None = None,
        override_headers: dict[str, Any] | None = None,
        environment_id: str = "",
        owner: str = "",
        client_ip: str = "",
    ) -> dict[str, Any]:
        """执行单个 API：加载环境 → 构建引擎 → 执行 → 返回 record dict"""
        doc = await self.db["api_dsls"].find_one({"id": api_id})
        if not doc:
            return None
        api = ApiDSL(**doc)

        env_headers: dict[str, str] = {}
        env_variables: dict[str, str] = {}
        if environment_id:
            env_doc = await self.db["environments"].find_one({"id": environment_id}, {"_id": 0})
            if not env_doc:
                return None  # 调用方应检查并抛 404
            env = Environment(**env_doc)
            # 环境级 base_url 优先级低于 API 自身的 base_url_override
            if env.base_url and not api.base_url_override:
                api.base_url_override = env.base_url
            env_headers = env.headers
            env_variables = env.variables

        engine = DagExecutionEngine(self.db, redis)
        record = await engine.run_single(
            api,
            override_params=override_params,
            override_headers=override_headers,
            owner=owner,
            env_headers=env_headers,
            env_variables=env_variables,
            client_ip=client_ip,
        )
        return record.model_dump(), record.id

    # ── AI 分析队列 ──────────────────────────────────────────

    async def _enqueue_ai_analysis(
        self,
        api_id: str,
        redis,
        *,
        queue_key: str,
        job_type: str,
        force: bool = False,
        skip_dedup: bool = False,
    ) -> bool:
        """
        统一 API AI 入队逻辑：更新 API 状态、推 Redis、持久化 ai_jobs 观测记录。

        skip_dedup=False（默认/自动触发）时：
          - 检查 API 是否已处于 queued/running 状态，避免重复入队
          - 检查最近是否已入队过（5 分钟内），避免频繁重复分析
        skip_dedup=True（手动点击）时：绕过去重检查，直接入队
        """
        # 调试日志：排查 find_one 在 auto-trigger 路径中返回 None 的根因
        logger.info(f"[_enqueue_ai_analysis] searching api_id={api_id!r} in api_dsls")
        doc = await self.db["api_dsls"].find_one({"id": api_id}, {"_id": 0, "project_id": 1, "analysis_status": 1, "analyzed_at": 1})
        if not doc:
            logger.warning(f"[_enqueue_ai_analysis] find_one returned None for api_id={api_id!r} — API not found in db")
            return False
        logger.info(f"[_enqueue_ai_analysis] found api_id={api_id!r}, project_id={doc.get('project_id')}")

        # ── 去重 & 过滤（仅自动触发生效，手动点击 skip_dedup=True 绕过）──
        if not skip_dedup:
            current_status = doc.get("analysis_status", "idle")
            # 去重：已处于排队或执行中，不重复入队
            if current_status in ("queued", "running"):
                logger.info(
                    "[_enqueue_ai_analysis] skip dedup: api_id=%s already has status=%s",
                    api_id, current_status,
                )
                return False
            # 过滤：最近 5 分钟内已完成分析，不重复入队（防止短时间内重复触发）
            analyzed_at = doc.get("analyzed_at")
            if analyzed_at:
                if isinstance(analyzed_at, str):
                    try:
                        analyzed_at = datetime.fromisoformat(analyzed_at)
                    except (ValueError, TypeError):
                        analyzed_at = None
                if analyzed_at and (datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None) - analyzed_at.replace(tzinfo=None)).total_seconds() < 300:
                    logger.info(
                        "[_enqueue_ai_analysis] skip filter: api_id=%s analyzed recently at %s",
                        api_id, analyzed_at,
                    )
                    return False

        project_id = doc.get("project_id", "default")
        now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        job_id = f"{job_type}:{api_id}:{uuid.uuid4().hex[:8]}"
        await self.db["api_dsls"].update_one(
            {"id": api_id},
            {"$set": {"analysis_status": "queued"}},
        )
        payload = {
            "api_id": api_id,
            "force": force,
            "project_id": project_id,
            "job_id": job_id,
            "status": "queued",
            "ts": now.isoformat(),
        }
        await redis.rpush(queue_key, json.dumps(payload, ensure_ascii=False, default=str))
        await AiJobService(self.db).mark_queued(
            job_id=job_id,
            type=job_type,
            project_id=project_id,
            source="api_service",
            target_ids=[api_id],
            queue_key=queue_key,
            payload=payload,
        )
        return True

    async def enqueue_analyze(self, api_id: str, redis, force: bool = False, skip_dedup: bool = False) -> bool:
        """将 API 标记为 queued 并推入 AI 分析队列（文档+断言）；skip_dedup=True 绕过自动去重，手动点击使用"""
        return await self._enqueue_ai_analysis(
            api_id, redis,
            queue_key="queue:ai_analyze",
            job_type="ai_analyze",
            force=force,
            skip_dedup=skip_dedup,
        )

    async def enqueue_analyze_doc(self, api_id: str, redis, force: bool = False, skip_dedup: bool = False) -> bool:
        """仅入队 AI 文档分析任务（queue:ai_analyze_doc），不生成断言；skip_dedup=True 绕过自动去重，手动点击使用"""
        return await self._enqueue_ai_analysis(
            api_id, redis,
            queue_key="queue:ai_analyze_doc",
            job_type="doc",
            force=force,
            skip_dedup=skip_dedup,
        )

    async def enqueue_analyze_asserts(self, api_id: str, redis, force: bool = False, skip_dedup: bool = False) -> bool:
        """仅入队 AI 断言分析任务（queue:ai_analyze_asserts），不生成文档；skip_dedup=True 绕过自动去重，手动点击使用"""
        return await self._enqueue_ai_analysis(
            api_id, redis,
            queue_key="queue:ai_analyze_asserts",
            job_type="asserts",
            force=force,
            skip_dedup=skip_dedup,
        )

    # ── 断言管理 ─────────────────────────────────────────────

    async def get_asserts(self, api_id: str) -> list[dict[str, Any]] | None:
        """获取 API 的断言列表；API 不存在返回 None"""
        doc = await self.db["api_dsls"].find_one({"id": api_id}, {"_id": 0, "asserts": 1})
        if not doc:
            return None
        return doc.get("asserts", [])

    async def replace_asserts(self, api_id: str, asserts: list[AssertRule]) -> bool:
        """替换 API 的全部断言；返回是否匹配到文档"""
        r = await self.db["api_dsls"].update_one(
            {"id": api_id},
            {"$set": {
                "asserts": [a.model_dump() for a in asserts],
                "updated_at": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None),
            }},
        )
        return r.matched_count > 0

    async def add_assert(self, api_id: str, rule: AssertRule) -> bool:
        """追加一条断言规则；返回是否匹配到文档"""
        r = await self.db["api_dsls"].update_one(
            {"id": api_id},
            {"$push": {"asserts": rule.model_dump()},
             "$set": {"updated_at": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)}},
        )
        return r.matched_count > 0

    # ── P2-4: Mock 生成 + 契约校验 ────────────────────────

    @staticmethod
    def generate_mock(api: ApiDSL, status_code: int | None = None) -> dict[str, Any]:
        """
        P2-4: 基于 API 的 doc.response_fields 生成类型正确的 Mock 响应。
        解决问题：前端联调时后端未就绪，需 Mock 数据；此前无此能力。

        生成策略：
        1. 优先用 doc.response_fields 的 example 值（若 AI 分析时已生成）
        2. 无 example 时按 type 生成合理默认值
        3. 支持嵌套字段（如 data.user.id 自动展开为 {data:{user:{id:...}}}）
        4. status_code 默认用 API 定义的，可覆盖
        """
        doc = api.doc
        body: Any = {}
        # 遍历 response_fields 构建响应体
        for field in (doc.response_fields or []):
            name = field.name
            # 解析嵌套路径（data.user.id → [data, user, id]）
            parts = name.replace("[0]", ".0").split(".")
            target = body
            for p in parts[:-1]:
                # 数组索引（[0] → 0）
                if p.isdigit():
                    # 当前层级应是 list，取索引元素（自动补 None）
                    idx = int(p)
                    if not isinstance(target, list):
                        continue
                    while len(target) <= idx:
                        target.append({})
                    target = target[idx]
                else:
                    if p not in target or not isinstance(target.get(p), dict):
                        target[p] = {}
                    target = target[p]
            # 叶子节点赋值
            last = parts[-1]
            value = ApiService._mock_value_for_field(field)
            if last.isdigit():
                idx = int(last)
                if isinstance(target, list):
                    while len(target) <= idx:
                        target.append(None)
                    target[idx] = value
            else:
                target[last] = value

        return {
            "status_code": status_code or api.response.status_code or 200,
            "headers": {"content-type": "application/json"},
            "body": body if body else {"message": "mock response"},
        }

    @staticmethod
    def _mock_value_for_field(field) -> Any:
        """根据 ParamDoc 的 type 和 example 生成 Mock 值。"""
        # 优先用 example（AI 分析时已生成代表性值）
        if field.example is not None:
            return field.example
        # 按 type 生成默认值
        t = (field.type or "string").lower()
        type_defaults = {
            "string": "", "str": "",
            "integer": 0, "int": 0,
            "number": 0.0, "float": 0.0,
            "boolean": False, "bool": False,
            "array": [], "list": [],
            "object": {}, "dict": {}, "null": None,
        }
        return type_defaults.get(t, "")

    @staticmethod
    def check_contract(actual_body: Any, api: ApiDSL) -> dict[str, Any]:
        """
        P2-4: 契约校验 —— 实际响应体 vs doc.response_fields 定义。
        区别于断言（断言是单点校验），契约校验是结构级整体比对：
        - 缺失字段：doc 定义了但响应没有（可能后端漏返）
        - 类型不匹配：doc 说 string 实际是 int
        - 多余字段：响应有但 doc 没记录（可能 doc 过时）

        返回 {passed, missing_fields, type_mismatches, extra_fields, summary}
        """
        doc_fields = {f.name: f for f in (api.doc.response_fields or [])}
        actual_flat = ApiService._flatten_body(actual_body)

        missing = []      # doc 定义但实际响应缺失
        mismatches = []   # 类型不匹配
        extra = []        # 实际有但 doc 未记录

        # 检查 doc 定义的字段
        for name, field in doc_fields.items():
            if name not in actual_flat:
                missing.append(name)
            else:
                actual_val = actual_flat[name]
                expected_type = (field.type or "").lower()
                if expected_type and not ApiService._type_matches(actual_val, expected_type):
                    mismatches.append({
                        "field": name,
                        "expected": expected_type,
                        "actual": type(actual_val).__name__,
                    })

        # 检查多余字段（实际有但 doc 没记录）
        for name in actual_flat:
            if name not in doc_fields:
                extra.append(name)

        passed = len(missing) == 0 and len(mismatches) == 0
        parts = []
        if missing:
            parts.append(f"缺失 {len(missing)} 个字段")
        if mismatches:
            parts.append(f"{len(mismatches)} 个类型不匹配")
        if extra:
            parts.append(f"{len(extra)} 个多余字段")
        summary = "；".join(parts) if parts else "契约校验通过"

        return {
            "passed": passed,
            "missing_fields": missing,
            "type_mismatches": mismatches,
            "extra_fields": extra,
            "summary": summary,
        }

    @staticmethod
    def _flatten_body(body: Any, prefix: str = "") -> dict[str, Any]:
        """将嵌套 body 扁平化为 {路径: 值}，便于与 doc.response_fields 比对。"""
        result: dict[str, Any] = {}
        if isinstance(body, dict):
            for k, v in body.items():
                path = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict):
                    result.update(ApiService._flatten_body(v, path))
                elif isinstance(v, list) and v and isinstance(v[0], dict):
                    # 数组取首元素展开（与 doc 的 [0] 约定一致）
                    result.update(ApiService._flatten_body(v[0], f"{path}[0]"))
                else:
                    result[path] = v
        return result

    @staticmethod
    def _type_matches(value: Any, expected_type: str) -> bool:
        """检查值是否匹配期望类型（兼容多种类型名）。"""
        type_map = {
            "string": (str,), "str": (str,),
            "integer": (int,), "int": (int,),
            "number": (int, float), "float": (float,),
            "boolean": (bool,), "bool": (bool,),
            "array": (list,), "list": (list,),
            "object": (dict,), "dict": (dict,),
            "null": (type(None),),
        }
        expected = type_map.get(expected_type.lower())
        if expected is None:
            return True  # 未知类型不校验
        # 注意：bool 是 int 子类，需排除（True 不应匹配 integer）
        if expected_type.lower() in ("integer", "int") and isinstance(value, bool):
            return False
        return isinstance(value, expected)
