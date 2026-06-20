"""
HAR 上传与隔离区路由
"""
from __future__ import annotations

import tempfile

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.database import get_db, get_redis
from har_parser.parser import HarParserService
from models.audit import AuditAction, AuditResource
from services.audit_service import AuditService
from services.diff_service import DiffService
from api.deps import _get_user_from_request, _get_client_ip
from api.state import _quarantine

router = APIRouter(tags=["HAR"])


def get_audit_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> AuditService:
    return AuditService(db)


@router.post("/har/upload")
async def upload_har(
    file: UploadFile = File(...),
    project_id: str = Query(default="default"),
    filter_host: str | None = Query(default=None, description="仅导入匹配域名的请求（子串匹配）"),
    filter_url: str | None = Query(default=None, description="仅导入 URL 中包含此关键字的请求"),
    request: Request = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
    audit_service: AuditService = Depends(get_audit_service),
):
    if not (file.filename or "").endswith(".har"):
        raise HTTPException(400, "Only .har files accepted")
    max_size = 50 * 1024 * 1024
    size = 0
    with tempfile.SpooledTemporaryFile(max_size=10 * 1024 * 1024) as tmp:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_size:
                raise HTTPException(413, "HAR file too large (max 50 MB)")
            tmp.write(chunk)
        tmp.seek(0)
        content = tmp.read()
    # 读取项目级别域名过滤配置，与请求参数合并后传给解析器
    proj = await db["projects"].find_one({"id": project_id}, {"domain_allowlist": 1, "domain_blocklist": 1})
    domain_allowlist = proj.get("domain_allowlist", []) if proj else []
    domain_blocklist = proj.get("domain_blocklist", []) if proj else []
    redis = await get_redis()
    svc = HarParserService(
        db, redis, _quarantine, project_id=project_id,
        filter_host=filter_host, filter_url=filter_url,
        domain_allowlist=domain_allowlist, domain_blocklist=domain_blocklist,
    )
    # 注入差异检测服务，导入后自动对比已分析 API 的字段变化
    svc.set_diff_service(DiffService(db, redis))
    result = await svc.parse_and_save(file.filename or "upload.har", content)
    # 审计日志：记录 HAR 上传操作
    await audit_service.log_action(
        user=_get_user_from_request(request), action=AuditAction.IMPORT,
        resource=AuditResource.HAR, resource_id="", resource_name=file.filename or "upload.har",
        ip=_get_client_ip(request), extra={"project_id": project_id, "imported": result.get("imported", 0)},
    )
    return result


@router.get("/har/quarantine")
async def list_quarantine(skip: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
    """列出隔离区中的失败 HAR 文件，支持分页"""
    items = await _quarantine.list_items(skip=skip, limit=limit)
    return {"items": items, "skip": skip, "limit": limit}
