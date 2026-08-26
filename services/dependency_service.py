"""
ApiPulse 数据驱动依赖发现服务（Q1）。

目标：不再让 LLM 凭空臆造接口依赖，而是从请求/响应数据里挖掘「下游 API 参数 <-
上游 API 响应字段」的候选依赖边，物化为 api_dependency_edges，供：
  1. 场景生成时作为硬约束注入 prompt（A5）；
  2. 规模化分片（连通分量聚类）提供输入（A4 / C1）；
  3. 审核中心展示依赖证据（D3）。

设计约束：
  - 只增不改不删现有链路；新集合 api_dependency_edges；
  - 依赖边默认 status=candidate，仅作候选（宁缺勿错），人工/动态证据可升级为 confirmed；
  - embedding 语义相似可选接入，缺省退化为字段名归一化 + 类型兼容（不新增外部依赖）。
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable

from config.settings import get_settings

# 依赖发现按项目 TTL 缓存，避免每次场景生成都全量重算（性能）
_GRAPH_TTL = 30.0

# 字段名归一化：lower + 去非字母数字。userId / user_id / userid -> userid
_re_non_alnum = re.compile(r"[^a-z0-9]")


def _norm(name: Any) -> str:
    return _re_non_alnum.sub("", str(name or "").lower())


def _resolve_path(obj: Any, path: str) -> bool:
    """判断 PATH（如 $.data.user.id / $.data[0].id）在样本对象中是否存在。"""
    if not isinstance(path, str) or not path.startswith("$"):
        return False
    segs = re.findall(r"[^.\[\]]+|\[\d+\]", path[1:])
    cur = obj
    for seg in segs:
        m = re.match(r"\[(\d+)\]", seg)
        if m:
            if not (isinstance(cur, list) and int(m.group(1)) < len(cur)):
                return False
            cur = cur[int(m.group(1))]
        else:
            if not isinstance(cur, dict) or seg not in cur:
                return False
            cur = cur[seg]
    return True


# JSONPath 提示：由响应字段名生成一个可读路径（不含真实样本时用 $.<field>）
def _path_for_field(field_name: str) -> str:
    return "$." + str(field_name or "").strip()


class DependencyService:
    """依赖图：静态挖掘 + 建图 + 连通分量聚类 + 依赖上下文生成。"""

    EDGES = "api_dependency_edges"

    def __init__(self, db, embed_similarity: Callable[[str, str], float] | None = None):
        """
        db: AsyncIOMotorDatabase
        embed_similarity: 可选 (a, b) -> 0..1 语义相似度函数（缺省 None 时用名称归一化匹配）
        """
        self.db = db
        self._embed = embed_similarity
        self._s = get_settings()
        self._last_discover: dict[str, float] = {}  # project_id -> 最近发现的秒级时间戳

    # ── 静态挖掘 ───────────────────────────────────────────
    @staticmethod
    def _build_indexes(docs: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
        """从 api_dsls 列表构建「响应字段索引」与「请求参数索引」。"""
        up_index: dict[str, list[dict[str, Any]]] = {}
        down_index: dict[str, list[dict[str, Any]]] = {}
        for d in docs:
            api_id = d.get("id", "")
            if not api_id:
                continue
            resp_list: list[dict[str, Any]] = []
            for f in (d.get("doc") or {}).get("response_fields") or []:
                if isinstance(f, dict):
                    fn = f.get("name") or ""
                    if fn:
                        resp_list.append({"name": fn, "norm": _norm(fn), "type": (f.get("type") or "").lower()})
            up_index[api_id] = resp_list
            param_list: list[dict[str, Any]] = []
            for p in (d.get("doc") or {}).get("params") or []:
                if isinstance(p, dict):
                    pn = p.get("name") or ""
                    if pn:
                        param_list.append({
                            "name": pn, "norm": _norm(pn),
                            "location": p.get("location") or "query",
                            "type": (p.get("type") or "").lower(),
                        })
            down_index[api_id] = param_list
        return up_index, down_index

    async def discover_static_edges(self, project_id: str, api_ids: list[str] | None = None,
                                    focus_api_id: str | None = None, force: bool = False) -> int:
        """对项目（或指定 API 集合）做静态依赖挖掘，返回新增/更新边数。

        focus_api_id：非空时仅产生「涉及该 API 的边」（作为下游消费方 + 作为上游提供方），
        用于单 API 增量更新；此时忽略 api_ids 过滤（需全项目文档做跨 API 匹配）。
        force：True 时忽略 TTL 缓存，强制重算（重建端点使用）。
        """
        # 性能：全项目发现的 TTL 缓存；focus 模式(单 API 增量)不缓存
        if not focus_api_id and not force:
            last = self._last_discover.get(project_id, 0.0)
            if time.time() - last < _GRAPH_TTL:
                return 0  # 近期已发现，跳过
        q: dict[str, Any] = {"project_id": project_id}
        if focus_api_id is None and api_ids is not None and api_ids:
            q["id"] = {"$in": api_ids}
        # 分页加载全部 API（按 dependency_batch_size 分批，避免一次性加载 10000 上限+内存峰值，适配大规模项目）
        batch_sz = max(100, self._s.dependency_batch_size or 500)
        docs: list[dict[str, Any]] = []
        skip = 0
        while True:
            chunk = await self.db["api_dsls"].find(q, {"_id": 0}).skip(skip).limit(batch_sz).to_list(batch_sz)
            if not chunk:
                break
            docs.extend(chunk)
            skip += batch_sz
            if len(docs) >= 200000:
                break  # 安全上限，防止极端项目拖垮内存
        up_index, down_index = self._build_indexes(docs)

        upserted = 0
        seen: set[tuple[str, str]] = set()
        for down_id, params in down_index.items():
            if not params:
                continue
            for p in params:
                best: tuple[float, str, str] | None = None
                for up_id, resp_list in up_index.items():
                    if up_id == down_id or not resp_list:
                        continue
                    if focus_api_id is not None and focus_api_id not in (up_id, down_id):
                        continue  # focus 模式：仅保留涉及 focus_api_id 的边
                    for f in resp_list:
                        score = self._score_match(p["norm"], p["type"], f["norm"], f["type"])
                        if best is None or score > best[0]:
                            best = (score, up_id, f["name"])
                if best is None or best[0] < 0.4:
                    continue
                score, up_id, resp_field = best
                key = (up_id, down_id)
                if key not in seen:
                    seen.add(key)
                    up = await self.upsert_edge(
                        project_id=project_id, up=up_id, down=down_id,
                        field_map={p["name"]: _path_for_field(resp_field)},
                        evidence_source="static", confidence=score, observed=0,
                    )
                    upserted += up
        if not focus_api_id:
            self._last_discover[project_id] = time.time()
        return upserted

    def _score_match(self, param_norm: str, param_type: str, field_norm: str, field_type: str) -> float:
        """字段名归一化匹配 + 类型兼容 → 0..1 得分。"""
        if not param_norm or not field_norm:
            return 0.0
        if param_norm == field_norm:
            base = 0.85
        elif param_norm in field_norm or field_norm in param_norm:
            base = 0.55
        else:
            return 0.0
        if self._type_compatible(param_type, field_type):
            base += 0.05
        return min(base, 0.95)

    @staticmethod
    def _type_compatible(a: str, b: str) -> bool:
        if not a or not b:
            return True
        nums = {"int", "number", "integer", "float", "long", "double"}
        return (a in nums and b in nums) or (a == b) or (a in {"", "any", "string"} or b in {"", "any", "string"})

    # ── 边落库（按 (up,down) 去重合并） ─────────────────────
    async def upsert_edge(self, project_id: str, up: str, down: str, field_map: dict[str, str],
                          evidence_source: str = "static", confidence: float = 0.0,
                          observed: int = 0, evidence: str = "") -> int:
        """按 (project, up, down) 合并：合并 field_map、取置信度最大值、累加观测次数。"""
        coll = self.db[self.EDGES]
        existing = await coll.find_one({"project_id": project_id, "upstream_api_id": up, "downstream_api_id": down})
        if existing:
            cur_map = dict(existing.get("field_map") or {})
            cur_map.update(field_map)
            new_conf = max(existing.get("confidence", 0.0), confidence)
            new_obs = (existing.get("observed_count") or 0) + observed
            if new_obs >= 2 and existing.get("status") != "rejected":
                status = "confirmed"
                ev = "dynamic_run"
            elif evidence:
                # 显式传入证据(如 dynamic_run/human_confirm) → 升级并确认
                status = "confirmed"
                ev = evidence
            else:
                status = existing.get("status", "candidate")
                ev = existing.get("evidence", "static_name")
            await coll.update_one(
                {"_id": existing["_id"]},
                {"$set": {"field_map": cur_map, "confidence": new_conf, "observed_count": new_obs,
                          "status": status, "evidence": ev, "source": evidence_source, "updated_at": _now()}},
            )
            return 1
        import uuid
        await coll.insert_one({
            "id": uuid.uuid4().hex[:12],
            "project_id": project_id, "upstream_api_id": up, "downstream_api_id": down,
            "field_map": field_map, "confidence": confidence, "observed_count": observed,
            "status": "confirmed" if (observed >= 2 or evidence) else "candidate",
            "evidence": evidence or ("dynamic_run" if observed >= 2 else "static_name"),
            "source": evidence_source, "created_at": _now(), "updated_at": _now(),
        })
        return 1

    # ── 建图 + 连通分量聚类（A4 / C1 输入） ─────────────────
    async def build_graph(self, project_id: str) -> list[dict[str, Any]]:
        """取置信度 >= 阈值的边（视为图的可信边）。"""
        s = self._s
        return await self.db[self.EDGES].find({
            "project_id": project_id,
            "confidence": {"$gte": s.dependency_min_confidence},
            "status": {"$ne": "rejected"},
        }, {"_id": 0}).to_list(length=100000)

    async def connected_components(self, project_id: str) -> list[list[str]]:
        """以可信边建无向图，求连通分量（Cluster）。无边的 API 各成独立簇。"""
        edges = await self.build_graph(project_id)
        adj: dict[str, set[str]] = {}
        for e in edges:
            u, d = e.get("upstream_api_id"), e.get("downstream_api_id")
            if u:
                adj.setdefault(u, set()).add(d)
            if d:
                adj.setdefault(d, set()).add(u)
        seen: set[str] = set()
        clusters: list[list[str]] = []
        for node in list(adj):
            if node in seen:
                continue
            comp: list[str] = []
            stack = [node]
            seen.add(node)
            while stack:
                cur = stack.pop()
                comp.append(cur)
                for nb in adj.get(cur, set()):
                    if nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
            clusters.append(comp)
        for node in adj:
            if node not in seen:
                clusters.append([node])
        return clusters

    # ── 依赖上下文（注入场景生成 prompt，A5） ───────────────
    async def dependency_context(self, project_id: str, api_ids: list[str] | None = None) -> str:
        """生成供 LLM 使用的依赖约束文本；仅列涉及给定 API 的边。"""
        edges = await self.build_graph(project_id)
        id_set = set(api_ids) if api_ids else None
        lines: list[str] = [
            "## 已知接口数据依赖（设计 depends_on/extract/变量引用时必须基于这些边，禁止臆造）",
        ]
        count = 0
        for e in edges:
            up, down = e.get("upstream_api_id"), e.get("downstream_api_id")
            if id_set and (up not in id_set and down not in id_set):
                continue
            fmap = e.get("field_map") or {}
            for param, path in fmap.items():
                lines.append("- " + str(up) + ".[" + str(path) + "] -> " + str(down) + ".(" + str(param) + ")  (evidence=" + str(e.get("evidence")) + ", conf=" + format(float(e.get("confidence") or 0), ".2f") + ")")
                count += 1
        if count == 0:
            return ""
        return "\n".join(lines)

    # ── 增量更新 / 边操作 ──────────────────────────────────
    async def update_edges_for_api(self, api_id: str) -> int:
        """新导入/更新单个 API 时，仅重新挖掘涉及该 API 的边（增量，不全量）。"""
        doc = await self.db["api_dsls"].find_one({"id": api_id})
        if not doc:
            return 0
        project_id = doc.get("project_id", "default")
        # 单 API 增量：以该 API 为焦点，同时发现它作为下游消费方与作为上游提供方的边
        n = await self.discover_static_edges(project_id, focus_api_id=api_id)
        # 增量更新后重置该项目的 TTL 缓存（后续全量发现会跳过，避免重复算）
        self._last_discover[project_id] = time.time()
        return n

    async def list_edges(self, project_id: str, status: str = "") -> list[dict[str, Any]]:
        q: dict[str, Any] = {"project_id": project_id}
        if status:
            q["status"] = status
        return await self.db[self.EDGES].find(q, {"_id": 0}).to_list(length=10000)

    async def set_edge_status(self, edge_id: str, status: str) -> bool:
        r = await self.db[self.EDGES].update_one({"id": edge_id}, {"$set": {"status": status, "updated_at": _now()}})
        return r.matched_count > 0

    async def promote_evidence(self, edge_id: str, evidence: str) -> bool:
        """人工确认后提升证据来源（如 human_confirm），并保持 status=confirmed。"""
        r = await self.db[self.EDGES].update_one(
            {"id": edge_id}, {"$set": {"evidence": evidence, "status": "confirmed", "updated_at": _now()}}
        )
        return r.matched_count > 0

    async def decay_edges_for_generation(self, project_id: str, api_ids: list[str], amount: float = 0.1) -> int:
        """P0-D4 反馈闭环：某生成物被拒绝时，弱化其涉及项目 API 的依赖边置信度。

        仅作用于两端都在给定 api_ids 内的边；降到下限 0.2 且 < 阈值时退回 candidate，避免误杀已确认边。
        返回受影响边数。"""
        if not api_ids:
            return 0
        from config.settings import get_settings
        threshold = get_settings().dependency_min_confidence
        coll = self.db[self.EDGES]
        edges = await coll.find({
            "project_id": project_id, "status": {"$ne": "rejected"},
            "upstream_api_id": {"$in": api_ids}, "downstream_api_id": {"$in": api_ids},
        }).to_list(length=1000)
        n = 0
        for e in edges:
            new_conf = max(0.2, (e.get("confidence") or 0.0) - amount)
            status = e.get("status", "candidate")
            # 低于阈值或曾是 confirmed 但被人为降权 → 退回 candidate（保留证据，不再作强约束）
            if new_conf < threshold and e.get("evidence") != "human_confirm":
                status = "candidate"
            await coll.update_one(
                {"_id": e["_id"]},
                {"$set": {"confidence": new_conf, "status": status, "updated_at": _now()}},
            )
            n += 1
        return n

    async def probe_dependencies(self, project_id: str, api_ids: list[str] | None = None) -> int:
        """P2 动态依赖探测：用真实响应样本校验候选边字段路径是否真实存在（近似动态证据）。

        对每条 candidate 边，取上游 API 的 response.body 样本，解析 field_map 中每个字段路径；
        全部存在则 confidence 提升并 evidence=dynamic_run。返回提升边数。"""
        from config.settings import get_settings
        s = get_settings()
        if not s.dependency_dynamic_probe:
            return 0
        edges = await self.list_edges(project_id, status="candidate")
        confirmed = 0
        for e in edges:
            up, down = e.get("upstream_api_id"), e.get("downstream_api_id")
            if api_ids and up not in api_ids and down not in api_ids:
                continue
            up_doc = await self.db["api_dsls"].find_one({"id": up}, {"response.body": 1})
            resp_doc = (up_doc or {}).get("response") or {}
            sample = resp_doc.get("body")
            if sample is None:
                continue
            fmap = e.get("field_map") or {}
            if not fmap:
                continue
            if all(_resolve_path(sample, p) for p in fmap.values()):
                await self.upsert_edge(
                    project_id, up, down, {}, evidence_source="dynamic",
                    confidence=max(e.get("confidence") or 0.0, 0.7), observed=1,
                    evidence="dynamic_run",
                )
                confirmed += 1
        return confirmed


def _now():
    from datetime import datetime, timezone, timedelta
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
