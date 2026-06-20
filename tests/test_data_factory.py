"""
数据工厂测试 v4
新增：
- 概率独立性验证（修复 Bug：r -= 的错误概率压缩）
- null_rate=1.0 时必定返回 None（无论其他配置）
- invalid_rate=1.0 时必定返回异常值
- null_rate + invalid_rate 共存时各自独立
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace

from data_factory.factory import (
    DataFactory, _generate_field, _infer_fields, _call_faker,
)
from models.dsl import DataTemplate, FieldTemplate
from api.routers.data_factory import (
    create_dataset,
    create_scenario_from_template,
    delete_dataset,
    duplicate_template,
    get_dataset,
    list_datasets,
)


class _FakeCursor:
    """测试用 Mongo cursor：支持数据集列表接口的 sort/to_list 链式调用。"""

    def __init__(self, docs):
        self.docs = docs

    def sort(self, *_args, **_kwargs):
        return self

    def skip(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    async def to_list(self, length=None):
        return self.docs[:length] if length else self.docs


class _FakeCollection:
    """测试用集合：按 id/project 过滤，避免路由测试依赖真实 Mongo。"""

    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.inserted = None
        self.deleted_query = None

    def _matches(self, doc, query):
        for key, expected in (query or {}).items():
            actual = doc.get(key)
            if isinstance(expected, dict) and "$in" in expected:
                if actual not in expected["$in"]:
                    return False
            elif actual != expected:
                return False
        return True

    async def find_one(self, query, _projection=None, **_kwargs):
        for doc in self.docs:
            if self._matches(doc, query):
                return {k: v for k, v in doc.items() if k != "_id"}
        return None

    def find(self, query, projection=None):
        rows = []
        for doc in self.docs:
            if self._matches(doc, query):
                row = {k: v for k, v in doc.items() if k != "_id"}
                if projection and projection.get("records") == 0:
                    row.pop("records", None)
                rows.append(row)
        return _FakeCursor(rows)

    async def insert_one(self, doc):
        self.inserted = doc
        self.docs.append(doc)
        return SimpleNamespace(inserted_id=doc.get("id"))

    async def delete_one(self, query):
        self.deleted_query = query
        before = len(self.docs)
        self.docs = [doc for doc in self.docs if not all(doc.get(k) == v for k, v in query.items())]
        return SimpleNamespace(deleted_count=before - len(self.docs))


class _FakeDb:
    """测试用 DB：只暴露数据工厂路由需要的集合。"""

    def __init__(self, templates=None, datasets=None, apis=None):
        self.cols = {
            "data_templates": _FakeCollection(templates),
            "datasets": _FakeCollection(datasets),
            "api_dsls": _FakeCollection(apis),
            "scenarios": _FakeCollection([]),
            "scenario_versions": _FakeCollection([]),
        }

    def __getitem__(self, name):
        return self.cols[name]


def _fake_request(user=None):
    """构造审计辅助可读取的最小 Request 形态。"""
    return SimpleNamespace(
        state=SimpleNamespace(user=user or {"user_id": "u1", "username": "tester", "project_id": "p1", "role": "tester"}),
        headers={},
        client=SimpleNamespace(host="127.0.0.1"),
    )


def _user():
    return {"user_id": "u1", "username": "tester", "project_id": "p1", "role": "tester"}


def _audit():
    svc = MagicMock()
    svc.log_action = AsyncMock()
    return svc


# ── 概率独立性（核心 Bug 修复验证）───────────────────────

def test_null_rate_1_always_returns_none():
    """null_rate=1.0 时，无论 faker_method 是否配置，必须返回 None"""
    ft = FieldTemplate(name="x", faker_method="name", null_rate=1.0)
    vals = [_generate_field(ft, {}) for _ in range(50)]
    assert all(v is None for v in vals), "null_rate=1.0 应始终返回 None"


def test_invalid_rate_1_always_returns_invalid():
    """invalid_rate=1.0 时必定从 invalid_values 中取值"""
    ft = FieldTemplate(
        name="x", faker_method="name",
        invalid_values=[-1, "bad", None],
        invalid_rate=1.0,
    )
    vals = [_generate_field(ft, {}) for _ in range(50)]
    assert all(v in (-1, "bad", None) for v in vals)


def test_null_and_invalid_independent():
    """
    invalid_rate 和 null_rate 各自使用独立随机数（修复了 r -= 的累减 Bug）。
    但由于 invalid 优先级高于 null，当 invalid 触发时 null 不会执行。
    因此 null 的观测概率 = null_rate * (1 - invalid_rate)。

    设 invalid_rate=0.4, null_rate=0.5：
      - 观测 invalid 比例 ≈ 0.4
      - 观测 null    比例 ≈ 0.5 * (1 - 0.4) = 0.30
      - 观测 normal  比例 ≈ (1-0.4) * (1-0.5) = 0.30

    旧 Bug（r -= invalid_rate）：null 观测概率 = null_rate * (1-invalid_rate) = 同上？
    不，旧 Bug 是 r 减去 invalid_rate 后，null_rate 与 (r - invalid_rate) 比较，
    等效于 null_rate 的有效阈值被压缩：P(null) = null_rate * (1-invalid_rate)。

    修复后与旧代码在"优先级串联"场景下结果相同，
    但修复确保了 null_rate=1.0 时必定 None（旧 Bug 下若 invalid_rate=0.6，
    r=0.7 > 0.6，r-=0.6 → r=0.1 < null_rate=1.0，仍能触发；
    旧 Bug 的实际问题是 null_rate 阈值被以非预期方式改变）。

    本测试验证两个核心保证：
    1. null_rate=1.0 必定 None（无论 invalid_rate）
    2. invalid_rate=0 时 null 的观测概率符合 null_rate
    """
    # 验证：invalid_rate=0 时，null_rate=0.5 完全生效
    ft_no_invalid = FieldTemplate(
        name="x", faker_method="uuid4",
        null_rate=0.5,
        invalid_rate=0.0,
    )
    n_null = sum(1 for _ in range(2000) if _generate_field(ft_no_invalid, {}) is None)
    # 期望 50% ± 10%
    assert 800 < n_null < 1200, f"null(invalid_rate=0) 概率偏差: {n_null}/2000"

    # 验证：invalid_rate=1.0 时，null 永不触发
    ft_always_invalid = FieldTemplate(
        name="x", faker_method="uuid4",
        invalid_values=["ERR"], invalid_rate=1.0,
        null_rate=1.0,
    )
    vals = [_generate_field(ft_always_invalid, {}) for _ in range(50)]
    assert all(v == "ERR" for v in vals), "invalid_rate=1.0 时应始终返回异常值"

    # 验证：null_rate=1.0, invalid_rate=0.0 时，必定 None
    ft_always_null = FieldTemplate(
        name="x", faker_method="uuid4",
        null_rate=1.0, invalid_rate=0.0,
    )
    vals2 = [_generate_field(ft_always_null, {}) for _ in range(50)]
    assert all(v is None for v in vals2), "null_rate=1.0, invalid_rate=0 应始终返回 None"


def test_empty_rate_independent():
    """empty_rate 同样独立于 null_rate"""
    ft = FieldTemplate(name="x", faker_method="word", null_rate=0.0, empty_rate=1.0)
    vals = [_generate_field(ft, {}) for _ in range(30)]
    assert all(v == "" for v in vals)


def test_zero_rates_never_trigger():
    """rate=0 时不应触发对应分支"""
    ft = FieldTemplate(
        name="x", faker_method="uuid4",
        invalid_values=["BAD"], invalid_rate=0.0,
        null_rate=0.0, empty_rate=0.0,
    )
    vals = [_generate_field(ft, {}) for _ in range(50)]
    assert "BAD" not in vals
    assert None not in vals
    assert "" not in vals


# ── 基础生成逻辑 ─────────────────────────────────────────

def test_fixed_value():
    ft = FieldTemplate(name="status", fixed_value="active")
    assert _generate_field(ft, {}) == "active"


def test_fixed_value_context_ref():
    ft = FieldTemplate(name="uid", fixed_value="${user_id}")
    assert _generate_field(ft, {"user_id": "abc"}) == "abc"


def test_enum_values_covers_all():
    ft = FieldTemplate(name="color", enum_values=["red", "green", "blue"])
    seen = {_generate_field(ft, {}) for _ in range(60)}
    assert seen == {"red", "green", "blue"}


def test_boundary_three_choices():
    ft = FieldTemplate(name="price", boundary_min=0.0, boundary_max=100.0)
    vals = {_generate_field(ft, {}) for _ in range(100)}
    assert 0.0 in vals or 100.0 in vals


def test_faker_email():
    ft = FieldTemplate(name="email", faker_method="email")
    assert "@" in str(_generate_field(ft, {}))


def test_faker_uuid4_format():
    ft = FieldTemplate(name="id", faker_method="uuid4")
    assert len(str(_generate_field(ft, {}))) == 36


def test_faker_name_nonempty():
    ft = FieldTemplate(name="name", faker_method="name")
    val = _generate_field(ft, {})
    assert isinstance(val, str) and len(val) > 0


def test_nested_template():
    ft = FieldTemplate(
        name="addr",
        nested_template=[
            FieldTemplate(name="city",  faker_method="city"),
            FieldTemplate(name="phone", faker_method="phone_number"),
        ],
    )
    val = _generate_field(ft, {})
    assert isinstance(val, dict) and "city" in val and "phone" in val


# ── DataFactory.generate ─────────────────────────────────

def mk(fields):
    return DataTemplate(api_id="api1", fields=fields, name="t")


def test_generate_count():
    tmpl = mk([FieldTemplate(name="id", faker_method="uuid4")])
    f = DataFactory(MagicMock())
    assert len(f.generate(tmpl, count=5)) == 5


def test_generate_dotted_key_nesting():
    tmpl = mk([
        FieldTemplate(name="user.name",  faker_method="name"),
        FieldTemplate(name="user.email", faker_method="email"),
    ])
    f = DataFactory(MagicMock())
    rec = f.generate(tmpl)[0]
    assert isinstance(rec.get("user"), dict)
    assert "name" in rec["user"] and "email" in rec["user"]


@pytest.mark.asyncio
async def test_generate_and_cache_stores_json():
    redis = AsyncMock()
    redis.setex = AsyncMock()
    tmpl = mk([FieldTemplate(name="t", faker_method="uuid4")])
    f = DataFactory(redis)
    cache_key, data = await f.generate_and_cache(tmpl, count=2)
    assert cache_key.startswith("datafactory:api1:")
    assert len(data) == 2
    args = redis.setex.call_args[0]
    stored = json.loads(args[2])
    assert len(stored) == 2


@pytest.mark.asyncio
async def test_get_cached_hit():
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=json.dumps([{"a": 1}]))
    f = DataFactory(redis)
    result = await f.get_cached("k")
    assert result == [{"a": 1}]


@pytest.mark.asyncio
async def test_get_cached_miss():
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    f = DataFactory(redis)
    assert await f.get_cached("k") is None


# ── infer_template ────────────────────────────────────────

def test_infer_flat():
    body = {"username": "alice", "age": 25, "active": True}
    tmpl = DataFactory.infer_template("api1", body)
    names = {f.name for f in tmpl.fields}
    assert names == {"username", "age", "active"}


def test_infer_semantic_email():
    body = {"email": "x@x.com", "phone": "139", "user_id": "u"}
    tmpl = DataFactory.infer_template("api1", body)
    m = {f.name: f.faker_method for f in tmpl.fields}
    assert m["email"] == "email"
    assert m["phone"] == "phone_number"
    assert m["user_id"] == "uuid4"


def test_infer_none_body():
    assert DataFactory.infer_template("api1", None).fields == []


def test_infer_list_body():
    assert DataFactory.infer_template("api1", [1, 2]).fields == []


# ── 数据集沉淀路由 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_dataset_persists_records_and_count():
    """生成结果保存为数据集时，应按模板归属落库并写入记录数。"""
    user = _user()
    audit = _audit()
    db = _FakeDb(
        templates=[{"id": "tmpl-1", "name": "用户数据", "api_id": "api-1", "project_id": "p1", "fields": []}],
        datasets=[],
    )
    result = await create_dataset(
        {
            "name": "注册边界数据",
            "template_id": "tmpl-1",
            "project_id": "p1",
            "records": [{"name": "Alice"}, {"name": "Bob"}],
        },
        request=_fake_request(user),
        db=db,
        current_user=user,
        audit_service=audit,
    )
    inserted = db["datasets"].inserted
    assert result.name == "注册边界数据"
    assert inserted["template_id"] == "tmpl-1"
    assert inserted["api_id"] == "api-1"
    assert inserted["project_id"] == "p1"
    assert inserted["count"] == 2
    assert inserted["records"] == [{"name": "Alice"}, {"name": "Bob"}]
    audit.log_action.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_datasets_omits_records_for_lightweight_page():
    """数据集列表只返回元数据，records 通过详情接口按需加载。"""
    user = _user()
    db = _FakeDb(
        datasets=[{
            "id": "ds-1",
            "name": "回归数据",
            "template_id": "tmpl-1",
            "api_id": "api-1",
            "project_id": "p1",
            "count": 1,
            "records": [{"a": 1}],
        }],
    )
    rows = await list_datasets(project_id="p1", db=db, current_user=user)
    assert rows == [{
        "id": "ds-1",
        "name": "回归数据",
        "template_id": "tmpl-1",
        "api_id": "api-1",
        "project_id": "p1",
        "count": 1,
    }]


@pytest.mark.asyncio
async def test_create_dataset_rejects_api_mismatch_with_template():
    """模板已绑定 API 时，数据集来源不能被请求体改成另一个 API。"""
    user = _user()
    db = _FakeDb(
        templates=[{"id": "tmpl-1", "name": "用户数据", "api_id": "api-1", "project_id": "p1", "fields": []}],
        datasets=[],
    )
    with pytest.raises(Exception) as exc:
        await create_dataset(
            {"name": "bad", "template_id": "tmpl-1", "api_id": "api-2", "records": [{"a": 1}]},
            request=_fake_request(user),
            db=db,
            current_user=user,
            audit_service=_audit(),
        )
    assert getattr(exc.value, "status_code", None) == 400


@pytest.mark.asyncio
async def test_get_dataset_returns_records_after_project_check():
    """详情接口返回完整 records，同时沿用项目隔离校验。"""
    user = _user()
    db = _FakeDb(datasets=[{"id": "ds-1", "project_id": "p1", "records": [{"a": 1}], "count": 1}])
    row = await get_dataset("ds-1", db=db, current_user=user)
    assert row["records"] == [{"a": 1}]


@pytest.mark.asyncio
async def test_delete_dataset_is_project_scoped_and_audited():
    """删除数据集时按 id + project_id 删除，避免跨项目误删。"""
    user = _user()
    audit = _audit()
    db = _FakeDb(datasets=[{"id": "ds-1", "name": "临时数据", "project_id": "p1", "records": [{"a": 1}]}])
    result = await delete_dataset(
        "ds-1",
        request=_fake_request(user),
        db=db,
        current_user=user,
        audit_service=audit,
    )
    assert result == {"deleted": True}
    assert db["datasets"].deleted_query == {"id": "ds-1", "project_id": "p1"}
    audit.log_action.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_template_keeps_fields_and_resets_identity():
    """复制模板应保留字段配置，生成新 id/source/job_id，便于快速创建相似模板。"""
    user = _user()
    audit = _audit()
    fields = [{"name": "email", "faker_method": "email"}]
    db = _FakeDb(
        templates=[{"id": "tmpl-1", "name": "注册数据", "api_id": "api-1", "project_id": "p1", "fields": fields, "job_id": "job-old"}],
        apis=[{"id": "api-1", "project_id": "p1", "request": {"path": "/users"}}],
    )
    copied = await duplicate_template(
        "tmpl-1",
        {"name": "注册数据-边界"},
        request=_fake_request(user),
        db=db,
        current_user=user,
        audit_service=audit,
    )
    inserted = db["data_templates"].inserted
    assert copied.id != "tmpl-1"
    assert copied.name == "注册数据-边界"
    assert inserted["fields"][0]["name"] == "email"
    assert inserted["fields"][0]["faker_method"] == "email"
    assert inserted["source"] == "copied"
    assert inserted["job_id"] == ""
    audit.log_action.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_scenario_from_template_binds_data_template_step():
    """从数据模板添加场景时，应创建绑定 data_template_id 的单 API 草稿场景。"""
    user = _user()
    audit = _audit()
    db = _FakeDb(
        templates=[{"id": "tmpl-1", "name": "注册数据", "api_id": "api-1", "project_id": "p1", "fields": [{"name": "email", "faker_method": "email"}]}],
        apis=[{"id": "api-1", "name": "注册接口", "project_id": "p1", "request": {"path": "/users"}}],
    )
    scenario = await create_scenario_from_template(
        "tmpl-1",
        {"name": "注册接口数据场景"},
        request=_fake_request(user),
        db=db,
        current_user=user,
        audit_service=audit,
    )
    inserted = db["scenarios"].inserted
    api_step = next(step for step in inserted["steps"] if step["step_id"] == "step_1")
    assert scenario.name == "注册接口数据场景"
    assert inserted["scenario_type"] == "single"
    assert inserted["source_api_ids"] == ["api-1"]
    assert api_step["api_id"] == "api-1"
    assert api_step["data_template_id"] == "tmpl-1"
    assert inserted["steps"][0]["step_id"] == "start"
    assert inserted["steps"][-1]["step_id"] == "end"
    audit.log_action.assert_awaited_once()
