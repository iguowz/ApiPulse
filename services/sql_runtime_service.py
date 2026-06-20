from __future__ import annotations

from loguru import logger

import asyncio
import base64
import hashlib
import hmac
import json
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.settings import get_settings
from models.database_service import DatabaseServiceConfig, SqlSnippet


WRITE_SQL_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|merge|replace|call|execute|exec|copy|vacuum|attach|detach|pragma|begin|commit|rollback)\b",
    re.IGNORECASE,
)
LINE_COMMENT_RE = re.compile(r"--[^\n]*(?=\n|$)")
BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
TEMPLATE_RE = re.compile(r"\{\{\s*([^}]+)\s*\}\}")
MAX_FIELD_CHARS = 4000
SAFE_SQLITE_ROOTS = ("data", "tmp", "logs")


def _now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)


def _secret_key() -> str:
    settings = get_settings()
    return getattr(settings, "sql_secret_key", "") or settings.jwt_secret


def encrypt_password(raw: str) -> str:
    if not raw:
        return ""
    key = hashlib.sha256(_secret_key().encode()).digest()
    data = raw.encode()
    encrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    sig = hmac.new(key, encrypted, hashlib.sha256).digest()[:8]
    return base64.urlsafe_b64encode(sig + encrypted).decode()


def decrypt_password(value: str) -> str:
    if not value:
        return ""
    key = hashlib.sha256(_secret_key().encode()).digest()
    raw = base64.urlsafe_b64decode(value.encode())
    sig, encrypted = raw[:8], raw[8:]
    expected = hmac.new(key, encrypted, hashlib.sha256).digest()[:8]
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(400, "Database password cannot be decrypted")
    data = bytes(b ^ key[i % len(key)] for i, b in enumerate(encrypted))
    return data.decode()


def mask_service(doc: dict[str, Any]) -> dict[str, Any]:
    item = dict(doc)
    item.pop("_id", None)
    if item.get("password_encrypted"):
        item["password"] = "******"
    item.pop("password_encrypted", None)
    return item


def _sanitize_cell(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
        return value[:MAX_FIELD_CHARS] + "..."
    if isinstance(value, (bytes, bytearray)):
        return f"<bytes:{len(value)}>"
    return value


def _get_path_value(data: Any, path: str) -> Any:
    if not path:
        return None
    cur = data
    for part in path.replace("$.", "").split("."):
        if not part:
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if 0 <= idx < len(cur) else None
        else:
            return None
    return cur


def render_value(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            expr = match.group(1).strip()
            found = _get_path_value(context, expr)
            return "" if found is None else str(found)
        return TEMPLATE_RE.sub(repl, value)
    if isinstance(value, dict):
        return {k: render_value(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [render_value(v, context) for v in value]
    return value


def _strip_sql_comments_and_literals(sql: str) -> str:
    """
    只读校验用的轻量 SQL 归一化。
    去掉注释与字符串字面量，解决 '-- update' 或 'select "delete"' 被误判/绕过的问题。
    """
    without_comments = BLOCK_COMMENT_RE.sub(" ", LINE_COMMENT_RE.sub(" ", sql or ""))
    out: list[str] = []
    quote = ""
    i = 0
    while i < len(without_comments):
        ch = without_comments[i]
        if quote:
            if ch == quote:
                if i + 1 < len(without_comments) and without_comments[i + 1] == quote:
                    i += 2
                    continue
                quote = ""
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            out.append(" ")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def normalize_readonly_sql(sql: str) -> str:
    """返回可执行的单条只读 SQL；保留原 SQL，仅剥离尾部分号。"""
    stripped = (sql or "").strip()
    if not stripped:
        raise HTTPException(422, "SQL is required")
    # 允许一个尾部分号；归一化后的主体中仍有分号说明是多语句。
    executable = stripped[:-1].strip() if stripped.endswith(";") else stripped
    validation_target = _strip_sql_comments_and_literals(executable)
    if ";" in validation_target:
        raise HTTPException(422, "Only single statement SQL is allowed")
    lowered = validation_target.strip().lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise HTTPException(422, "Only SELECT SQL is allowed")
    if WRITE_SQL_RE.search(validation_target):
        raise HTTPException(422, "Unsafe SQL keyword is not allowed")
    return executable


def validate_readonly_sql(sql: str) -> None:
    normalize_readonly_sql(sql)


def _error_result(message: str, code: str = "sql_error", duration_ms: int = 0) -> dict[str, Any]:
    return {
        "ok": False,
        "error_code": code,
        "error": message,
        "message": message,
        "columns": [],
        "rows": [],
        "first": None,
        "scalar": None,
        "row_count": 0,
        "duration_ms": duration_ms,
        "truncated": False,
        "rendered_sql_preview": "",
        "rendered_params": {},
    }


def _normalize_service_payload(data: dict[str, Any], partial: bool = False) -> dict[str, Any]:
    payload = dict(data)
    payload["read_only"] = True
    if not partial or "timeout_ms" in payload:
        payload["timeout_ms"] = max(500, min(int(payload.get("timeout_ms") or 5000), 30000))
    if not partial or "max_rows" in payload:
        payload["max_rows"] = max(1, min(int(payload.get("max_rows") or 100), 1000))
    return payload


def _sqlite_path_allowed(path: Path) -> bool:
    settings = get_settings()
    resolved = path.expanduser().resolve()
    if settings.app_env == "development":
        return True
    cwd = Path.cwd().resolve()
    allowed_roots = [cwd / root for root in SAFE_SQLITE_ROOTS]
    allowed_roots.append(Path("/tmp").resolve())
    return any(str(resolved).startswith(str(root.resolve())) for root in allowed_roots)


class SqlRuntimeService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    async def list_services(self, project_id: str) -> list[dict[str, Any]]:
        docs = await self.db["database_services"].find({"project_id": project_id}, {"_id": 0}).sort("updated_at", -1).to_list(200)
        return [mask_service(d) for d in docs]

    async def get_service(self, service_id: str) -> dict[str, Any] | None:
        return await self.db["database_services"].find_one({"id": service_id}, {"_id": 0})

    async def create_service(self, data: dict[str, Any], project_id: str, username: str = "") -> dict[str, Any]:
        payload = _normalize_service_payload({**data, "project_id": project_id, "created_by": username, "updated_by": username})
        password = payload.pop("password", "")
        if password:
            payload["password_encrypted"] = encrypt_password(password)
        svc = DatabaseServiceConfig(**payload)
        await self.db["database_services"].insert_one(svc.model_dump())
        return mask_service(svc.model_dump())

    async def update_service(self, service_id: str, data: dict[str, Any], username: str = "") -> dict[str, Any] | None:
        existing = await self.get_service(service_id)
        if not existing:
            return None
        update = _normalize_service_payload(
            {k: v for k, v in data.items() if k not in {"id", "project_id", "created_at", "created_by", "password_encrypted"}},
            partial=True,
        )
        if update.get("password"):
            update["password_encrypted"] = encrypt_password(update.pop("password"))
        else:
            update.pop("password", None)
        update["updated_by"] = username
        update["updated_at"] = _now()
        await self.db["database_services"].update_one({"id": service_id}, {"$set": update})
        return mask_service(await self.get_service(service_id) or {})

    async def delete_service(self, service_id: str) -> bool:
        result = await self.db["database_services"].delete_one({"id": service_id})
        await self.db["sql_snippets"].delete_many({"db_service_id": service_id})
        return result.deleted_count > 0

    async def list_snippets(self, project_id: str, db_service_id: str = "") -> list[dict[str, Any]]:
        q: dict[str, Any] = {"project_id": project_id}
        if db_service_id:
            q["db_service_id"] = db_service_id
        return await self.db["sql_snippets"].find(q, {"_id": 0}).sort("updated_at", -1).to_list(500)

    async def get_snippet(self, snippet_id: str) -> dict[str, Any] | None:
        return await self.db["sql_snippets"].find_one({"id": snippet_id}, {"_id": 0})

    async def create_snippet(self, data: dict[str, Any], project_id: str, username: str = "") -> dict[str, Any]:
        service = await self.get_service(data.get("db_service_id", ""))
        if not service or service.get("project_id") != project_id:
            raise HTTPException(404, "Database service not found")
        validate_readonly_sql(data.get("sql", ""))
        snippet = SqlSnippet(**{**data, "project_id": project_id, "created_by": username, "updated_by": username})
        await self.db["sql_snippets"].insert_one(snippet.model_dump())
        return snippet.model_dump()

    async def update_snippet(self, snippet_id: str, data: dict[str, Any], username: str = "") -> dict[str, Any] | None:
        existing = await self.get_snippet(snippet_id)
        if not existing:
            return None
        update = {k: v for k, v in data.items() if k not in {"id", "project_id", "created_at", "created_by"}}
        if "sql" in update:
            validate_readonly_sql(update["sql"])
        update["updated_by"] = username
        update["updated_at"] = _now()
        await self.db["sql_snippets"].update_one({"id": snippet_id}, {"$set": update})
        return await self.get_snippet(snippet_id)

    async def delete_snippet(self, snippet_id: str) -> bool:
        result = await self.db["sql_snippets"].delete_one({"id": snippet_id})
        return result.deleted_count > 0

    async def test_service(self, service_id: str, project_id: str) -> dict[str, Any]:
        return await self.run_sql(project_id=project_id, db_service_id=service_id, sql_text="SELECT 1 AS ok", params={}, context={})

    async def run_snippet(self, project_id: str, snippet_id: str, params: dict[str, Any] | None = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
        snippet = await self.get_snippet(snippet_id)
        if not snippet or snippet.get("project_id") != project_id or not snippet.get("enabled", True):
            raise HTTPException(404, "SQL snippet not found")
        return await self.run_sql(
            project_id=project_id,
            db_service_id=snippet["db_service_id"],
            sql_text=snippet["sql"],
            params=params or {},
            context=context or {},
            timeout_ms=int(snippet.get("timeout_ms") or 5000),
            max_rows=int(snippet.get("max_rows") or 100),
        )

    async def validate_ref(self, project_id: str, query: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
        if query.get("sql_ref"):
            snippet = await self.get_snippet(query["sql_ref"])
            if not snippet or snippet.get("project_id") != project_id:
                raise HTTPException(404, "SQL snippet not found")
            sql_text = snippet.get("sql", "")
            db_service_id = snippet.get("db_service_id", "")
            params = query.get("params") or {}
        else:
            sql_text = query.get("sql_text", "")
            db_service_id = query.get("db_service_id", "")
            params = query.get("params") or {}
        return await self.validate_sql(project_id, db_service_id, sql_text, params=params, context=context or {})

    async def run_ref(self, project_id: str, query: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
        if query.get("sql_ref"):
            return await self.run_snippet(project_id, query["sql_ref"], params=query.get("params") or {}, context=context or {})
        return await self.run_sql(
            project_id=project_id,
            db_service_id=query.get("db_service_id", ""),
            sql_text=query.get("sql_text", ""),
            params=query.get("params") or {},
            context=context or {},
            timeout_ms=query.get("timeout_ms"),
            max_rows=query.get("max_rows"),
        )

    async def validate_sql(
        self,
        project_id: str,
        db_service_id: str,
        sql_text: str,
        params: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        service = await self.get_service(db_service_id) if db_service_id else None
        if db_service_id and (not service or service.get("project_id") != project_id):
            raise HTTPException(404, "Database service not found")
        sql = normalize_readonly_sql(sql_text)
        rendered_params = render_value(params or {}, context or {})
        return {
            "ok": True,
            "error_code": "",
            "error": "",
            "message": "SQL is valid",
            "rendered_sql_preview": sql[:1000],
            "rendered_params": rendered_params,
            "db_service_id": db_service_id,
        }

    async def run_sql(
        self,
        project_id: str,
        db_service_id: str,
        sql_text: str,
        params: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        timeout_ms: int | None = None,
        max_rows: int | None = None,
    ) -> dict[str, Any]:
        service = await self.get_service(db_service_id)
        if not service or service.get("project_id") != project_id or not service.get("enabled", True):
            raise HTTPException(404, "Database service not found")
        sql = normalize_readonly_sql(sql_text)
        rendered_params = render_value(params or {}, context or {})
        limit = max(1, min(int(max_rows or service.get("max_rows") or 100), int(service.get("max_rows") or 100), 1000))
        timeout = max(500, min(int(timeout_ms or service.get("timeout_ms") or 5000), 30000))
        start = time.perf_counter()
        try:
            rows = await asyncio.wait_for(self._execute(service, sql, rendered_params, limit), timeout=timeout / 1000)
            duration_ms = int((time.perf_counter() - start) * 1000)
            columns = list(rows[0].keys()) if rows else []
            first = rows[0] if rows else None
            scalar = next(iter(first.values())) if isinstance(first, dict) and first else None
            return {
                "ok": True,
                "error_code": "",
                "error": "",
                "message": "ok",
                "columns": columns,
                "rows": rows,
                "first": first,
                "scalar": scalar,
                "row_count": len(rows),
                "duration_ms": duration_ms,
                "truncated": len(rows) >= limit,
                "rendered_sql_preview": sql[:1000],
                "rendered_params": rendered_params,
            }
        except HTTPException:
            raise
        except asyncio.TimeoutError:
            return {
                **_error_result("SQL execution timed out", "timeout", int((time.perf_counter() - start) * 1000)),
                "rendered_sql_preview": sql[:1000],
                "rendered_params": rendered_params,
            }
        except Exception as e:
            return {
                **_error_result(str(e)[:300], "execution_error", int((time.perf_counter() - start) * 1000)),
                "rendered_sql_preview": sql[:1000],
                "rendered_params": rendered_params,
            }

    async def _execute(self, service: dict[str, Any], sql: str, params: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        db_type = service.get("type")
        sql_limited = f"SELECT * FROM ({sql.rstrip(';')}) AS apipulse_sql_limit LIMIT {limit}"
        if db_type == "postgresql":
            return await self._execute_postgres(service, sql_limited, params)
        if db_type == "mysql":
            return await self._execute_mysql(service, sql_limited, params)
        if db_type == "sqlite":
            return await self._execute_sqlite(service, sql_limited, params)
        raise HTTPException(422, f"Unsupported database type: {db_type}")

    async def _execute_postgres(self, service: dict[str, Any], sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        import asyncpg
        conn = await asyncpg.connect(
            host=service.get("host") or "localhost",
            port=int(service.get("port") or 5432),
            database=service.get("database") or "",
            user=service.get("username") or "",
            password=decrypt_password(service.get("password_encrypted") or ""),
        )
        try:
            try:
                await conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            except Exception:
                logger.warning("Failed to set PostgreSQL session READ ONLY, continuing anyway")
            rows = await conn.fetch(sql, *list(params.values()))
            return [{k: _sanitize_cell(v) for k, v in dict(row).items()} for row in rows]
        finally:
            await conn.close()

    async def _execute_mysql(self, service: dict[str, Any], sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        import aiomysql
        conn = await aiomysql.connect(
            host=service.get("host") or "localhost",
            port=int(service.get("port") or 3306),
            db=service.get("database") or "",
            user=service.get("username") or "",
            password=decrypt_password(service.get("password_encrypted") or ""),
        )
        try:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                try:
                    await cur.execute("SET SESSION TRANSACTION READ ONLY")
                except Exception:
                    logger.warning("Failed to set MySQL session READ ONLY, continuing anyway")
                await cur.execute(sql, tuple(params.values()))
                rows = await cur.fetchall()
                return [{k: _sanitize_cell(v) for k, v in dict(row).items()} for row in rows]
        finally:
            conn.close()

    async def _execute_sqlite(self, service: dict[str, Any], sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        path = service.get("database") or service.get("options", {}).get("path") or ""
        if not path:
            raise HTTPException(422, "SQLite database path is required")
        resolved = Path(path).expanduser()
        if not _sqlite_path_allowed(resolved):
            raise HTTPException(403, "SQLite path is not allowed")
        if not resolved.exists():
            raise HTTPException(404, "SQLite database file not found")

        def run() -> list[dict[str, Any]]:
            conn = sqlite3.connect(f"file:{resolved.resolve()}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                cur = conn.execute(sql, tuple(params.values()))
                return [{k: _sanitize_cell(v) for k, v in dict(row).items()} for row in cur.fetchall()]
            finally:
                conn.close()
        return await asyncio.to_thread(run)


def summarize_sql_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "columns": result.get("columns") or [],
        "first": result.get("first"),
        "scalar": result.get("scalar"),
        "row_count": result.get("row_count", 0),
        "duration_ms": result.get("duration_ms", 0),
        "truncated": bool(result.get("truncated")),
        "error": result.get("error", ""),
    }
