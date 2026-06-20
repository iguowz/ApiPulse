from __future__ import annotations
from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase


async def init_indexes(db: AsyncIOMotorDatabase) -> None:
    # ── api_dsls ──────────────────────────────────────────
    await db["api_dsls"].create_index("id", unique=True)
    # source_hash 唯一索引：先删除旧的非唯一索引，避免升级冲突
    try:
        await db["api_dsls"].drop_index("source_hash_1")
    except Exception:
        logger.warning("Failed to drop index source_hash_1 (may not exist yet)")
    try:
        await db["api_dsls"].drop_index("project_id_1_source_hash_1")
    except Exception:
        logger.warning("Failed to drop index project_id_1_source_hash_1 (may not exist yet)")
    await db["api_dsls"].create_index([("project_id", 1), ("source_hash", 1)], unique=True)
    await db["api_dsls"].create_index([("project_id", 1), ("created_at", -1)])
    # 替换旧 ai_analyzed bool 索引为 analysis_status 枚举索引
    await db["api_dsls"].create_index([("project_id", 1), ("analysis_status", 1)])
    await db["api_dsls"].create_index("tags")
    # 支持路径/名称搜索
    await db["api_dsls"].create_index([("request.path", "text"), ("name", "text")])

    # ── scenarios ─────────────────────────────────────────
    await db["scenarios"].create_index("id", unique=True)
    await db["scenarios"].create_index([("project_id", 1), ("updated_at", -1)])

    # ── data_templates ────────────────────────────────────
    await db["data_templates"].create_index("id", unique=True)
    await db["data_templates"].create_index("api_id")

    # ── monitors ──────────────────────────────────────────
    await db["monitors"].create_index("id", unique=True)
    await db["monitors"].create_index([("project_id", 1), ("enabled", 1)])
    await db["monitors"].create_index("api_id")

    # ── mock services ─────────────────────────────────────
    await db["mock_services"].create_index("id", unique=True)
    await db["mock_services"].create_index([("project_id", 1), ("slug", 1)], unique=True)
    await db["mock_services"].create_index([("project_id", 1), ("updated_at", -1)])
    await db["mock_routes"].create_index("id", unique=True)
    await db["mock_routes"].create_index([("service_id", 1), ("priority", 1)])
    await db["mock_call_logs"].create_index("id", unique=True)
    await db["mock_call_logs"].create_index([("service_id", 1), ("created_at", -1)])
    await db["mock_call_logs"].create_index([("service_id", 1), ("status_code", 1), ("created_at", -1)])
    await db["mock_call_logs"].create_index([("service_id", 1), ("route_id", 1), ("created_at", -1)])

    # ── traffic ingest / proxy rules ──────────────────────
    await db["traffic_sources"].create_index("id", unique=True)
    await db["traffic_sources"].create_index([("project_id", 1), ("updated_at", -1)])
    await db["traffic_rules"].create_index("id", unique=True)
    await db["traffic_rules"].create_index([("project_id", 1), ("source_id", 1), ("priority", 1)])
    await db["traffic_records"].create_index("id", unique=True)
    await db["traffic_records"].create_index([("project_id", 1), ("created_at", -1)])
    await db["traffic_records"].create_index([("source_id", 1), ("created_at", -1)])
    await db["traffic_records"].create_index([("project_id", 1), ("method", 1), ("created_at", -1)])
    await db["traffic_records"].create_index([("project_id", 1), ("path", 1)])

    # ── database services / sql snippets ─────────────────
    await db["database_services"].create_index("id", unique=True)
    await db["database_services"].create_index([("project_id", 1), ("updated_at", -1)])
    await db["sql_snippets"].create_index("id", unique=True)
    await db["sql_snippets"].create_index([("project_id", 1), ("db_service_id", 1)])
    await db["sql_snippets"].create_index([("project_id", 1), ("updated_at", -1)])

    # ── executions ────────────────────────────────────────
    await db["executions"].create_index("id", unique=True)
    # 高频查询：按项目+时间倒序（修复：之前缺少此复合索引）
    await db["executions"].create_index([("project_id", 1), ("started_at", -1)])
    await db["executions"].create_index([("api_id", 1), ("started_at", -1)])
    await db["executions"].create_index([("scenario_id", 1), ("started_at", -1)])
    await db["executions"].create_index([("trigger", 1), ("started_at", -1)])
    await db["executions"].create_index([("passed", 1), ("started_at", -1)])
    # 巡检差异检测专用：api_id + trigger + passed + started_at
    await db["executions"].create_index(
        [("api_id", 1), ("trigger", 1), ("passed", 1), ("started_at", -1)]
    )

    # ── alert_records ─────────────────────────────────────
    await db["alert_records"].create_index("id", unique=True)
    # 高频查询：monitor_id + sent_at 倒序（修复：之前只有 monitor_id）
    await db["alert_records"].create_index([("monitor_id", 1), ("sent_at", -1)])
    await db["alert_records"].create_index([("sent_at", -1)])
    await db["alert_records"].create_index("is_recovery")
    await db["alert_records"].create_index("risk_level")

    # ── projects ──────────────────────────────────────────
    await db["projects"].create_index("id", unique=True)
    await db["projects"].create_index("slug", unique=True)

    # ── alert_channels ────────────────────────────────────
    await db["alert_channels"].create_index("id", unique=True)
    await db["alert_channels"].create_index([("project_id", 1), ("enabled", 1)])

    # ── knowledge_entries ─────────────────────────────────
    await db["knowledge_entries"].create_index("id", unique=True)
    await db["knowledge_entries"].create_index([("project_id", 1), ("type", 1)])
    await db["knowledge_entries"].create_index([("project_id", 1), ("tags", 1)])
    await db["knowledge_entries"].create_index("source_hash")
    await db["knowledge_entries"].create_index("updated_at")
    # 全文索引用于关键词搜索（title + content）
    await db["knowledge_entries"].create_index([("title", "text"), ("content", "text")])

    # ── ai_operation_logs ─────────────────────────────
    await db["ai_operation_logs"].create_index("id", unique=True)
    await db["ai_operation_logs"].create_index([("project_id", 1), ("created_at", -1)])
    await db["ai_operation_logs"].create_index([("api_ids", 1), ("created_at", -1)])
    await db["ai_operation_logs"].create_index("type")

    # ── ai_jobs ───────────────────────────────────────────
    await db["ai_jobs"].create_index("job_id", unique=True)
    await db["ai_jobs"].create_index([("project_id", 1), ("updated_at", -1)])
    await db["ai_jobs"].create_index([("project_id", 1), ("status", 1), ("updated_at", -1)])
    await db["ai_jobs"].create_index([("project_id", 1), ("type", 1), ("updated_at", -1)])

    logger.info("MongoDB indexes initialized")

    # ── 数据迁移：ai_analyzed bool → analysis_status 枚举 ──
    await _migrate_analysis_status(db)


async def _migrate_analysis_status(db: AsyncIOMotorDatabase) -> None:
    """
    将旧版 ai_analyzed: bool 迁移为 analysis_status: str 枚举。
    - ai_analyzed==True → analysis_status="done"
    - 否则 → analysis_status="idle"
    - 已迁移的文档（已有 analysis_status 字段）跳过
    """
    # 只迁移还未有 analysis_status 字段的文档
    migrated = 0
    # True → done
    result = await db["api_dsls"].update_many(
        {"analysis_status": {"$exists": False}, "ai_analyzed": True},
        {"$set": {"analysis_status": "done"}},
    )
    migrated += result.modified_count
    # False/不存在 → idle
    result = await db["api_dsls"].update_many(
        {"analysis_status": {"$exists": False}},
        {"$set": {"analysis_status": "idle"}},
    )
    migrated += result.modified_count
    if migrated:
        logger.info("Migrated {} api_dsls documents: ai_analyzed → analysis_status", migrated)
