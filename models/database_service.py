from __future__ import annotations

from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)


class DatabaseType(str, Enum):
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    SQLITE = "sqlite"


class DatabaseServiceConfig(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str = "default"
    name: str
    type: DatabaseType = DatabaseType.POSTGRESQL
    host: str = ""
    port: int = 0
    database: str = ""
    username: str = ""
    password_encrypted: str = ""
    options: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    read_only: bool = True
    timeout_ms: int = 5000
    max_rows: int = 100
    created_by: str = ""
    updated_by: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class SqlSnippet(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str = "default"
    db_service_id: str
    name: str
    sql: str
    params_schema: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    enabled: bool = True
    timeout_ms: int = 5000
    max_rows: int = 100
    created_by: str = ""
    updated_by: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class SqlQueryRef(BaseModel):
    name: str = ""
    sql_ref: str = ""
    sql_text: str = ""
    db_service_id: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    target_var: str = ""
    timeout_ms: int | None = None
    max_rows: int | None = None
