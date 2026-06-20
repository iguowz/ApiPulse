"""
场景服务层 —— CRUD、执行、批量操作、导入
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import HTTPException
from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase

from ai_analyzer.analyzer import AI_SCENARIO_QUEUE
from dag_engine.engine import DagExecutionEngine, _ExecEnv
from models.dsl import Environment, ScenarioDSL
from services.ai_job_service import AiJobService


class ScenarioService:
    """场景业务逻辑，不依赖 HTTP 层"""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    def _now(self) -> datetime:
        return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)

    def _project_query(self, project_id: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        q = {"project_id": project_id}
        if extra:
            q.update(extra)
        return q

    # ── 查询 ─────────────────────────────────────────────────

    async def list_scenarios(
        self,
        project_id: str = "default",
        skip: int = 0,
        limit: int = 50,
        api_id: str = "",
        search: str = "",
        status: str = "",
        scenario_type: str = "",
    ) -> dict[str, Any]:
        """列表查询，支持按 api_id/search/status/scenario_type 筛选"""
        q: dict[str, Any] = {"project_id": project_id}
        if api_id:
            q["steps.api_id"] = api_id
        if search:
            q["$or"] = [
                {"name": {"$regex": search, "$options": "i"}},
                {"description": {"$regex": search, "$options": "i"}},
            ]
        if status:
            q["status"] = status
        # 场景类型筛选：以 scenario_type 优先，兼容旧数据按可执行 API 步骤数量/容器节点推断。
        type_filter: dict[str, Any] | None = None
        api_step_size = {"$size": {"$filter": {
            "input": "$steps", "as": "s",
            "cond": {"$not": [{"$in": ["$$s.type", ["start", "end", "condition", "loop"]]}]},
        }}}
        if scenario_type == "single":
            type_filter = {"$or": [
                {"scenario_type": "single"},
                {"$and": [
                    {"scenario_type": {"$in": ["", None]}},
                    {"$expr": {"$eq": [api_step_size, 1]}},
                ]},
            ]}
        elif scenario_type == "multi":
            type_filter = {"$or": [
                {"scenario_type": "multi"},
                {"$and": [
                    {"scenario_type": {"$in": ["", None]}},
                    {"steps.type": {"$nin": ["condition", "loop"]}},
                    {"$expr": {"$gt": [api_step_size, 1]}},
                ]},
            ]}
        elif scenario_type == "complex":
            type_filter = {"$or": [
                {"scenario_type": "complex"},
                {"steps.type": {"$in": ["condition", "loop"]}},
                {"$expr": {"$gte": [api_step_size, 5]}},
            ]}
        if type_filter:
            existing = dict(q)
            q = {"$and": [existing, type_filter]}
        docs = await self.db["scenarios"].find(q, {"_id": 0}).sort("updated_at", -1).skip(skip).limit(limit).to_list(limit)
        total = await self.db["scenarios"].count_documents(q)
        return {"total": total, "items": docs}

    async def get_scenario(self, scenario_id: str, project_id: str | None = None) -> dict[str, Any] | None:
        """获取单个场景"""
        q = {"id": scenario_id}
        if project_id:
            q["project_id"] = project_id
        return await self.db["scenarios"].find_one(q, {"_id": 0})

    # ── CRUD ─────────────────────────────────────────────────

    async def create_scenario(self, scenario: ScenarioDSL, project_id: str) -> ScenarioDSL:
        """创建场景，自动填充 id 和时间戳"""
        scenario.id = str(uuid.uuid4())
        scenario.project_id = project_id
        scenario.created_at = scenario.updated_at = self._now()
        await self.db["scenarios"].insert_one(scenario.model_dump())
        return scenario

    async def update_scenario(self, scenario_id: str, data: dict[str, Any], project_id: str) -> bool:
        """更新场景字段"""
        old = await self.get_scenario(scenario_id, project_id)
        if not old:
            return False
        await self.save_scenario_version(old, actor=data.pop("_actor", ""), reason=data.pop("_reason", "update"))
        data.pop("id", None)
        data.pop("project_id", None)
        data["updated_at"] = self._now()
        r = await self.db["scenarios"].update_one(self._project_query(project_id, {"id": scenario_id}), {"$set": data})
        return r.matched_count > 0

    async def save_scenario_version(self, scenario_doc: dict[str, Any], actor: str = "", reason: str = "update") -> str:
        """
        P2-1: 保存场景持久版本快照。
        用途：服务端保存前自动记录旧版本，解决前端撤销栈刷新后丢失、误保存无法回滚的问题。
        """
        scenario_id = scenario_doc.get("id", "")
        project_id = scenario_doc.get("project_id", "default")
        if not scenario_id:
            return ""
        latest = await self.db["scenario_versions"].find_one(
            {"scenario_id": scenario_id, "project_id": project_id},
            sort=[("version", -1)],
        )
        version = int((latest or {}).get("version", 0)) + 1
        snapshot = dict(scenario_doc)
        snapshot.pop("_id", None)
        version_id = str(uuid.uuid4())
        now = self._now()
        await self.db["scenario_versions"].insert_one({
            "id": version_id,
            "scenario_id": scenario_id,
            "project_id": project_id,
            "version": version,
            "name": snapshot.get("name", ""),
            "description": snapshot.get("description", ""),
            "steps_count": len(snapshot.get("steps") or []),
            "snapshot": snapshot,
            "actor": actor,
            "reason": reason,
            "created_at": now,
        })
        return version_id

    async def list_scenario_versions(self, scenario_id: str, project_id: str, limit: int = 50) -> dict[str, Any] | None:
        """列出指定场景的历史版本，不返回完整 snapshot，避免列表响应过大。"""
        current = await self.get_scenario(scenario_id, project_id)
        if not current:
            return None
        cursor = self.db["scenario_versions"].find(
            {"scenario_id": scenario_id, "project_id": project_id},
            {"_id": 0, "snapshot": 0},
        ).sort("version", -1).limit(limit)
        items = await cursor.to_list(limit)
        return {"items": items, "total": len(items), "scenario_id": scenario_id}

    async def restore_scenario_version(self, scenario_id: str, version_id: str, project_id: str, actor: str = "") -> dict[str, Any] | None:
        """
        将场景恢复到指定历史版本。
        恢复前先保存当前版本，确保回滚动作本身也可撤回。
        """
        current = await self.get_scenario(scenario_id, project_id)
        if not current:
            return None
        version_doc = await self.db["scenario_versions"].find_one(
            {"id": version_id, "scenario_id": scenario_id, "project_id": project_id},
            {"_id": 0},
        )
        if not version_doc:
            raise HTTPException(status_code=404, detail="Scenario version not found")
        await self.save_scenario_version(current, actor=actor, reason=f"restore:{version_doc.get('version')}")
        snapshot = dict(version_doc.get("snapshot") or {})
        snapshot.pop("_id", None)
        snapshot["id"] = scenario_id
        snapshot["project_id"] = project_id
        snapshot["updated_at"] = self._now()
        await self.db["scenarios"].replace_one(
            {"id": scenario_id, "project_id": project_id},
            snapshot,
        )
        return snapshot

    async def delete_scenario(self, scenario_id: str, project_id: str) -> bool:
        """删除单个场景"""
        r = await self.db["scenarios"].delete_one(self._project_query(project_id, {"id": scenario_id}))
        return r.deleted_count > 0

    async def batch_delete_scenarios(self, ids: list[str]) -> int:
        """批量删除场景；返回删除数量。调用方须预先校验每个 ID 的项目访问权限。"""
        r = await self.db["scenarios"].delete_many({"id": {"$in": ids}})
        return r.deleted_count

    # ── 校验与运行前规范化 ─────────────────────────────────

    def _step_type(self, step: dict[str, Any]) -> str:
        return str(step.get("type") or "api")

    def _api_steps(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [s for s in steps if self._step_type(s) == "api"]

    def _collect_refs(self, value: Any) -> list[tuple[str, str]]:
        refs: list[tuple[str, str]] = []
        if isinstance(value, str):
            import re
            refs.extend(re.findall(r"\{\{\s*([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)\s*\}\}", value))
        elif isinstance(value, dict):
            for v in value.values():
                refs.extend(self._collect_refs(v))
        elif isinstance(value, list):
            for v in value:
                refs.extend(self._collect_refs(v))
        return refs

    async def validate_scenario_doc(self, doc: dict[str, Any], project_id: str) -> dict[str, Any]:
        """校验场景 DSL。返回 issues，供保存/运行前定位问题。"""
        issues: list[dict[str, Any]] = []
        steps = doc.get("steps") or []
        if not isinstance(steps, list):
            return {"valid": False, "issues": [{
                "level": "error", "code": "steps_invalid", "step_id": "",
                "message": "steps must be an array", "action": "edit_steps",
            }]}

        all_ids: list[str] = []
        node_ids: set[str] = set()
        api_steps = self._api_steps(steps)
        api_step_ids = {s.get("step_id") for s in api_steps if s.get("step_id")}
        extract_map: dict[str, set[str]] = {}
        valid_types = {"start", "end", "api", "condition", "loop"}
        start_count = 0
        end_count = 0
        type_by_id: dict[str, str] = {}

        for step in steps:
            sid = str(step.get("step_id") or "")
            stype = self._step_type(step)
            if not sid:
                issues.append({"level": "error", "code": "missing_step_id", "step_id": "", "message": "步骤缺少 step_id", "action": "edit_step"})
                continue
            all_ids.append(sid)
            node_ids.add(sid)
            type_by_id[sid] = stype
            if stype not in valid_types:
                issues.append({"level": "error", "code": "invalid_step_type", "step_id": sid, "message": f"不支持的节点类型: {stype}", "action": "edit_step"})
            if stype == "start":
                start_count += 1
            if stype == "end":
                end_count += 1
            parent_id = step.get("parent_id") or ""
            if parent_id and parent_id == sid:
                issues.append({"level": "error", "code": "invalid_parent", "step_id": sid, "message": "节点不能以自身作为父节点", "action": "fix_dependency"})
            if stype == "api":
                if not step.get("api_id"):
                    issues.append({"level": "error", "code": "empty_api", "step_id": sid, "message": f"步骤 {sid} 未选择 API", "action": "select_api"})
                extract_map[sid] = set((step.get("extract") or {}).keys())
                for name, path in (step.get("extract") or {}).items():
                    if not isinstance(path, str) or not path.startswith("$."):
                        issues.append({"level": "warning", "code": "invalid_jsonpath", "step_id": sid, "message": f"提取变量 {name} 的 JSONPath 无效", "action": "edit_extract"})
            if stype == "condition" and not step.get("condition"):
                issues.append({"level": "error", "code": "missing_condition", "step_id": sid, "message": "条件节点缺少 condition 配置", "action": "edit_condition"})
            if stype == "loop":
                loop = step.get("loop") or {}
                if not loop and not step.get("loop_var") and not step.get("loop_count"):
                    issues.append({"level": "error", "code": "missing_loop", "step_id": sid, "message": "循环节点缺少 loop 配置", "action": "edit_loop"})

        if start_count != 1:
            issues.append({"level": "error", "code": "invalid_start", "step_id": "start", "message": "场景必须且只能有一个 start 节点", "action": "fix_start_end"})
        if end_count != 1:
            issues.append({"level": "error", "code": "invalid_end", "step_id": "end", "message": "场景必须且只能有一个 end 节点", "action": "fix_start_end"})

        for step in steps:
            parent_id = step.get("parent_id") or ""
            if parent_id and parent_id not in node_ids:
                issues.append({"level": "error", "code": "unknown_parent", "step_id": step.get("step_id", ""), "message": f"父容器 {parent_id} 不存在", "action": "fix_dependency"})
            elif parent_id and type_by_id.get(parent_id) not in {"condition", "loop"}:
                issues.append({"level": "error", "code": "invalid_parent_type", "step_id": step.get("step_id", ""), "message": f"父节点 {parent_id} 不是容器节点", "action": "fix_dependency"})
            sid = step.get("step_id", "")
            for dep in step.get("depends_on") or []:
                if dep in ("start", "end"):
                    continue
                if dep not in node_ids:
                    issues.append({"level": "error", "code": "unknown_dependency", "step_id": sid, "message": f"依赖步骤 {dep} 不存在", "action": "fix_dependency"})

        seen: set[str] = set()
        for sid in all_ids:
            if sid in seen:
                issues.append({"level": "error", "code": "duplicate_step_id", "step_id": sid, "message": f"重复的 step_id: {sid}", "action": "rename_step"})
            seen.add(sid)

        api_ids = [s.get("api_id") for s in api_steps if s.get("api_id")]
        if api_ids:
            docs = await self.db["api_dsls"].find({"id": {"$in": api_ids}, "project_id": project_id}, {"id": 1}).to_list(length=len(api_ids))
            valid_api_ids = {d["id"] for d in docs}
            for step in api_steps:
                api_id = step.get("api_id")
                if api_id and api_id not in valid_api_ids:
                    issues.append({"level": "error", "code": "api_not_in_project", "step_id": step.get("step_id", ""), "message": "步骤引用的 API 不存在或不属于当前项目", "action": "select_api"})

        executable_ids = {s.get("step_id") for s in steps if self._step_type(s) not in {"start", "end"} and s.get("step_id")}
        adj = {
            s.get("step_id"): [d for d in (s.get("depends_on") or []) if d in executable_ids]
            for s in steps if s.get("step_id") in executable_ids
        }
        visiting: set[str] = set()
        visited: set[str] = set()

        def dfs(sid: str) -> bool:
            if sid in visiting:
                return True
            if sid in visited:
                return False
            visiting.add(sid)
            for dep in adj.get(sid, []):
                if dfs(dep):
                    return True
            visiting.remove(sid)
            visited.add(sid)
            return False

        for sid in list(adj):
            if dfs(sid):
                issues.append({"level": "error", "code": "cycle_dependency", "step_id": sid, "message": "步骤依赖存在循环", "action": "fix_dependency"})
                break

        for step in api_steps:
            sid = step.get("step_id", "")
            for ref_step, ref_var in self._collect_refs({
                "override_params": step.get("override_params") or {},
                "override_headers": step.get("override_headers") or {},
                "assertions": step.get("assertions") or [],
            }):
                if ref_step in {"env", "loop", "start"}:
                    continue
                if ref_step not in extract_map or ref_var not in extract_map.get(ref_step, set()):
                    issues.append({"level": "warning", "code": "unknown_variable_ref", "step_id": sid, "message": f"引用变量 {{{{{ref_step}.{ref_var}}}}} 未在来源步骤 extract 中定义", "action": "fix_extract"})

        return {"valid": not any(i["level"] == "error" for i in issues), "issues": issues}

    async def validate_scenario(self, scenario_id: str, project_id: str) -> dict[str, Any] | None:
        doc = await self.get_scenario(scenario_id, project_id)
        if not doc:
            return None
        return await self.validate_scenario_doc(doc, project_id)

    async def _load_project_environment(self, environment_id: str, project_id: str) -> tuple[str, dict[str, str], dict[str, str]]:
        if not environment_id:
            return "", {}, {}
        env_doc = await self.db["environments"].find_one({"id": environment_id, "project_id": project_id}, {"_id": 0})
        if not env_doc:
            raise HTTPException(status_code=404, detail="Environment not found")
        env = Environment(**env_doc)
        return env.base_url, env.headers, env.variables

    async def preview_step_request(
        self,
        scenario_id: str,
        step_data: dict[str, Any],
        project_id: str,
        context: dict[str, Any] | None = None,
        environment_id: str = "",
    ) -> dict[str, Any]:
        """使用 DAG 引擎同一套组装逻辑预览单步最终请求，保存/执行前定位变量和鉴权问题。"""
        scenario = await self.get_scenario(scenario_id, project_id)
        if not scenario:
            raise HTTPException(status_code=404, detail="Scenario not found")
        step = ScenarioDSL(**{**scenario, "steps": [step_data]}).steps[0]
        if not step.api_id:
            raise HTTPException(status_code=422, detail="api_id is required")
        api_doc = await self.db["api_dsls"].find_one({"id": step.api_id, "project_id": project_id}, {"_id": 0})
        if not api_doc:
            raise HTTPException(status_code=404, detail="API not found")
        base_url, headers, variables = await self._load_project_environment(environment_id, project_id)
        from models.dsl import ApiDSL
        engine = DagExecutionEngine(self.db)
        api = ApiDSL(**api_doc)
        if base_url and not api.base_url_override:
            api.base_url_override = base_url
        return engine.preview_step_request(
            step,
            api,
            context=context or {},
            env=_ExecEnv(base_url=base_url, headers=headers, variables=variables),
        )

    # ── 执行 ─────────────────────────────────────────────────

    async def run_scenario(
        self,
        scenario_id: str,
        redis,
        initial_context: dict[str, Any] | None = None,
        environment_id: str = "",
        owner: str = "",
        client_ip: str = "",
        ws_manager=None,
        exec_id: str = "",
        project_id: str = "",
    ) -> dict[str, Any]:
        """执行单个场景：加载环境 → 引擎执行 → 返回 record dict
        P0-2: ws_manager/exec_id 透传给 engine，支持执行中逐步骤 WS 广播进度"""
        q = {"id": scenario_id}
        if project_id:
            q["project_id"] = project_id
        doc = await self.db["scenarios"].find_one(q)
        if not doc:
            return None

        validation = await self.validate_scenario_doc(doc, doc.get("project_id", project_id or "default"))
        if not validation.get("valid"):
            raise HTTPException(status_code=422, detail=validation)

        env_base_url, env_headers, env_variables = await self._load_project_environment(
            environment_id, doc.get("project_id", project_id or "default")
        )

        scenario = ScenarioDSL(**doc)
        # P0-1: 执行前更新场景状态为 running（前端状态筛选器和列表 tag 据此显示）
        await self.db["scenarios"].update_one(
            {"id": scenario_id}, {"$set": {"status": "running", "updated_at": self._now()}}
        )
        engine = DagExecutionEngine(self.db, redis, ws_manager=ws_manager)
        record = await engine.run_scenario(
            scenario,
            initial_context=initial_context,
            owner=owner,
            env_base_url=env_base_url,
            env_headers=env_headers,
            env_variables=env_variables,
            client_ip=client_ip,
            exec_id=exec_id,
        )
        # P0-1: 执行完成后更新场景状态（done/failed），让列表筛选器能区分已执行/失败场景
        final_status = "done" if record.passed else "failed"
        await self.db["scenarios"].update_one(
            {"id": scenario_id}, {"$set": {"status": final_status, "updated_at": self._now()}}
        )
        return record

    async def batch_run_scenarios(self, ids: list[str], redis, environment_id: str = "", owner: str = "", client_ip: str = "", project_id: str = "", ws_manager=None, job_id: str = "") -> list[dict[str, Any]]:
        """批量执行场景（P1-10: 并发执行，带 semaphore 限流），返回每个场景的执行结果摘要
        此前是串行 for 循环，N 个场景 = N 倍耗时；改为 asyncio.gather 并发（限流 5 防止过载）"""
        import asyncio
        env_base_url, env_headers, env_variables = await self._load_project_environment(environment_id, project_id)

        # P1-10: semaphore 限流 5，避免大量场景并发压垮下游服务
        sem = asyncio.Semaphore(5)

        async def run_one(sid: str) -> dict[str, Any]:
            async with sem:
                if ws_manager and job_id:
                    await ws_manager.broadcast(f"ai_analysis:{project_id}", {"type": "scenario_batch_progress", "project_id": project_id, "job_id": job_id, "scenario_id": sid, "status": "running"})
                doc = await self.db["scenarios"].find_one({"id": sid, "project_id": project_id})
                if not doc:
                    return {"scenario_id": sid, "error": "Scenario not found"}
                try:
                    validation = await self.validate_scenario_doc(doc, project_id)
                    if not validation.get("valid"):
                        return {"scenario_id": sid, "error": "Validation failed", "issues": validation.get("issues", [])}
                    scenario = ScenarioDSL(**doc)
                    # 更新状态为 running
                    await self.db["scenarios"].update_one(
                        {"id": sid}, {"$set": {"status": "running", "updated_at": self._now()}}
                    )
                    engine = DagExecutionEngine(self.db, redis)
                    record = await engine.run_scenario(
                        scenario, trigger="manual", owner=owner,
                        env_base_url=env_base_url, env_headers=env_headers, env_variables=env_variables,
                        client_ip=client_ip,
                    )
                    final_status = "done" if record.passed else "failed"
                    await self.db["scenarios"].update_one(
                        {"id": sid}, {"$set": {"status": final_status, "updated_at": self._now()}}
                    )
                    result = {
                        "scenario_id": sid,
                        "passed": record.passed,
                        "duration_ms": record.duration_ms,
                        "steps": len(record.steps),
                        "failure_reason": record.failure_reason,
                        "exec_id": record.id,
                    }
                    if ws_manager and job_id:
                        await ws_manager.broadcast(f"ai_analysis:{project_id}", {"type": "scenario_batch_progress", "project_id": project_id, "job_id": job_id, "scenario_id": sid, "status": "done" if record.passed else "failed", "result": result})
                    return result
                except Exception as e:
                    await self.db["scenarios"].update_one(
                        {"id": sid, "project_id": project_id}, {"$set": {"status": "failed", "updated_at": self._now()}}
                    )
                    if ws_manager and job_id:
                        await ws_manager.broadcast(f"ai_analysis:{project_id}", {"type": "scenario_batch_progress", "project_id": project_id, "job_id": job_id, "scenario_id": sid, "status": "failed", "error": str(e)[:500]})
                    return {"scenario_id": sid, "error": str(e)}

        # 并发执行所有场景
        results = await asyncio.gather(*[run_one(sid) for sid in ids], return_exceptions=False)
        return list(results)

    async def enqueue_batch_run(self, ids: list[str], redis, project_id: str, environment_id: str = "", owner: str = "", client_ip: str = "", ws_manager=None) -> dict[str, Any]:
        """异步批量运行：立即返回 job_id，后台通过项目隔离频道广播逐条进度。"""
        import asyncio
        job_id = str(uuid.uuid4())

        async def _run():
            try:
                if ws_manager:
                    await ws_manager.broadcast(f"ai_analysis:{project_id}", {"type": "scenario_batch", "project_id": project_id, "job_id": job_id, "status": "running", "total": len(ids)})
                results = await self.batch_run_scenarios(ids, redis, environment_id, owner, client_ip, project_id, ws_manager, job_id)
                if ws_manager:
                    await ws_manager.broadcast(f"ai_analysis:{project_id}", {"type": "scenario_batch", "project_id": project_id, "job_id": job_id, "status": "done", "total": len(ids), "results": results})
            except Exception as e:
                if ws_manager:
                    await ws_manager.broadcast(f"ai_analysis:{project_id}", {"type": "scenario_batch", "project_id": project_id, "job_id": job_id, "status": "failed", "error": str(e)[:500]})

        asyncio.create_task(_run())
        return {"job_id": job_id, "total": len(ids), "project_id": project_id}

    # ── 导入 ─────────────────────────────────────────────────

    async def import_scenarios(self, items: list[dict[str, Any]], project_id: str) -> dict[str, int]:
        """批量导入场景（JSON 数组）；返回成功导入数量"""
        imported = 0
        failed = 0
        now = self._now()
        for item in items:
            item.pop("_id", None)
            item["id"] = item.get("id") or str(uuid.uuid4())
            item["project_id"] = project_id
            item["created_at"] = now
            item["updated_at"] = now
            try:
                scenario = ScenarioDSL(**item)
                await self.db["scenarios"].insert_one(scenario.model_dump())
                imported += 1
            except Exception as e:
                failed += 1
                logger.warning("Import scenario failed: {}", e)
        return {"imported": imported, "failed": failed, "skipped": 0}

    # ── AI 生成队列 ──────────────────────────────────────────

    async def enqueue_generate(self, api_ids: list[str], project_id: str, redis, scenario_type: str = "", user_id: str = "") -> dict[str, Any]:
        """将场景生成任务推入 Redis 队列，携带 scenario_type 控制生成策略"""
        if scenario_type not in ("", "single", "multi", "complex"):
            raise HTTPException(status_code=400, detail="Unsupported scenario_type")
        valid_docs = await self.db["api_dsls"].find({"id": {"$in": api_ids}, "project_id": project_id}, {"id": 1}).to_list(length=len(api_ids))
        valid_api_ids = [d["id"] for d in valid_docs]
        if not api_ids or len(valid_api_ids) != len(set(api_ids)):
            raise HTTPException(status_code=403, detail="Some APIs are not accessible in this project")
        job_id = str(uuid.uuid4())
        payload = {"api_ids": api_ids, "project_id": project_id, "scenario_type": scenario_type, "job_id": job_id, "user_id": user_id}
        await redis.rpush(
            AI_SCENARIO_QUEUE,
            json.dumps(payload, ensure_ascii=False),
        )
        await AiJobService(self.db).mark_queued(
            job_id=job_id,
            type="scenario",
            project_id=project_id,
            source="scenario_service",
            target_ids=api_ids,
            queue_key=AI_SCENARIO_QUEUE,
            payload=payload,
            user_id=user_id,
        )
        return {"queued": True, "job_id": job_id, "project_id": project_id}
