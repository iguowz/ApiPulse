"""
共享依赖项 —— JWT 认证、权限校验、数据库/Redis 获取、审计日志辅助
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request, WebSocket, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from loguru import logger

from config.database import get_db, get_redis
from services.auth_service import decode_access_token, has_permission


def is_admin(user: dict[str, Any] | None) -> bool:
    """当前用户是否为 admin；admin 可跨项目访问。"""
    return bool(user and user.get("role") == "admin")


def ensure_project_access(user: dict[str, Any] | None, project_id: str | None) -> None:
    """按 project_id 做多租户隔离，admin 放行，普通用户只能访问自己的项目。"""
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    if not project_id:
        return
    if is_admin(user):
        return
    if user.get("project_id", "default") != project_id:
        raise HTTPException(status_code=403, detail="无权访问该项目")


def visible_project_id(user: dict[str, Any] | None, requested_project_id: str | None = None) -> str:
    """解析当前请求可访问的项目 ID；非 admin 忽略外部传入的跨项目值。"""
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    if is_admin(user):
        return requested_project_id or user.get("project_id", "default")
    return user.get("project_id", "default")


def user_has_permission(user: dict[str, Any] | None, permission: str) -> bool:
    """检查用户权限，供中间件和工具层复用。"""
    if not user:
        return False
    return has_permission(user.get("role", ""), permission)


# ── 审计日志辅助 ──────────────────────────────────────────────

def _get_user_from_request(request: Request) -> dict[str, Any] | None:
    """从 request.state 提取当前用户信息（由 JWT 中间件注入），未认证返回 None"""
    return getattr(request.state, "user", None)


def _get_client_ip(request: Request) -> str:
    """获取客户端 IP：优先 X-Forwarded-For，fallback 直连 IP"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

# HTTP Bearer token 提取器
_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
):
    """
    从 Authorization: Bearer <token> 中提取当前用户信息。
    未携带 token 返回 None（由路由自行决定是否拒绝）。
    token 无效时返回 401。
    """
    state_user = getattr(request.state, "user", None)
    if state_user:
        return state_user
    if getattr(request.state, "api_key_authenticated", False):
        # 兼容历史 X-API-Key 机器调用；它是全局服务凭证，按 admin 权限处理。
        return {
            "user_id": "api_key",
            "username": "api_key",
            "role": "admin",
            "project_id": "default",
        }
    if credentials is None:
        return None

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")

    user_info = {
        "user_id": payload.get("sub"),
        "username": payload.get("username"),
        "role": payload.get("role"),
        "project_id": payload.get("project_id", "default"),
    }
    # 注入到 request.state 供下游使用
    request.state.user = user_info
    return user_info


def require_auth(request: Request):
    """依赖：要求已认证用户，未认证返回 401"""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def require_permission(permission: str):
    """依赖工厂：要求当前用户拥有指定权限"""

    async def _check(request: Request):
        user = require_auth(request)
        role = user.get("role", "")
        if not has_permission(role, permission):
            logger.warning(
                "权限拒绝: user={} role={} permission={}",
                user.get("username"), role, permission,
            )
            raise HTTPException(status_code=403, detail="权限不足")
        return user

    return _check


async def get_ws_user(websocket: WebSocket) -> dict[str, Any] | None:
    """
    WebSocket 鉴权：支持 query token 与 Sec-WebSocket-Protocol 中的 bearer token。
    前端统一走 ?token=，保留协议头兼容自动化脚本。
    """
    token = websocket.query_params.get("token", "")
    if not token:
        proto = websocket.headers.get("sec-websocket-protocol", "")
        if proto.lower().startswith("bearer,"):
            token = proto.split(",", 1)[1].strip()
        elif proto.lower().startswith("bearer "):
            token = proto[7:].strip()
    if not token:
        return None
    payload = decode_access_token(token)
    if payload is None:
        return None
    return {
        "user_id": payload.get("sub"),
        "username": payload.get("username"),
        "role": payload.get("role"),
        "project_id": payload.get("project_id", "default"),
    }


async def close_ws_unauthorized(websocket: WebSocket) -> None:
    """WS 未授权统一关闭码。"""
    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
