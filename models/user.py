"""
用户模型 —— 多用户认证与 RBAC 权限系统
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from enum import Enum
from pydantic import BaseModel, Field


class UserRole(str, Enum):
    ADMIN = "admin"           # 全部权限
    EDITOR = "editor"         # CRUD（API/场景/监控/数据工厂/环境）
    VIEWER = "viewer"         # 只读（Dashboard + 列表 + 详情）
    MONITOR_ADMIN = "monitor_admin"  # 监控管理（创建/编辑/启停监控 + 查看其他）


# 角色权限矩阵：每个角色允许的操作列表
ROLE_PERMISSIONS: dict[str, set[str]] = {
    UserRole.ADMIN: {
        "api:read", "api:create", "api:update", "api:delete", "api:run",
        "api:analyze",
        "scenario:read", "scenario:create", "scenario:update", "scenario:delete", "scenario:run",
        "monitor:read", "monitor:create", "monitor:update", "monitor:delete",
        "factory:read", "factory:create", "factory:update", "factory:delete",
        "knowledge:read", "knowledge:create", "knowledge:update", "knowledge:delete",
        "environment:read", "environment:create", "environment:update", "environment:delete",
        "generation:read", "generation:review",
        "prompt:read", "prompt:manage",
        "settings:read", "settings:update",
        "alert_channel:read", "alert_channel:manage",
        "capture:read", "capture:manage",
        "mock_service:read", "mock_service:manage",
        "traffic:read", "traffic:manage",
        "database_service:read", "database_service:manage", "sql:run",
        "audit:read", "ai_log:read", "dlq:manage",
        "ai_chat:use",
        "project:read", "project:create", "project:update", "project:delete",
        "har:upload", "stats:read", "user:manage",
    },
    UserRole.EDITOR: {
        "api:read", "api:create", "api:update", "api:delete", "api:run",
        "api:analyze",
        "scenario:read", "scenario:create", "scenario:update", "scenario:delete", "scenario:run",
        "monitor:read", "monitor:create", "monitor:update", "monitor:delete",
        "factory:read", "factory:create", "factory:update", "factory:delete",
        "knowledge:read", "knowledge:create", "knowledge:update", "knowledge:delete",
        "environment:read", "environment:create", "environment:update", "environment:delete",
        "generation:read", "generation:review",
        "prompt:read",
        "settings:read",
        "alert_channel:read", "alert_channel:manage",
        "capture:read", "capture:manage",
        "mock_service:read", "mock_service:manage",
        "traffic:read", "traffic:manage",
        "database_service:read", "database_service:manage", "sql:run",
        "ai_chat:use",
        "project:read", "har:upload", "stats:read",
    },
    UserRole.MONITOR_ADMIN: {
        "api:read", "api:run",
        "scenario:read", "scenario:run",
        "monitor:read", "monitor:create", "monitor:update", "monitor:delete",
        "generation:read",
        "alert_channel:read", "alert_channel:manage",
        "mock_service:read",
        "traffic:read",
        "database_service:read", "sql:run",
        "ai_chat:use",
        "project:read", "stats:read",
    },
    UserRole.VIEWER: {
        "api:read",
        "scenario:read",
        "monitor:read",
        "factory:read",
        "knowledge:read",
        "environment:read",
        "generation:read",
        "settings:read",
        "alert_channel:read",
        "mock_service:read",
        "traffic:read",
        "database_service:read",
        "ai_chat:use",
        "project:read",
        "stats:read",
    },
}


class User(BaseModel):
    id: str = ""
    username: str
    password_hash: str = ""       # bcrypt hash，不对外暴露
    role: UserRole = UserRole.VIEWER
    display_name: str = ""
    project_id: str = "default"   # 所属项目
    enabled: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))


class UserCreate(BaseModel):
    """注册/创建用户请求体"""
    username: str
    password: str
    role: UserRole = UserRole.VIEWER
    display_name: str = ""
    project_id: str = "default"


class UserLogin(BaseModel):
    """登录请求体"""
    username: str
    password: str


class UserResponse(BaseModel):
    """用户信息响应（不含密码哈希）"""
    id: str
    username: str
    role: UserRole
    display_name: str
    project_id: str
    enabled: bool
    created_at: datetime


class TokenResponse(BaseModel):
    """JWT 令牌响应"""
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
