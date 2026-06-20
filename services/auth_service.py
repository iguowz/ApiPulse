"""
认证服务 —— JWT令牌管理 + 密码哈希 + 用户CRUD
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

import bcrypt
import jwt
from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.settings import get_settings
from models.user import (
    User, UserCreate, UserLogin, UserResponse, TokenResponse, UserRole, ROLE_PERMISSIONS,
)

_settings = get_settings()
_JWT_SECRET = _settings.jwt_secret
_JWT_ALGORITHM = "HS256"
_JWT_EXPIRE_HOURS = 24  # token 有效期 24 小时


# ── 密码哈希 ─────────────────────────────────────────────

def hash_password(password: str) -> str:
    """bcrypt 哈希密码"""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """验证密码与哈希是否匹配"""
    return bcrypt.checkpw(password.encode(), password_hash.encode())


# ── JWT 令牌 ─────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def create_access_token(user: User) -> str:
    """生成 JWT access token，payload 含 user_id/username/role"""
    payload = {
        "sub": user.id,
        "username": user.username,
        "role": user.role.value if isinstance(user.role, UserRole) else user.role,
        "project_id": user.project_id,
        "iat": _now(),
        "exp": _now() + timedelta(hours=_JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    """解码 JWT token，失败返回 None"""
    try:
        payload = jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        logger.debug("JWT token expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug("JWT token invalid: {}", e)
        return None


# ── 权限检查 ─────────────────────────────────────────────

def has_permission(role: str, permission: str) -> bool:
    """检查角色是否拥有指定权限"""
    perms = ROLE_PERMISSIONS.get(role, set())
    return permission in perms


# ── 用户CRUD ─────────────────────────────────────────────


class AuthService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self._col = db["users"]

    async def init_default_admin(self) -> None:
        """如果用户表为空，创建默认管理员 admin/admin123"""
        count = await self._col.estimated_document_count()
        if count > 0:
            return
        admin = User(
            id=str(uuid.uuid4()),
            username="admin",
            password_hash=hash_password("admin123"),
            role=UserRole.ADMIN,
            display_name="管理员",
            enabled=True,
        )
        await self._col.insert_one(admin.model_dump())
        logger.info("已创建默认管理员账户: admin / admin123")

    async def authenticate(self, login: UserLogin) -> TokenResponse | None:
        """验证用户名密码，返回 JWT token；失败返回 None"""
        doc = await self._col.find_one({"username": login.username})
        if not doc:
            return None
        user = User(**doc)
        if not user.enabled:
            return None
        if not verify_password(login.password, user.password_hash):
            return None

        token = create_access_token(user)
        return TokenResponse(
            access_token=token,
            user=_user_to_response(user),
        )

    async def register(self, data: UserCreate) -> UserResponse:
        """注册新用户；用户名重复抛 ValueError"""
        existing = await self._col.find_one({"username": data.username})
        if existing:
            raise ValueError(f"用户名 '{data.username}' 已存在")

        user = User(
            id=str(uuid.uuid4()),
            username=data.username,
            password_hash=hash_password(data.password),
            role=data.role,
            display_name=data.display_name or data.username,
            project_id=data.project_id,
        )
        await self._col.insert_one(user.model_dump())
        logger.info("用户 '{}' 注册成功，角色: {}", user.username, user.role.value)
        return _user_to_response(user)

    async def get_user_by_id(self, user_id: str) -> User | None:
        doc = await self._col.find_one({"id": user_id})
        return User(**doc) if doc else None

    async def list_users(self) -> list[UserResponse]:
        docs = await self._col.find({}).sort("created_at", 1).to_list(length=200)
        return [_user_to_response(User(**d)) for d in docs]

    async def update_user(self, user_id: str, updates: dict[str, Any]) -> UserResponse | None:
        """更新用户字段（username, role, display_name, enabled, project_id）"""
        # 不允许通过此接口修改密码哈希
        safe_updates = {k: v for k, v in updates.items() if k in (
            "username", "role", "display_name", "enabled", "project_id",
        )}
        if "role" in safe_updates:
            safe_updates["role"] = UserRole(safe_updates["role"]).value
        result = await self._col.find_one_and_update(
            {"id": user_id},
            {"$set": safe_updates},
            return_document=True,
        )
        return _user_to_response(User(**result)) if result else None

    async def change_password(self, user_id: str, old_password: str, new_password: str) -> bool:
        """修改密码：验证旧密码 → 更新哈希"""
        user = await self.get_user_by_id(user_id)
        if not user:
            return False
        if not verify_password(old_password, user.password_hash):
            return False
        new_hash = hash_password(new_password)
        await self._col.update_one(
            {"id": user_id},
            {"$set": {"password_hash": new_hash}},
        )
        return True

    async def delete_user(self, user_id: str) -> bool:
        result = await self._col.delete_one({"id": user_id})
        return result.deleted_count > 0


def _user_to_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        display_name=user.display_name or user.username,
        project_id=user.project_id,
        enabled=user.enabled,
        created_at=user.created_at,
    )
