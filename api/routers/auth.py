"""
认证路由 —— 登录/注册/用户管理
所有端点挂在 /auth 前缀下，由主路由注册时设置
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.database import get_db
from models.user import UserCreate, UserLogin
from services.auth_service import AuthService
from api.deps import get_current_user, require_auth, require_permission

router = APIRouter(prefix="/auth", tags=["Auth"])


def _get_auth_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> AuthService:
    """依赖注入：获取 AuthService 实例"""
    return AuthService(db)


# ── 认证端点（无需登录） ────────────────────────────────────

@router.post("/login")
async def login(
    data: UserLogin = Body(...),
    auth: AuthService = Depends(_get_auth_service),
):
    """用户名密码登录，返回 JWT token 和用户信息"""
    result = await auth.authenticate(data)
    if result is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return result


@router.post("/register")
async def register(
    data: UserCreate = Body(...),
    auth: AuthService = Depends(_get_auth_service),
    current_user: dict = Depends(get_current_user),
):
    """
    注册新用户。
    - 未登录时可自助注册（默认 viewer 角色）
    - 已登录 admin 可创建任意角色用户
    """
    # 如果已登录且非 admin 尝试创建非 viewer 用户，拒绝
    if current_user is not None:
        from services.auth_service import has_permission
        if not has_permission(current_user.get("role", ""), "user:manage"):
            # 非管理员仅允许创建 viewer 角色（自助注册）
            if data.role.value != "viewer":
                raise HTTPException(status_code=403, detail="仅管理员可创建非 viewer 角色用户")

    try:
        user = await auth.register(data)
        return user
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


# ── 用户管理端点（需登录） ──────────────────────────────────

@router.get("/me")
async def get_me(current_user: dict = Depends(require_auth)):
    """获取当前登录用户信息"""
    return current_user


@router.get("/users")
async def list_users(
    auth: AuthService = Depends(_get_auth_service),
    _: dict = Depends(require_permission("user:manage")),
):
    """获取所有用户列表（仅 admin）"""
    return await auth.list_users()


@router.get("/users/{user_id}")
async def get_user(
    user_id: str,
    auth: AuthService = Depends(_get_auth_service),
    _: dict = Depends(require_auth),
):
    """获取指定用户信息"""
    user = await auth.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    from services.auth_service import _user_to_response
    return _user_to_response(user)


@router.put("/users/{user_id}")
async def update_user(
    user_id: str,
    updates: dict = Body(...),
    auth: AuthService = Depends(_get_auth_service),
    _: dict = Depends(require_permission("user:manage")),
):
    """更新用户信息（仅 admin）"""
    result = await auth.update_user(user_id, updates)
    if result is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return result


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    auth: AuthService = Depends(_get_auth_service),
    current_user: dict = Depends(require_permission("user:manage")),
):
    """删除用户（仅 admin，不可删除自己）"""
    if user_id == current_user.get("user_id"):
        raise HTTPException(status_code=400, detail="不能删除自己")
    ok = await auth.delete_user(user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"deleted": True}


@router.post("/change-password")
async def change_password(
    data: dict = Body(...),
    auth: AuthService = Depends(_get_auth_service),
    current_user: dict = Depends(require_auth),
):
    """修改当前用户密码：需提供 old_password 和 new_password"""
    old_password = data.get("old_password", "")
    new_password = data.get("new_password", "")
    if not old_password or not new_password:
        raise HTTPException(status_code=400, detail="请提供 old_password 和 new_password")
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码至少 6 位")

    ok = await auth.change_password(current_user["user_id"], old_password, new_password)
    if not ok:
        raise HTTPException(status_code=400, detail="旧密码错误")
    return {"changed": True}
