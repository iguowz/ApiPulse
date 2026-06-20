"""
数据工厂 v2
- 递归嵌套字段生成
- null_rate / empty_rate / invalid_rate 三路随机
- 边界值：严格下界、严格上界、中间值三选一
- 缓存：Redis SETEX + 管道批量写
- infer_template：递归推断嵌套结构
"""
from __future__ import annotations

import asyncio
import json
import random
import uuid
from typing import Any

from faker import Faker
from loguru import logger
from redis.asyncio import Redis

from models.dsl import DataTemplate, FieldTemplate

_faker_cn = Faker("zh_CN")
_faker_en = Faker("en_US")

DATA_TTL = 3600  # 1 小时

# 白名单：faker 方法名 → (Faker 实例, 属性名)
# v3 扩展至 80+ 常用方法，按分类组织
_FAKER_DISPATCH: dict[str, tuple[Any, str]] = {
    # ── 个人信息（中文） ──
    "name": (_faker_cn, "name"),
    "first_name": (_faker_cn, "first_name"),
    "last_name": (_faker_cn, "last_name"),
    "name_male": (_faker_cn, "name_male"),
    "name_female": (_faker_cn, "name_female"),
    "phone_number": (_faker_cn, "phone_number"),
    "ssn": (_faker_cn, "ssn"),                 # 身份证号
    "company": (_faker_cn, "company"),
    "company_prefix": (_faker_cn, "company_prefix"),
    "job": (_faker_cn, "job"),
    # ── 地址（中文） ──
    "address": (_faker_cn, "address"),
    "city": (_faker_cn, "city"),
    "province": (_faker_cn, "province"),
    "country": (_faker_cn, "country"),
    "street_address": (_faker_cn, "street_address"),
    "street_name": (_faker_cn, "street_name"),
    "postcode": (_faker_cn, "postcode"),
    "district": (_faker_cn, "district"),
    # ── 金融（通用） ──
    "credit_card_number": (_faker_en, "credit_card_number"),
    "credit_card_provider": (_faker_en, "credit_card_provider"),
    "credit_card_expire": (_faker_en, "credit_card_expire"),
    "credit_card_security_code": (_faker_en, "credit_card_security_code"),
    "iban": (_faker_en, "iban"),
    "swift": (_faker_en, "swift"),
    "currency_code": (_faker_en, "currency_code"),
    "currency_name": (_faker_en, "currency_name"),
    "currency_symbol": (_faker_en, "currency_symbol"),
    # ── 互联网（通用） ──
    "email": (_faker_en, "email"),
    "safe_email": (_faker_en, "safe_email"),
    "free_email": (_faker_en, "free_email"),
    "company_email": (_faker_en, "company_email"),
    "user_name": (_faker_en, "user_name"),
    "password": (_faker_en, "password"),
    "url": (_faker_en, "url"),
    "domain_name": (_faker_en, "domain_name"),
    "ipv4": (_faker_en, "ipv4"),
    "ipv6": (_faker_en, "ipv6"),
    "mac_address": (_faker_en, "mac_address"),
    "slug": (_faker_en, "slug"),
    "image_url": (_faker_en, "image_url"),
    # ── 时间（通用） ──
    "date": (_faker_en, "date"),
    "date_time": (_faker_en, "date_time"),
    "iso8601": (_faker_en, "iso8601"),
    "unix_time": (_faker_en, "unix_time"),
    "time": (_faker_en, "time"),
    "date_of_birth": (_faker_en, "date_of_birth"),
    "date_between": (_faker_en, "date_between"),
    "day_of_week": (_faker_en, "day_of_week"),
    "month_name": (_faker_en, "month_name"),
    "year": (_faker_en, "year"),
    "am_pm": (_faker_en, "am_pm"),
    "century": (_faker_en, "century"),
    # ── 文本（通用） ──
    "text": (_faker_en, "text"),
    "sentence": (_faker_en, "sentence"),
    "paragraph": (_faker_en, "paragraph"),
    "word": (_faker_en, "word"),
    "words": (_faker_en, "words"),
    "texts": (_faker_en, "texts"),
    "sentences": (_faker_en, "sentences"),
    "paragraphs": (_faker_en, "paragraphs"),
    # ── 哈希/标识（通用） ──
    "uuid4": (_faker_en, "uuid4"),
    "md5": (_faker_en, "md5"),
    "sha256": (_faker_en, "sha256"),
    "ean13": (_faker_en, "ean13"),
    "ean8": (_faker_en, "ean8"),
    "isbn10": (_faker_en, "isbn10"),
    "isbn13": (_faker_en, "isbn13"),
    # ── 数值/随机（通用） ──
    "random_int": (_faker_en, "random_int"),
    "random_number": (_faker_en, "random_number"),
    "random_digit": (_faker_en, "random_digit"),
    "random_element": (_faker_en, "random_element"),
    "random_letter": (_faker_en, "random_letter"),
    "boolean": (_faker_en, "boolean"),
    "pybool": (_faker_en, "pybool"),
    "pyint": (_faker_en, "pyint"),
    "pyfloat": (_faker_en, "pyfloat"),
    "pystr": (_faker_en, "pystr"),
    # ── 格式生成（通用） ──
    "bothify": (_faker_en, "bothify"),
    "numerify": (_faker_en, "numerify"),
    "lexify": (_faker_en, "lexify"),
    "hexify": (_faker_en, "hexify"),
    # ── 文件/系统（通用） ──
    "file_name": (_faker_en, "file_name"),
    "file_extension": (_faker_en, "file_extension"),
    "file_path": (_faker_en, "file_path"),
    "mime_type": (_faker_en, "mime_type"),
    "unix_device": (_faker_en, "unix_device"),
    "unix_partition": (_faker_en, "unix_partition"),
    # ── 颜色（通用） ──
    "color_name": (_faker_en, "color_name"),
    "hex_color": (_faker_en, "hex_color"),
    "rgb_color": (_faker_en, "rgb_color"),
    # ── 地理（通用） ──
    "latitude": (_faker_en, "latitude"),
    "longitude": (_faker_en, "longitude"),
    "local_latlng": (_faker_en, "local_latlng"),
    "location_on_land": (_faker_en, "location_on_land"),
}

# P0-1: faker 方法分组元数据（供 GET /datafactory/faker-methods 端点下发，前端动态渲染分组下拉）
# 分组标签与 _FAKER_DISPATCH 的注释分组一一对应，用户按语义类别快速定位方法
_FAKER_GROUPS: list[dict[str, Any]] = [
    {"group": "personal", "label_zh": "个人信息", "label_en": "Personal",
     "methods": ["name", "first_name", "last_name", "name_male", "name_female", "phone_number", "ssn", "company", "company_prefix", "job"]},
    {"group": "address", "label_zh": "地址", "label_en": "Address",
     "methods": ["address", "city", "province", "country", "street_address", "street_name", "postcode", "district"]},
    {"group": "finance", "label_zh": "金融", "label_en": "Finance",
     "methods": ["credit_card_number", "credit_card_provider", "credit_card_expire", "credit_card_security_code", "iban", "swift", "currency_code", "currency_name", "currency_symbol"]},
    {"group": "internet", "label_zh": "互联网", "label_en": "Internet",
     "methods": ["email", "safe_email", "free_email", "company_email", "user_name", "password", "url", "domain_name", "ipv4", "ipv6", "mac_address", "slug", "image_url"]},
    {"group": "datetime", "label_zh": "时间", "label_en": "DateTime",
     "methods": ["date", "date_time", "iso8601", "unix_time", "time", "date_of_birth", "date_between", "day_of_week", "month_name", "year", "am_pm", "century"]},
    {"group": "text", "label_zh": "文本", "label_en": "Text",
     "methods": ["text", "sentence", "paragraph", "word", "words", "texts", "sentences", "paragraphs"]},
    {"group": "id", "label_zh": "哈希/标识", "label_en": "ID & Hash",
     "methods": ["uuid4", "md5", "sha256", "ean13", "ean8", "isbn10", "isbn13"]},
    {"group": "number", "label_zh": "数值/随机", "label_en": "Number & Random",
     "methods": ["random_int", "random_number", "random_digit", "random_element", "random_letter", "boolean", "pybool", "pyint", "pyfloat", "pystr"]},
    {"group": "format", "label_zh": "格式生成", "label_en": "Format",
     "methods": ["bothify", "numerify", "lexify", "hexify"]},
    {"group": "file", "label_zh": "文件/系统", "label_en": "File & System",
     "methods": ["file_name", "file_extension", "file_path", "mime_type", "unix_device", "unix_partition"]},
    {"group": "color", "label_zh": "颜色", "label_en": "Color",
     "methods": ["color_name", "hex_color", "rgb_color"]},
    {"group": "geo", "label_zh": "地理", "label_en": "Geo",
     "methods": ["latitude", "longitude", "local_latlng", "location_on_land"]},
]


def get_faker_methods() -> list[dict[str, Any]]:
    """P0-1: 返回 faker 方法分组元数据，供前端动态渲染分组下拉。
    消除前端硬编码 25 个 vs 后端白名单 94 个的不一致问题。"""
    return [dict(item) for item in _FAKER_GROUPS]


def get_faker_method_names() -> set[str]:
    """返回后端支持的 faker 方法名集合，供模板校验复用。"""
    return set(_FAKER_DISPATCH.keys())


def _call_faker(method: str) -> Any:
    entry = _FAKER_DISPATCH.get(method)
    if not entry:
        return str(uuid.uuid4())
    instance, attr = entry
    fn = getattr(instance, attr, None)
    return fn() if fn else str(uuid.uuid4())


def _generate_field(ft: FieldTemplate, context: dict[str, Any]) -> Any:
    """
    生成单个字段值。
    invalid_rate / null_rate / empty_rate 各自使用独立随机数，
    概率绝对独立，不相互压缩。优先级顺序：
    1. invalid_rate → 随机异常值
    2. null_rate    → None
    3. empty_rate   → ""
    4. fixed_value（支持 ${ctx_var} 引用）
    5. enum_values  → 随机选一
    6. boundary     → 下界 / 上界 / 中间 三选一
    7. nested_template → 递归 dict
    8. faker_method
    9. fallback uuid
    """
    # --- 1. invalid：独立随机数判断 ---
    if ft.invalid_values and ft.invalid_rate > 0 and random.random() < ft.invalid_rate:
        return random.choice(ft.invalid_values)

    # --- 2. null：独立随机数判断 ---
    if ft.null_rate > 0 and random.random() < ft.null_rate:
        return None

    # --- 3. empty：独立随机数判断 ---
    if ft.empty_rate > 0 and random.random() < ft.empty_rate:
        return ""

    # --- 4. fixed / context ref ---
    if ft.fixed_value is not None:
        if isinstance(ft.fixed_value, str) and ft.fixed_value.startswith("${"):
            var = ft.fixed_value[2:-1]
            return context.get(var, ft.fixed_value)
        return ft.fixed_value

    # --- 5. enum ---
    if ft.enum_values:
        return random.choice(ft.enum_values)

    # --- 6. boundary ---
    if ft.boundary_min is not None and ft.boundary_max is not None:
        choice = random.choice(["min", "max", "mid"])
        if choice == "min":
            return ft.boundary_min
        if choice == "max":
            return ft.boundary_max
        return round((ft.boundary_min + ft.boundary_max) / 2, 4)

    # --- 7. nested ---
    if ft.nested_template:
        return {
            sub.name.split(".")[-1]: _generate_field(sub, context)
            for sub in ft.nested_template
        }

    # --- 8. faker ---
    if ft.faker_method:
        return _call_faker(ft.faker_method)

    # --- 9. fallback ---
    return str(uuid.uuid4())


def _infer_fields(obj: Any, prefix: str = "") -> list[FieldTemplate]:
    """递归推断字段模板（最多 3 层）"""
    if not isinstance(obj, dict) or len(prefix.split(".")) > 3:
        return []

    fields: list[FieldTemplate] = []
    for key, val in obj.items():
        full_name = f"{prefix}.{key}" if prefix else key

        if isinstance(val, dict) and val:
            nested = _infer_fields(val, "")
            ft = FieldTemplate(name=full_name, nested_template=nested or None)
        elif isinstance(val, list):
            ft = FieldTemplate(name=full_name, faker_method="uuid4")
        elif isinstance(val, bool):
            ft = FieldTemplate(name=full_name, enum_values=[True, False])
        elif isinstance(val, int):
            # 推断合理边界
            lo = max(0, val - abs(val) * 2)
            hi = val + abs(val) * 2 + 1
            ft = FieldTemplate(name=full_name, boundary_min=float(lo), boundary_max=float(hi))
        elif isinstance(val, float):
            lo = val - abs(val) * 2
            hi = val + abs(val) * 2 + 1e-6
            ft = FieldTemplate(name=full_name, boundary_min=lo, boundary_max=hi)
        elif isinstance(val, str):
            # 语义识别：email / phone / url / uuid
            lk = key.lower()
            if any(k in lk for k in ("email", "mail")):
                ft = FieldTemplate(name=full_name, faker_method="email")
            elif any(k in lk for k in ("phone", "mobile", "tel")):
                ft = FieldTemplate(name=full_name, faker_method="phone_number")
            elif any(k in lk for k in ("url", "link", "href")):
                ft = FieldTemplate(name=full_name, faker_method="url")
            elif any(k in lk for k in ("id", "uuid", "token", "key", "secret")):
                ft = FieldTemplate(name=full_name, faker_method="uuid4")
            elif any(k in lk for k in ("name", "username", "user")):
                ft = FieldTemplate(name=full_name, faker_method="name")
            elif any(k in lk for k in ("city", "addr", "address")):
                ft = FieldTemplate(name=full_name, faker_method="address")
            elif any(k in lk for k in ("date", "time", "at", "created", "updated")):
                ft = FieldTemplate(name=full_name, faker_method="iso8601")
            elif any(k in lk for k in ("pass", "pwd", "password")):
                ft = FieldTemplate(name=full_name, faker_method="password")
            else:
                ft = FieldTemplate(name=full_name, faker_method="word")
        else:
            ft = FieldTemplate(name=full_name, faker_method="uuid4")

        fields.append(ft)

    return fields


class DataFactory:
    def __init__(self, redis: Redis):
        self._redis = redis

    def generate(
        self,
        template: DataTemplate,
        context: dict[str, Any] | None = None,
        count: int = 1,
    ) -> list[dict[str, Any]]:
        ctx = context or {}
        # 防御：无字段模板不应生成空对象列表
        if not template.fields:
            logger.warning("DataFactory.generate called with empty fields (api_id={})", template.api_id)
            return []
        results = []
        for _ in range(count):
            record: dict[str, Any] = {}
            for ft in template.fields:
                # 扁平键名还是嵌套？支持 "a.b.c" → nested dict
                val = _generate_field(ft, ctx)
                parts = ft.name.split(".")
                target = record
                for p in parts[:-1]:
                    target = target.setdefault(p, {})
                target[parts[-1]] = val
            results.append(record)
        return results

    @staticmethod
    def validate_template(template: DataTemplate) -> list[dict[str, Any]]:
        """校验模板草稿，返回结构化问题列表。"""
        issues: list[dict[str, Any]] = []
        seen: set[str] = set()
        faker_names = get_faker_method_names()

        def walk(fields: list[FieldTemplate], prefix: str = ""):
            for ft in fields:
                path = f"{prefix}.{ft.name}" if prefix else ft.name
                if not ft.name or not ft.name.strip():
                    issues.append({"level": "error", "field": path, "message": "字段名不能为空"})
                    continue
                if path in seen:
                    issues.append({"level": "error", "field": path, "message": f"字段名重复: {path}"})
                seen.add(path)
                for key in ("null_rate", "empty_rate", "invalid_rate"):
                    val = getattr(ft, key, 0)
                    if val < 0 or val > 1:
                        issues.append({"level": "error", "field": path, "message": f"{key} 必须在 0 到 1 之间"})
                if ft.boundary_min is not None and ft.boundary_max is not None and ft.boundary_min > ft.boundary_max:
                    issues.append({"level": "error", "field": path, "message": "boundary_min 不能大于 boundary_max"})
                if ft.faker_method and ft.faker_method not in faker_names:
                    issues.append({"level": "error", "field": path, "message": f"不支持的 faker 方法: {ft.faker_method}"})
                if ft.nested_template:
                    walk(ft.nested_template, path)

        walk(template.fields)
        if not template.fields:
            issues.append({"level": "warning", "field": "", "message": "模板没有字段，无法生成有效数据"})
        return issues

    async def generate_and_cache(
        self,
        template: DataTemplate,
        context: dict[str, Any] | None = None,
        count: int = 1,
    ) -> tuple[str, list[dict[str, Any]]]:
        # 在线程池中运行同步 faker 生成，避免阻塞事件循环（大量字段/记录时 faker 调用密集）
        data = await asyncio.to_thread(self.generate, template, context, count)
        cache_key = f"datafactory:{template.api_id}:{uuid.uuid4().hex[:8]}"
        await self._redis.setex(cache_key, DATA_TTL, json.dumps(data, ensure_ascii=False, default=str))
        logger.debug("DataFactory cached {} records → {}", len(data), cache_key)
        return cache_key, data

    async def get_cached(self, cache_key: str) -> list[dict[str, Any]] | None:
        raw = await self._redis.get(cache_key)
        return json.loads(raw) if raw else None

    async def invalidate(self, cache_key: str) -> None:
        await self._redis.delete(cache_key)

    @staticmethod
    def infer_template(api_id: str, body: Any, name: str = "") -> DataTemplate:
        """从请求体递归推断 DataTemplate"""
        from datetime import datetime, timezone, timedelta
        fields = _infer_fields(body) if isinstance(body, dict) else []
        return DataTemplate(
            api_id=api_id,
            name=name or f"auto:{api_id[:8]}",
            fields=fields,
            source="inferred",
            updated_at=datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None),
        )
