from __future__ import annotations

import sqlite3

import pytest
from fastapi import HTTPException

from services.sql_runtime_service import SqlRuntimeService, render_value, validate_readonly_sql


class _Cursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, *_args):
        return self

    async def to_list(self, _limit):
        return self.docs


class _Collection:
    def __init__(self, docs):
        self.docs = docs

    async def find_one(self, query, *_args):
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                return dict(doc)
        return None

    def find(self, query, *_args):
        rows = [dict(doc) for doc in self.docs if all(doc.get(k) == v for k, v in query.items())]
        return _Cursor(rows)


class _Db:
    def __init__(self, services=None, snippets=None):
        self.collections = {
            "database_services": _Collection(services or []),
            "sql_snippets": _Collection(snippets or []),
        }

    def __getitem__(self, name):
        return self.collections[name]


def test_readonly_sql_validation_handles_comments_literals_and_with():
    validate_readonly_sql("WITH base AS (SELECT 1 AS id) SELECT id FROM base;")
    validate_readonly_sql("SELECT 'delete from users' AS text -- update orders\n")
    validate_readonly_sql("SELECT \"drop table\" AS label")

    for sql in [
        "SELECT 1; SELECT 2",
        "UPDATE users SET name='x'",
        "SELECT 1 /* ok */; DELETE FROM users",
        "INSERT INTO logs VALUES (1)",
    ]:
        with pytest.raises(HTTPException):
            validate_readonly_sql(sql)


def test_render_value_supports_nested_context_paths():
    context = {"sql": {"user": {"first": {"id": 42}, "scalar": "ok"}}, "response": {"token": "abc"}}
    value = {
        "id": "{{sql.user.first.id}}",
        "token": "Bearer {{response.token}}",
        "nested": ["{{sql.user.scalar}}"],
    }

    assert render_value(value, context) == {"id": "42", "token": "Bearer abc", "nested": ["ok"]}


@pytest.mark.asyncio
async def test_sql_runtime_sqlite_success_and_truncation(tmp_path):
    db_file = tmp_path / "runtime.sqlite"
    conn = sqlite3.connect(db_file)
    try:
        conn.execute("CREATE TABLE users (id INTEGER, name TEXT)")
        conn.executemany("INSERT INTO users VALUES (?, ?)", [(1, "Ada"), (2, "Grace")])
        conn.commit()
    finally:
        conn.close()

    service_doc = {
        "id": "svc1",
        "project_id": "p1",
        "type": "sqlite",
        "database": str(db_file),
        "enabled": True,
        "timeout_ms": 5000,
        "max_rows": 10,
    }
    service = SqlRuntimeService(_Db(services=[service_doc]))

    result = await service.run_sql("p1", "svc1", "SELECT id, name FROM users ORDER BY id", max_rows=1)

    assert result["ok"] is True
    assert result["columns"] == ["id", "name"]
    assert result["first"] == {"id": 1, "name": "Ada"}
    assert result["scalar"] == 1
    assert result["row_count"] == 1
    assert result["truncated"] is True
    assert result["rendered_sql_preview"].startswith("SELECT id")


@pytest.mark.asyncio
async def test_sql_runtime_returns_structured_execution_error(tmp_path):
    db_file = tmp_path / "runtime.sqlite"
    sqlite3.connect(db_file).close()
    service_doc = {
        "id": "svc1",
        "project_id": "p1",
        "type": "sqlite",
        "database": str(db_file),
        "enabled": True,
        "timeout_ms": 5000,
        "max_rows": 10,
    }
    service = SqlRuntimeService(_Db(services=[service_doc]))

    result = await service.run_sql("p1", "svc1", "SELECT missing FROM nowhere")

    assert result["ok"] is False
    assert result["error_code"] == "execution_error"
    assert result["error"]
    assert result["columns"] == []
    assert result["rows"] == []
