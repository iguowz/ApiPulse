"""
FastAPI 应用入口 v5 — 路由已按功能域拆分为 api/routers/*.py
"""
from __future__ import annotations

import asyncio
import time
import uuid

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

# 共享状态模块（startup 时将服务实例注入 api.state）
import api.state as _state
from api.ws_manager import _ws
from api.routers.auth import router as auth_router

from config.database import close_connections, get_db, get_redis
from config.init_db import init_indexes
from config.settings import get_settings

from ai_analyzer.analyzer import AiAnalyzerService
from ai_analyzer.diff_evaluator import DiffEvaluatorService
from ai_analyzer.failure_diagnoser import FailureDiagnoserService
from api.deps import (
    close_ws_unauthorized,
    ensure_project_access,
    get_ws_user,
    user_has_permission,
)
from knowledge.service import KnowledgeService
from monitor.monitor import MonitorService
from services.memory_service import MemoryService  # ReMe 4-tier 记忆系统

_settings = get_settings()


def _user_from_bearer(auth_header: str) -> dict | None:
    """从 Bearer token 解析用户；HTTP 中间件共用，避免鉴权逻辑分散。"""
    if not auth_header.startswith("Bearer "):
        return None
    from services.auth_service import decode_access_token
    payload = decode_access_token(auth_header[7:])
    if payload is None:
        return None
    return {
        "user_id": payload.get("sub"),
        "username": payload.get("username"),
        "role": payload.get("role"),
        "project_id": payload.get("project_id", "default"),
    }


def _api_key_service_user() -> dict:
    """把有效 X-API-Key 映射为内部服务用户，兼容历史机器调用。"""
    return {
        "user_id": "api_key",
        "username": "api_key",
        "role": "admin",
        "project_id": "default",
    }

_WRITE_PERMISSION_RULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("POST", ("/apis/batch_delete",), "api:delete"),
    ("PUT", ("/apis/",), "api:update"),
    ("DELETE", ("/apis/",), "api:delete"),
    ("POST", ("/scenarios/generate", "/scenarios/import", "/scenarios/batch_delete"), "scenario:create"),
    ("POST", ("/scenarios/batch_run",), "scenario:run"),
    ("POST", ("/scenarios",), "scenario:create"),
    ("PUT", ("/scenarios/",), "scenario:update"),
    ("DELETE", ("/scenarios/",), "scenario:delete"),
    ("POST", ("/scenarios/",), "scenario:run"),
    ("POST", ("/monitors",), "monitor:create"),
    ("PUT", ("/monitors/",), "monitor:update"),
    ("DELETE", ("/monitors/",), "monitor:delete"),
    ("POST", ("/monitors/",), "monitor:update"),
    ("POST", ("/generations/",), "generation:review"),
    ("PUT", ("/settings/llm", "/settings/general"), "settings:update"),
    ("POST", ("/settings/llm",), "settings:update"),
    ("POST", ("/environments",), "environment:create"),
    ("PUT", ("/environments/",), "environment:update"),
    ("DELETE", ("/environments/",), "environment:delete"),
    ("POST", ("/alert-channels",), "alert_channel:manage"),
    ("PUT", ("/alert-channels/",), "alert_channel:manage"),
    ("DELETE", ("/alert-channels/",), "alert_channel:manage"),
    ("POST", ("/templates", "/datafactory/infer", "/datafactory/generate"), "factory:create"),
    ("PUT", ("/templates/",), "factory:update"),
    ("DELETE", ("/templates/",), "factory:delete"),
    ("POST", ("/templates/",), "factory:update"),
    ("POST", ("/knowledge/",), "knowledge:create"),
    ("PUT", ("/knowledge/",), "knowledge:update"),
    ("DELETE", ("/knowledge/",), "knowledge:delete"),
    ("POST", ("/capture/toggle",), "capture:manage"),
    ("POST", ("/traffic/ingest", "/traffic/batch-ingest"), "traffic:manage"),
    ("POST", ("/traffic/sources", "/traffic/rules"), "traffic:manage"),
    ("PUT", ("/traffic/sources/", "/traffic/rules/"), "traffic:manage"),
    ("DELETE", ("/traffic/sources/", "/traffic/rules/"), "traffic:manage"),
    ("POST", ("/database-services", "/sql-snippets"), "database_service:manage"),
    ("POST", ("/sql/run", "/sql-snippets/", "/database-services/"), "sql:run"),
    ("PUT", ("/database-services/", "/sql-snippets/"), "database_service:manage"),
    ("DELETE", ("/database-services/", "/sql-snippets/"), "database_service:manage"),
    ("POST", ("/mock-services",), "mock_service:manage"),
    ("PUT", ("/mock-services/",), "mock_service:manage"),
    ("DELETE", ("/mock-services/",), "mock_service:manage"),
    ("POST", ("/mock-services/",), "mock_service:manage"),
    ("POST", ("/har/upload",), "har:upload"),
    ("POST", ("/prompts",), "prompt:manage"),
    ("PUT", ("/prompts/",), "prompt:manage"),
    ("DELETE", ("/prompts/",), "prompt:manage"),
    ("POST", ("/prompts/",), "prompt:manage"),
    ("POST", ("/ai/dlq", "/ai/scenario-dlq"), "dlq:manage"),
    ("DELETE", ("/ai/dlq", "/ai/scenario-dlq"), "dlq:manage"),
    ("POST", ("/ai/chat",), "ai_chat:use"),
)


def _permission_for_request(method: str, path: str) -> str | None:
    """中心化写接口权限映射，避免每个路由遗漏 Depends。"""
    if method == "POST" and path == "/apis/import-curl":
        return "api:create"
    if method == "POST" and path == "/apis":
        return "api:create"
    if method == "POST" and path.startswith("/apis/"):
        if any(path.endswith(suffix) for suffix in ("/analyze", "/analyze-doc", "/analyze-asserts")):
            return "api:analyze"
        if path.endswith("/asserts"):
            return "api:update"
        if path.endswith("/contract-check"):
            return "api:read"
        return "api:run"
    for rule_method, prefixes, permission in _WRITE_PERMISSION_RULES:
        if method == rule_method and any(path.startswith(prefix) for prefix in prefixes):
            return permission
    return None

app = FastAPI(
    title="API Quality Platform",
    version="5.0.0",
    description="HAR → DSL → AI → DAG → Monitor",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ═══════════════════════════════════════════════════════════════
# 中间件
# ═══════════════════════════════════════════════════════════════

# 请求追踪中间件：为每个请求生成唯一 ID，注入日志上下文和响应头
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    with logger.contextualize(request_id=request_id):
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

# JWT 认证中间件：从 Authorization Bearer 头提取用户信息，注入 request.state。
# 说明：无 token 仍放行，后续 require_auth_middleware 会统一拦截非公开 HTTP 请求。
_JWT_EXEMPT_PATHS = frozenset({"/auth/login", "/auth/register", "/health", "/docs", "/openapi.json", "/redoc"})


def _is_public_path(path: str) -> bool:
    return path in _JWT_EXEMPT_PATHS or path.startswith("/mock-api/") or path in {"/traffic/ingest", "/traffic/batch-ingest"} or path == "/traffic/proxy-config"

@app.middleware("http")
async def jwt_auth_middleware(request: Request, call_next):
    # WebSocket 升级请求跳过（由 WS 端点自行处理认证）
    if request.headers.get("upgrade", "").lower() == "websocket":
        return await call_next(request)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        user = _user_from_bearer(auth_header)
        if user is None:
            return JSONResponse(status_code=401, content={"detail": "登录已过期，请重新登录"})
        request.state.user = user

    # 公开路径无需强制登录；若上方存在 Bearer，仍会注入 state 供管理端 traffic ingest 使用。
    if _is_public_path(request.url.path):
        return await call_next(request)

    return await call_next(request)

# API Key 认证中间件：当 api_key 配置非空时，校验 X-API-Key 请求头
# 跳过 WebSocket 升级路径和健康检查端点；若 JWT 已认证则跳过 API Key 校验
_EXEMPT_PATHS = _JWT_EXEMPT_PATHS

@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    if not _settings.api_key:
        return await call_next(request)
    # WebSocket 升级请求跳过（由 WS 端点自行处理认证）
    if request.headers.get("upgrade", "").lower() == "websocket":
        return await call_next(request)
    # 公开路径跳过
    if request.url.path in _EXEMPT_PATHS or request.url.path.startswith("/mock-api/") or request.url.path in {"/traffic/ingest", "/traffic/batch-ingest", "/traffic/proxy-config"}:
        return await call_next(request)
    # JWT 已认证的用户跳过 API Key 校验，避免多用户场景下仍需额外 API Key
    if not hasattr(request.state, "user"):
        user = _user_from_bearer(request.headers.get("Authorization", ""))
        if user:
            request.state.user = user
    if hasattr(request.state, "user"):
        return await call_next(request)

    api_key_header = request.headers.get("X-API-Key", "")
    if api_key_header != _settings.api_key:
        logger.warning("Invalid API key from ip={}", request.client.host if request.client else "unknown")
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing API key"},
        )

    # 标记 API Key 已认证，供统一鉴权兜底识别机器调用。
    request.state.api_key_authenticated = True
    return await call_next(request)


@app.middleware("http")
async def require_authenticated_middleware(request: Request, call_next):
    """非公开 HTTP 入口必须认证，避免历史只读路由遗漏 Depends 后跨项目裸露。"""
    if request.method.upper() == "OPTIONS":
        return await call_next(request)
    if request.headers.get("upgrade", "").lower() == "websocket":
        return await call_next(request)
    if _is_public_path(request.url.path) or request.url.path in _EXEMPT_PATHS:
        return await call_next(request)

    user = getattr(request.state, "user", None)
    if not user:
        user = _user_from_bearer(request.headers.get("Authorization", ""))
        if user:
            request.state.user = user
    if user or getattr(request.state, "api_key_authenticated", False):
        return await call_next(request)
    if _settings.api_key and request.headers.get("X-API-Key", "") == _settings.api_key:
        request.state.api_key_authenticated = True
        request.state.user = _api_key_service_user()
        return await call_next(request)
    return JSONResponse(status_code=401, content={"detail": "请先登录"})


@app.middleware("http")
async def rbac_middleware(request: Request, call_next):
    """核心写接口统一权限检查，弥补历史路由中遗漏的 Depends。"""
    if request.headers.get("upgrade", "").lower() == "websocket":
        return await call_next(request)
    if _is_public_path(request.url.path) or request.url.path in _EXEMPT_PATHS:
        return await call_next(request)

    permission = _permission_for_request(request.method.upper(), request.url.path)
    if not permission:
        return await call_next(request)

    user = getattr(request.state, "user", None)
    if not user:
        user = _user_from_bearer(request.headers.get("Authorization", ""))
        if user:
            request.state.user = user
    if not user and _settings.api_key and request.headers.get("X-API-Key", "") == _settings.api_key:
        request.state.api_key_authenticated = True
        user = _api_key_service_user()
        request.state.user = user
    if not user:
        return JSONResponse(status_code=401, content={"detail": "请先登录"})
    if not user_has_permission(user, permission):
        logger.warning(
            "权限拒绝: user={} role={} permission={} path={}",
            user.get("username"), user.get("role"), permission, request.url.path,
        )
        return JSONResponse(status_code=403, content={"detail": "权限不足"})
    return await call_next(request)

# 速率限制中间件：基于客户端 IP 的固定窗口计数器，Redis 存储
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if not _settings.rate_limit_enabled:
        return await call_next(request)

    # 获取客户端 IP：优先 X-Forwarded-For（最左侧为真实客户端），fallback 直连 IP
    forwarded = request.headers.get("X-Forwarded-For")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else "unknown"
    )

    try:
        redis = await get_redis()
        window_ts = int(time.time() // _settings.rate_limit_window_s)
        key = f"ratelimit:{client_ip}:{window_ts}"

        count = await redis.incr(key)
        if count == 1:
            # 首次请求时设置过期：窗口 2 倍避免时钟偏差导致残留
            await redis.expire(key, _settings.rate_limit_window_s * 2)

        if count > _settings.rate_limit_max_requests:
            logger.warning("Rate limit exceeded: ip={} count={}", client_ip, count)
            return JSONResponse(
                status_code=429,
                content={"detail": "Too Many Requests", "retry_after": _settings.rate_limit_window_s},
                headers={"Retry-After": str(_settings.rate_limit_window_s)},
            )
    except Exception:
        # Redis 不可用时放行请求，避免速率限制成为单点故障
        pass

    return await call_next(request)


# ═══════════════════════════════════════════════════════════════
# 注册路由模块
# ═══════════════════════════════════════════════════════════════

app.include_router(auth_router)

from api.routers.projects import router as projects_router
from api.routers.har import router as har_router
from api.routers.apis import router as apis_router
from api.routers.dlq import router as dlq_router
from api.routers.scenarios import router as scenarios_router
from api.routers.data_factory import router as data_factory_router
from api.routers.monitors import router as monitors_router
from api.routers.alerts import router as alerts_router
from api.routers.executions import router as executions_router
from api.routers.environments import router as environments_router
from api.routers.stats import router as stats_router
from api.routers.capture import router as capture_router
from api.routers.alert_channels import router as alert_channels_router
from api.routers.knowledge import router as knowledge_router
from api.routers.settings_llm import router as settings_llm_router
from api.routers.settings_general import router as settings_general_router
from api.routers.system import router as system_router
from api.routers.audit import router as audit_router
from api.routers.ai_operation_logs import router as ai_ops_router
from api.routers.diff_alerts import router as diff_alerts_router
from api.routers.generations import router as generations_router
from api.routers.prompts import router as prompts_router  # P1-6: Prompt 版本化管理
from api.routers.ai_chat import router as ai_chat_router  # P1-3: AI 对话面板
from api.routers.memory import router as memory_router  # P3: 4-tier 记忆管理
from api.routers.mock_services import router as mock_services_router
from api.routers.traffic import router as traffic_router
from api.routers.database_services import router as database_services_router

app.include_router(audit_router)
app.include_router(ai_ops_router)
app.include_router(diff_alerts_router)
app.include_router(generations_router)
app.include_router(prompts_router)  # P1-6: Prompt 版本化管理
app.include_router(ai_chat_router)  # P1-3: AI 对话面板
app.include_router(mock_services_router)
app.include_router(traffic_router)
app.include_router(database_services_router)
app.include_router(memory_router)  # P3: 4-tier 记忆管理
app.include_router(projects_router)
app.include_router(har_router)
app.include_router(apis_router)
app.include_router(dlq_router)
app.include_router(scenarios_router)
app.include_router(data_factory_router)
app.include_router(monitors_router)
app.include_router(alerts_router)
app.include_router(executions_router)
app.include_router(environments_router)
app.include_router(stats_router)
app.include_router(capture_router)
app.include_router(alert_channels_router)
app.include_router(knowledge_router)
app.include_router(settings_llm_router)
app.include_router(settings_general_router)
app.include_router(system_router)


# ═══════════════════════════════════════════════════════════════
# 启动 / 关闭
# ═══════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup():
    db    = await get_db()
    redis = await get_redis()
    await _validate_production_safety(db)
    await init_indexes(db)
    # 初始化默认管理员账户（仅在用户表为空时创建 admin/admin123）
    from services.auth_service import AuthService
    await AuthService(db).init_default_admin()

    # 初始化默认项目（projects 集合为空时自动创建，确保 find_public_service 可匹配）
    proj_count = await db["projects"].estimated_document_count()
    if proj_count == 0:
        from models.project import Project
        proj = Project(id=str(uuid.uuid4()), name="默认项目", slug="default", description="系统默认项目")
        await db["projects"].insert_one(proj.model_dump())
        logger.info("已创建默认项目: 默认项目 / default")

    # 注入服务实例到 api.state 模块，路由通过 import api.state 读取
    _state._monitor_service = MonitorService(db, redis, ws_manager=_ws)
    await _state._monitor_service.start()

    _state._knowledge_service = KnowledgeService(db, ws_manager=_ws)
    # ReMe 4-tier 记忆服务：L1 长期 / L2 项目 / L3 会话 / L4 对话
    _state._memory_service = MemoryService(db, redis, _settings)
    await _state._memory_service.start()
    _state._ai_analyzer = AiAnalyzerService(db, redis, ws_manager=_ws, knowledge=_state._knowledge_service, memory=_state._memory_service)
    # run_all_workers 同时启动 analyze + scenario 两个队列消费者
    _state._ai_worker_task = asyncio.create_task(_state._ai_analyzer.run_all_workers())

    # 启动差异评估 worker（消费 queue:diff_evaluate）
    _state._diff_evaluator = DiffEvaluatorService(db, redis, ws_manager=_ws)
    _state._diff_eval_task = asyncio.create_task(_state._diff_evaluator.run_worker())

    # Phase 4: 启动失败诊断 worker（消费 queue:diagnose_failure）
    _state._failure_diagnoser = FailureDiagnoserService(db, redis, ws_manager=_ws, memory=_state._memory_service)
    _state._diagnose_task = asyncio.create_task(_state._failure_diagnoser.run_worker())

    # P1-2: 启动告警 AI 分析 worker（消费 queue:alert_analyze）
    from ai_analyzer.alert_analyzer import AlertAnalyzerService
    _state._alert_analyzer = AlertAnalyzerService(db, redis, ws_manager=_ws, memory=_state._memory_service)
    _state._alert_analyze_task = asyncio.create_task(_state._alert_analyzer.run_worker())

    logger.info("Platform v5 started")

    # 生产环境 CORS 安全警告：通配符 * 允许任意来源跨域访问
    if _settings.app_env != "development" and _settings.cors_origins.strip() == "*":
        logger.warning(
            "SECURITY: CORS origins is set to '*' in non-development environment. "
            "Set CORS_ORIGINS to your specific domain(s) to prevent unauthorized cross-origin access."
        )


async def _validate_production_safety(db):
    """生产环境启动保护：阻断明显不安全的默认配置。"""
    if _settings.app_env == "development":
        return
    problems = []
    if _settings.jwt_secret == "apipulse-jwt-secret-change-in-production":
        problems.append("JWT_SECRET 仍为默认值")
    if _settings.cors_origins.strip() == "*":
        problems.append("CORS_ORIGINS 不能在非开发环境使用 '*'")
    if not _settings.sql_secret_key:
        problems.append("SQL_SECRET_KEY 未配置，数据库密码加密不能回退默认密钥")
    try:
        from services.auth_service import verify_password
        admin = await db["users"].find_one({"username": "admin"})
        if admin and verify_password("admin123", admin.get("password_hash", "")):
            problems.append("默认管理员 admin/admin123 尚未修改")
    except Exception as e:
        logger.warning("生产安全检查无法验证默认管理员: {}", e)
    if problems:
        msg = "生产环境安全检查失败: " + "；".join(problems)
        logger.error(msg)
        raise RuntimeError(msg)


@app.on_event("shutdown")
async def shutdown():
    # 取消 AI worker 后台任务，避免优雅关闭时残留协程
    if _state._ai_worker_task and not _state._ai_worker_task.done():
        _state._ai_worker_task.cancel()
        try:
            await _state._ai_worker_task
        except asyncio.CancelledError:
            pass
    # 取消差异评估 worker 任务
    if _state._diff_eval_task and not _state._diff_eval_task.done():
        _state._diff_eval_task.cancel()
        try:
            await _state._diff_eval_task
        except asyncio.CancelledError:
            pass
    # Phase 4: 取消失败诊断 worker 任务
    if hasattr(_state, "_diagnose_task") and _state._diagnose_task and not _state._diagnose_task.done():
        _state._diagnose_task.cancel()
        try:
            await _state._diagnose_task
        except asyncio.CancelledError:
            pass
    # P1-2: 取消告警 AI 分析 worker 任务
    if hasattr(_state, "_alert_analyze_task") and _state._alert_analyze_task and not _state._alert_analyze_task.done():
        _state._alert_analyze_task.cancel()
        try:
            await _state._alert_analyze_task
        except asyncio.CancelledError:
            pass
    if _state._monitor_service:
        _state._monitor_service.stop()
    if _state._memory_service:
        await _state._memory_service.close()
    await close_connections()


# ═══════════════════════════════════════════════════════════════
# WebSocket 端点
# ═══════════════════════════════════════════════════════════════

@app.websocket("/ws/execution/{exec_id}")
async def ws_execution(exec_id: str, websocket: WebSocket):
    """订阅单次执行进度推送"""
    user = await get_ws_user(websocket)
    if not user or not user_has_permission(user, "scenario:read"):
        await close_ws_unauthorized(websocket)
        return
    try:
        db = await get_db()
        exec_doc = await db["executions"].find_one({"id": exec_id}, {"project_id": 1})
        ensure_project_access(user, exec_doc.get("project_id") if exec_doc else user.get("project_id"))
    except Exception:
        await close_ws_unauthorized(websocket)
        return
    await _ws.connect(f"exec:{exec_id}", websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        _ws.disconnect(f"exec:{exec_id}", websocket)


@app.websocket("/ws/monitor")
async def ws_monitor_events(websocket: WebSocket):
    """订阅巡检告警实时事件"""
    user = await get_ws_user(websocket)
    if not user or not user_has_permission(user, "monitor:read"):
        await close_ws_unauthorized(websocket)
        return
    project_id = websocket.query_params.get("project_id") or user.get("project_id", "default")
    try:
        ensure_project_access(user, project_id)
    except Exception:
        await close_ws_unauthorized(websocket)
        return
    key = f"monitor:events:{project_id}"
    await _ws.connect(key, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        _ws.disconnect(key, websocket)


@app.websocket("/ws/ai-analysis")
async def ws_ai_analysis(websocket: WebSocket):
    """订阅 AI 分析状态变更 + 场景生成完成事件"""
    user = await get_ws_user(websocket)
    if not user or not user_has_permission(user, "generation:read"):
        await close_ws_unauthorized(websocket)
        return
    project_id = websocket.query_params.get("project_id") or user.get("project_id", "default")
    try:
        ensure_project_access(user, project_id)
    except Exception:
        await close_ws_unauthorized(websocket)
        return
    key = f"ai_analysis:{project_id}"
    await _ws.connect(key, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        _ws.disconnect(key, websocket)
