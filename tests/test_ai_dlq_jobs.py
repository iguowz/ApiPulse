"""
P0: 通用 AI DLQ / Job 队列辅助测试。

覆盖共享 ai_analyze DLQ 的目标队列过滤、重试入队和队列摘要，避免 doc/asserts
等子队列在生产恢复时被错误地重投到主分析队列。
"""
from __future__ import annotations

import json

import pytest

from api.routers.dlq import (
    _list_dlq_items,
    _retry_dlq_item,
    _remove_dlq_item,
    get_queue_summary,
)


class FakeRedis:
    def __init__(self, lists: dict[str, list[str]] | None = None):
        self.lists = lists or {}

    async def lrange(self, key, start, end):
        items = list(self.lists.get(key, []))
        if end == -1:
            return items[start:]
        return items[start:end + 1]

    async def llen(self, key):
        return len(self.lists.get(key, []))

    async def lset(self, key, index, value):
        self.lists.setdefault(key, [])[index] = value

    async def lrem(self, key, count, value):
        items = self.lists.get(key, [])
        removed = 0
        kept = []
        for item in items:
            if item == value and (count == 0 or removed < count):
                removed += 1
                continue
            kept.append(item)
        self.lists[key] = kept
        return removed

    async def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])


def _dump(payload):
    return json.dumps(payload, ensure_ascii=False)


@pytest.mark.asyncio
async def test_shared_ai_analyze_dlq_filters_target_queue():
    redis = FakeRedis({
        "queue:ai_analyze:dlq": [
            _dump({"api_id": "api-main"}),
            _dump({"api_id": "api-doc", "target_queue": "queue:ai_analyze_doc"}),
            _dump({"api_id": "api-asserts", "target_queue": "queue:ai_analyze_asserts"}),
        ]
    })

    main_items = await _list_dlq_items(redis, "ai_analyze")
    doc_items = await _list_dlq_items(redis, "ai_analyze_doc")
    assert_items = await _list_dlq_items(redis, "ai_analyze_asserts")

    assert [i["api_id"] for i in main_items] == ["api-main"]
    assert [i["api_id"] for i in doc_items] == ["api-doc"]
    assert [i["api_id"] for i in assert_items] == ["api-asserts"]
    assert doc_items[0]["raw_index"] == 1


@pytest.mark.asyncio
async def test_retry_shared_dlq_item_uses_original_target_queue():
    redis = FakeRedis({
        "queue:ai_analyze:dlq": [
            _dump({"api_id": "api-doc", "target_queue": "queue:ai_analyze_doc", "fail_count": 3}),
        ]
    })

    ok = await _retry_dlq_item(redis, "ai_analyze_doc", 0)

    assert ok is True
    assert redis.lists["queue:ai_analyze:dlq"] == []
    retried = json.loads(redis.lists["queue:ai_analyze_doc"][0])
    assert retried["api_id"] == "api-doc"
    assert retried["fail_count"] == 0
    assert "target_queue" not in retried


@pytest.mark.asyncio
async def test_remove_generic_dlq_item_uses_visible_index():
    redis = FakeRedis({
        "queue:ai_analyze:dlq": [
            _dump({"api_id": "api-main"}),
            _dump({"api_id": "api-doc", "target_queue": "queue:ai_analyze_doc"}),
        ]
    })

    ok = await _remove_dlq_item(redis, "ai_analyze_doc", 0)

    assert ok is True
    remaining = [json.loads(x)["api_id"] for x in redis.lists["queue:ai_analyze:dlq"]]
    assert remaining == ["api-main"]


@pytest.mark.asyncio
async def test_queue_summary_reports_per_queue_dlq_counts():
    redis = FakeRedis({
        "queue:ai_analyze": ["{}"],
        "queue:ai_analyze:dlq": [
            _dump({"api_id": "api-main"}),
            _dump({"api_id": "api-doc", "target_queue": "queue:ai_analyze_doc"}),
        ],
        "queue:data_template:dlq": [_dump({"template_id": "tmpl-1"})],
    })

    summary = await get_queue_summary(redis)

    assert summary["queues"]["queue:ai_analyze"]["pending"] == 1
    assert summary["queues"]["queue:ai_analyze"]["dlq"] == 1
    assert summary["queues"]["queue:ai_analyze_doc"]["dlq"] == 1
    assert summary["queues"]["queue:data_template"]["dlq"] == 1
    assert any(d["id"] == "alert_analyze" for d in summary["definitions"])
