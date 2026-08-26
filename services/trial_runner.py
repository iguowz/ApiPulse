"""
Gate3 试跑执行器（Q2/B2）——把生成的场景在真实引擎里跑一遍，用「实际通过 + 覆盖率」自证。

安全护栏：
  - 默认只试跑只读方法(GET/HEAD)的场景；含写操作(POST/PUT/PATCH/DELETE)默认跳过，除非 trial_allow_write。
  - 任何试跑异常都不向上抛出（记录 skipped/error，不阻塞生成与审阅）。
"""
from __future__ import annotations

import asyncio

from bson import ObjectId

from config.settings import get_settings
from models.dsl import ScenarioDSL, ScenarioStep


async def trial_run_generation(analyzer, generation_id: str) -> bool:
    """对某 GenerationVersion 试跑，结果写回该记录的 trial_run 字段。

    返回 True 表示完成了一次试跑（不管通过与否）；False 表示无法试跑（跳过/无步骤/写操作被护栏拦截）。
    """
    s = get_settings()
    gen_col = getattr(analyzer, "_generation_col", None)
    if gen_col is None:
        return False

    # 支持 ObjectId 或字符串 id
    gv = None
    try:
        gv = await gen_col.find_one({"_id": ObjectId(generation_id)})
    except Exception:
        pass
    if gv is None:
        gv = await gen_col.find_one({"id": generation_id})
    if gv is None:
        return False

    content = gv.get("content") or {}
    raw_steps = content.get("steps") or []
    if not isinstance(raw_steps, list) or not raw_steps:
        return False

    # 构建 ScenarioStep
    try:
        steps = [ScenarioStep(**x) for x in raw_steps if isinstance(x, dict)]
    except Exception:
        return False
    if not steps:
        return False

    # ── 护栏：读取 API 方法，写操作默认拒绝 ──
    api_ids = [st.api_id for st in steps if st.api_id]
    methods: set[str] = set()
    try:
        if api_ids:
            docs = await getattr(analyzer, "_api_col", None).find(
                {"id": {"$in": api_ids}}, {"_id": 0, "request.method": 1}
            ).to_list(length=100)
            for d in docs:
                req = d.get("request") or {}
                if req.get("method"):
                    methods.add(str(req["method"]).upper())
    except Exception:
        pass
    write = {"POST", "PUT", "PATCH", "DELETE"}
    if not s.trial_allow_write and (methods & write):
        await _mark_trial(gen_col, gv, {"passed": False, "coverage": 0.0,
                                        "skipped": "write_operation", "step_failures": []})
        return False

    # ── 执行 ──
    scenario = ScenarioDSL(
        id=gv.get("id") or str(ObjectId()), name=content.get("name") or "",
        description=content.get("description") or "", steps=steps,
        project_id=gv.get("project_id") or "default", ai_generated=True,
    )
    result: dict = {"passed": False, "coverage": 0.0, "step_failures": []}
    try:
        from dag_engine.engine import DagExecutionEngine
        engine = DagExecutionEngine(getattr(analyzer, "_db", None), getattr(analyzer, "_redis", None))
        # 试跑加超时保护（trial_timeout_s），避免下游慢/挂起拖住试跑流程
        record = await asyncio.wait_for(
            engine.run_scenario(scenario, trigger="trial", owner="system"),
            timeout=s.trial_timeout_s or 60,
        )
        step_results = record.steps or []
        total = len(step_results)
        passed = sum(1 for x in step_results if getattr(x, "passed", False))
        result = {
            "passed": bool(record.passed),
            "coverage": round(passed / total, 3) if total else 0.0,
            "step_count": total,
            "step_failures": [getattr(x, "step_id", "") for x in step_results if not getattr(x, "passed", False)],
        }
    except Exception as e:
        result = {"passed": False, "coverage": 0.0, "skipped": "exec_error", "error": str(e)[:200], "step_failures": []}

    await _mark_trial(gen_col, gv, result)
    return True


async def _mark_trial(gen_col, gv, trial_result: dict) -> None:
    try:
        await gen_col.update_one(
            {"_id": gv["_id"]},
            {"$set": {"trial_run": trial_result, "updated_at": _now()}},
        )
    except Exception:
        pass


def _now():
    from datetime import datetime, timezone, timedelta
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
