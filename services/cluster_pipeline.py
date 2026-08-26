"""
规模化分片管线（Q3）。

把大量 api_ids 按依赖图连通分量聚类后，拆成若干 <= batch_max_apis 的批次，
每批作为独立生成任务（配合依赖上下文），避免：
  1. 生成时一次塞入全部 API 导致 prompt 溢出；
  2. 现有 find(...).to_list(length=100) 静默截断。
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from config.settings import get_settings
from services.dependency_service import DependencyService


def plan_batches(api_ids: Sequence[str], clusters: Sequence[Sequence[str]] | None = None,
                 batch_size: int | None = None) -> list[list[str]]:
    """把 api_ids 切成若干批次，每批 <= batch_size，并集 = 输入全集、无重复。

    clusters 由 dependency_service.connected_components 提供；缺省则直接按 batch_size 切块。
    仅在给定的 api_ids 范围内分片（不会引入输入之外的 id）。
    """
    s = get_settings()
    size = batch_size or s.cluster_batch_max_apis or 30
    id_set = set(api_ids)

    if clusters:
        grouped: list[list[str]] = []
        seen: set[str] = set()
        for cl in clusters:
            cl_ids = [x for x in cl if x in id_set and x not in seen]
            if not cl_ids:
                continue
            seen.update(cl_ids)
            # 大簇按 batch_size 切分（保持簇内相关 API 尽量同批）
            for i in range(0, len(cl_ids), size):
                grouped.append(cl_ids[i:i + size])
        # 处理不在任何簇的孤立 id
        orphan = [x for x in api_ids if x not in seen]
        for i in range(0, len(orphan), size):
            grouped.append(orphan[i:i + size])
        return [g for g in grouped if g]

    # 无簇：直接切块
    return [list(api_ids[i:i + size]) for i in range(0, len(api_ids), size)]


class ClusterPipeline:
    """依赖依赖图分片；后续可接入队列/worker（C2）。"""

    def __init__(self, db):
        self.db = db
        self._dep = DependencyService(db)

    async def build_batches(self, project_id: str, api_ids: list[str]) -> list[list[str]]:
        """先按依赖图连通分量聚类，再分片。"""
        clusters = await self._dep.connected_components(project_id)
        return plan_batches(api_ids, clusters)

    async def dependency_context_for(self, project_id: str, api_ids: list[str]) -> str:
        """取涉及这批 API 的依赖约束文本，供生成注入。"""
        return await self._dep.dependency_context(project_id, api_ids)

    async def run_discovery(self, project_id: str, api_ids: list[str] | None = None) -> int:
        """执行静态依赖挖掘（生成前置步骤，增量可复用）。"""
        return await self._dep.discover_static_edges(project_id, api_ids)
