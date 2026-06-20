"""
执行记录路由
"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.database import get_db
from api.deps import ensure_project_access, get_current_user, visible_project_id

router = APIRouter(tags=["Executions"])


@router.get("/executions")
async def list_executions(
    project_id: str | None = None,
    api_id: str | None = None,
    scenario_id: str | None = None,
    trigger: str | None = None,
    executor: str | None = None,
    keyword: str | None = None,
    passed: bool | None = None,
    skip: int = 0,
    limit: int = 20,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    q: dict[str, Any] = {}
    q["project_id"] = visible_project_id(current_user, project_id)
    if api_id:
        q["$or"] = [{"api_id": api_id}, {"steps.api_id": api_id}]
    if scenario_id:
        q["scenario_id"] = scenario_id
    if trigger:
        q["trigger"] = trigger
    if executor:
        q["executor"] = {"$regex": executor, "$options": "i"}  # 执行人模糊匹配（大小写不敏感）
    if passed is not None:
        q["passed"] = passed
    # 关键字搜索：在 id/api_id/scenario_id/executor 字段中模糊匹配
    if keyword:
        regex = {"$regex": keyword, "$options": "i"}
        keyword_or = [
            {"id": regex},
            {"api_id": regex},
            {"scenario_id": regex},
            {"executor": regex},
        ]
        if "$or" in q:
            q["$and"] = [{"$or": q.pop("$or")}, {"$or": keyword_or}]
        else:
            q["$or"] = keyword_or
    docs = await db["executions"].find(q, {"_id": 0}).sort("started_at", -1).skip(skip).limit(limit).to_list(limit)
    total = await db["executions"].count_documents(q)
    return {"total": total, "items": docs}


@router.get("/executions/{execution_id}")
async def get_execution(
    execution_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    doc = await db["executions"].find_one({"id": execution_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Execution not found")
    ensure_project_access(current_user, doc.get("project_id"))
    links = await db["diagnosis_diff_links"].find(
        {"execution_id": execution_id},
        {"_id": 0},
    ).sort("created_at", -1).limit(10).to_list(10)
    doc["diagnosis_links"] = links
    return doc


# ── CSV 导出：将执行记录导出为 CSV 文件下载 ──
# 解决审计/合规场景需要数据导出能力（需求 3.4 导出与合规）
@router.get("/executions/export/csv")
async def export_executions_csv(
    project_id: str | None = None,
    api_id: str | None = None,
    scenario_id: str | None = None,
    trigger: str | None = None,
    executor: str | None = None,
    keyword: str | None = None,
    passed: bool | None = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    # 复用与 list_executions 相同的筛选逻辑，导出全量匹配记录
    q: dict[str, Any] = {}
    q["project_id"] = visible_project_id(current_user, project_id)
    if api_id:
        q["$or"] = [{"api_id": api_id}, {"steps.api_id": api_id}]
    if scenario_id:
        q["scenario_id"] = scenario_id
    if trigger:
        q["trigger"] = trigger
    if executor:
        q["executor"] = {"$regex": executor, "$options": "i"}
    if passed is not None:
        q["passed"] = passed
    if keyword:
        regex = {"$regex": keyword, "$options": "i"}
        keyword_or = [
            {"id": regex},
            {"api_id": regex},
            {"scenario_id": regex},
            {"executor": regex},
        ]
        if "$or" in q:
            q["$and"] = [{"$or": q.pop("$or")}, {"$or": keyword_or}]
        else:
            q["$or"] = keyword_or

    # 限制最多导出 10000 条，防止内存溢出（to_list(None) 在生产环境可能 OOM）
    docs = await db["executions"].find(q, {"_id": 0}).sort("started_at", -1).to_list(10000)

    # 生成 CSV：使用内存缓冲区避免写临时文件
    output = io.StringIO()
    writer = csv.writer(output)
    # CSV 表头
    writer.writerow([
        "ID", "Type", "Trigger", "Executor", "API ID", "Scenario ID", "Monitor ID",
        "Steps", "Duration (ms)", "Passed", "Failure Reason", "Started At"
    ])
    # CSV 数据行
    for doc in docs:
        writer.writerow([
            doc.get("id", ""),
            doc.get("type", ""),
            doc.get("trigger", ""),
            doc.get("executor", ""),
            doc.get("api_id", ""),
            doc.get("scenario_id", ""),
            doc.get("monitor_id", ""),
            len(doc.get("steps") or []),
            doc.get("duration_ms", ""),
            "PASS" if doc.get("passed") else "FAIL",
            (doc.get("failure_reason") or "").replace("\n", " ")[:500],  # 截断长文本，去换行
            doc.get("started_at", ""),
        ])

    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),  # BOM 使 Excel 正确识别 UTF-8
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=executions.csv"},
    )


# ── Markdown 测试报告：为执行记录生成可下载的测试报告 ──
# 需求 1.5 测试报告：通过率、失败步骤详情、响应时间分布
@router.get("/executions/{execution_id}/report")
async def download_execution_report(
    execution_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    doc = await db["executions"].find_one({"id": execution_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Execution not found")
    ensure_project_access(current_user, doc.get("project_id"))

    steps = doc.get("steps") or []
    total_steps = len(steps)
    passed_steps = sum(1 for s in steps if s.get("passed"))
    failed_steps = sum(1 for s in steps if not s.get("skipped") and not s.get("passed"))
    skipped_steps = sum(1 for s in steps if s.get("skipped"))
    # 响应时间统计（仅非跳过步骤）
    latencies = [s.get("latency_ms", 0) for s in steps if not s.get("skipped") and s.get("latency_ms") is not None]
    # 断言统计
    total_asserts = sum(len(s.get("assert_results") or []) for s in steps)
    passed_asserts = sum(
        sum(1 for a in (s.get("assert_results") or []) if a.get("passed"))
        for s in steps
    )

    # 构建 Markdown 报告
    lines = [
        f"# 测试执行报告",
        f"",
        f"## 概览",
        f"",
        f"| 项目 | 值 |",
        f"|------|-----|",
        f"| 执行ID | `{doc.get('id', 'N/A')}` |",
        f"| 类型 | {doc.get('type', 'N/A')} |",
        f"| 触发方式 | {doc.get('trigger', 'N/A')} |",
        f"| 结果 | {'✅ 通过' if doc.get('passed') else '❌ 失败'} |",
        f"| 总步骤数 | {total_steps} |",
        f"| 通过步骤 | {passed_steps} |",
        f"| 失败步骤 | {failed_steps} |",
        f"| 跳过步骤 | {skipped_steps} |",
        f"| 通过率 | {passed_steps / total_steps * 100:.1f}% |" if total_steps > 0 else "| 通过率 | N/A |",
        f"| 总耗时 | {doc.get('duration_ms', 0)}ms |",
        f"| 开始时间 | {doc.get('started_at', 'N/A')} |",
        f"| 结束时间 | {doc.get('finished_at', 'N/A')} |",
        f"",
    ]

    # 失败原因
    if doc.get("failure_reason"):
        lines += [
            f"## 失败原因",
            f"",
            f"> {doc['failure_reason']}",
            f"",
        ]

    # 响应时间分布
    if latencies:
        latencies_sorted = sorted(latencies)
        avg = sum(latencies) / len(latencies)
        median = latencies_sorted[len(latencies_sorted) // 2]
        lines += [
            f"## 响应时间分布",
            f"",
            f"| 指标 | 值 |",
            f"|------|-----|",
            f"| 最小 | {min(latencies)}ms |",
            f"| 最大 | {max(latencies)}ms |",
            f"| 平均 | {avg:.1f}ms |",
            f"| 中位数(P50) | {median}ms |",
            f"| P95 | {latencies_sorted[int(len(latencies_sorted) * 0.95)] if len(latencies_sorted) > 1 else latencies_sorted[0]}ms |",
            f"| P99 | {latencies_sorted[int(len(latencies_sorted) * 0.99)] if len(latencies_sorted) > 1 else latencies_sorted[0]}ms |",
            f"",
        ]

    # 断言统计
    lines += [
        f"## 断言统计",
        f"",
        f"| 项目 | 值 |",
        f"|------|-----|",
        f"| 断言总数 | {total_asserts} |",
        f"| 通过断言 | {passed_asserts} |",
        f"| 失败断言 | {total_asserts - passed_asserts} |",
        f"| 断言通过率 | {passed_asserts / total_asserts * 100:.1f}% |" if total_asserts > 0 else "| 断言通过率 | N/A |",
        f"",
    ]

    # 步骤详情表
    lines += [
        f"## 步骤详情",
        f"",
        f"| # | 步骤ID | 名称 | 状态 | 耗时 | 断言(通/总) |",
        f"|---|--------|------|------|------|-------------|",
    ]
    for i, s in enumerate(steps):
        name = s.get("name") or s.get("api_id") or "N/A"
        if s.get("skipped"):
            status = "⏭ 跳过"
        elif s.get("passed"):
            status = "✅ 通过"
        else:
            status = "❌ 失败"
        latency = f"{s.get('latency_ms', '-')}ms" if s.get("latency_ms") is not None else "-"
        ar = s.get("assert_results") or []
        ar_ok = sum(1 for a in ar if a.get("passed"))
        ar_total = len(ar)
        lines.append(
            f"| {i+1} | `{s.get('step_id', 'N/A')}` | {name} | {status} | {latency} | {ar_ok}/{ar_total} |"
        )
    lines.append("")

    # 失败步骤详情
    fail_steps = [s for s in steps if not s.get("skipped") and not s.get("passed")]
    if fail_steps:
        lines += ["## 失败步骤详情", ""]
        for s in fail_steps:
            lines += [
                f"### {s.get('name') or s.get('step_id', 'N/A')}",
                f"",
            ]
            if s.get("error"):
                lines += [
                    f"**错误信息**:",
                    f"```",
                    f"{s['error']}",
                    f"```",
                    f"",
                ]
            # 失败断言
            ar = s.get("assert_results") or []
            failed_ar = [a for a in ar if not a.get("passed")]
            if failed_ar:
                lines += [
                    f"**失败断言**:",
                    f"",
                    f"| 字段 | 操作符 | 期望值 | 实际值 | 风险等级 |",
                    f"|------|--------|--------|--------|----------|",
                ]
                for a in failed_ar:
                    lines.append(
                        f"| `{a.get('field', '')}` | {a.get('operator', '')} | `{a.get('expected', '')}` | `{a.get('actual', '')}` | {a.get('risk_level', '')} |"
                    )
                lines.append("")

    report = "\n".join(lines)

    return StreamingResponse(
        io.BytesIO(report.encode("utf-8")),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=execution_report_{execution_id[:8]}.md"},
    )
