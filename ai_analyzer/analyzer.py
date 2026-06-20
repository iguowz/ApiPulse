"""
AI 分析服务 v3
- AnalysisStatus 枚举替代 ai_analyzed bool，支持全流程状态跟踪
- WebSocket 实时推送状态变更
- 场景生成异步化：queue:ai_scenario 独立队列
- 死信队列管理：list/retry/remove
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
from loguru import logger
from motor.motor_asyncio import AsyncIOMotorDatabase
from openai import AsyncOpenAI, RateLimitError, APIError
from redis.asyncio import Redis

from config.settings import get_settings
from har_parser.parser import AI_ANALYZE_QUEUE
# ReMe 记忆系统（可选注入，不存在时静默跳过）
from knowledge.service import KnowledgeService
from models.dsl import (
    AnalysisStatus, ApiDoc, ApiDSL, AssertRule, ParamDoc, RiskLevel,
    ScenarioDSL, ScenarioStep, MonitorDSL,
)
from models.generation_version import GenerationVersion, GenerationType, GenerationStatus
from services.ai_job_service import AiJobService
from services.llm_config_service import resolve_llm_config
from services.structured_output_service import StructuredOutputError, parse_structured_output
from ai_analyzer.utils import safe_fire_and_forget

AI_DLQ = "queue:ai_analyze:dlq"
# 场景生成异步队列，路由立即返回 {queued:true}，由 worker 后台处理
AI_SCENARIO_QUEUE = "queue:ai_scenario"
AI_SCENARIO_DLQ = "queue:ai_scenario:dlq"
AI_MONITOR_QUEUE = "queue:ai_monitor"
AI_MONITOR_DLQ = "queue:ai_monitor:dlq"
DATA_TEMPLATE_QUEUE = "queue:data_template"
DATA_TEMPLATE_DLQ = "queue:data_template:dlq"
MAX_RETRY = 3


_DOC_SYSTEM = """\
你是专业 API 文档工程师。你的任务是根据真实的 HTTP 请求/响应数据生成准确的 API 文档。

规则：
1. summary 用一句话描述接口功能，description 补充业务背景和注意事项。
2. params 只列实际出现在请求中的字段；location 取 query/body/header/path 之一。
   每个参数必须包含 example 字段，给出一个代表性的示例值（如字符串、数字等）。
   对每个参数必须输出 required 字段（true/false），并给出判定依据（如"请求体中始终存在"→true，"仅在特定条件下出现"→false）。
3. response_fields 只列响应体中实际存在的字段（递归展开至两层嵌套对象，如 data.user.id）。
   对嵌套对象展开到叶子字段（基本类型），数组元素用 [0] 表示第一个元素展开。
   每个响应字段必须包含 example 字段，给出该字段在响应中的实际值。
4. 字段类型使用 JSON Schema 类型：string / number / integer / boolean / array / object。
5. 对值有限的字段（如 status、code、type），在 description 中列出可能的枚举值，如"状态码：0=成功, 1=失败, 2=待审核"。
6. 安全原则：对无法确定必填性的字段一律标记为 required=false，宁可漏报不可误报。
7. 仅输出纯 JSON 对象，不要加 markdown 代码围栏（```json），不要加任何解释文字。

## 示例
输入：POST /api/login {username:"alice",password:"***"} → 200 {code:0,data:{token:"eyJ..."}}
输出：
{"summary":"用户登录接口","description":"使用用户名和密码进行身份认证，返回访问令牌。令牌需在后续请求的 Authorization 头中携带。","params":[{"name":"username","location":"body","type":"string","required":true,"description":"用户登录名","example":"alice"},{"name":"password","location":"body","type":"string","required":true,"description":"登录密码","example":"***"}],"response_fields":[{"name":"code","location":"body","type":"integer","required":true,"description":"业务状态码，0=成功","example":0},{"name":"data.token","location":"body","type":"string","required":true,"description":"JWT 访问令牌","example":"eyJhbGciOiJIUzI1NiJ9..."}],"tags":["auth","user"]}
"""

_DOC_USER = """\
根据以下接口的真实请求/响应数据，生成完整的 API 文档 JSON：

{api_json}"""

_ASSERT_SYSTEM = """\
你是接口测试专家。你的任务是为 API 接口生成自动化断言规则，确保每次调用都能自动验证正确性。

规则：
1. 必须包含 status_code 断言（operator=eq, expected=实际状态码, risk_level=critical）。
2. 响应体关键字段用 jsonpath 表示（如 $.code, $.data.id, $.data[0].name）。
3. operator 支持以下 23 种（必须严格使用这些值）：
   - 比较: eq / ne / gt / gte / lt / lte
   - 字符串: contains / not_contains / starts_with / ends_with / regex
   - 存在性: exists / not_exists / empty / not_empty
   - 集合: in / not_in
   - 类型: type_match（值取 int/float/str/bool/list/dict/null）
   - 结构: length（数组/字符串长度）, json_schema（JSON Schema 校验）
   - 响应头: header_eq / header_contains
   - 性能: response_time_lt（响应时间 ms，特殊字段 $response_time_ms）
4. risk_level 按影响程度取：low（次要字段）/ medium（辅助字段）/ high（核心字段）/ critical（status_code 专用）。
5. 每个断言必须有 description 说明其业务含义。
6. **边界与边缘情况**：
   - 对数字字段自动添加范围断言（如 $.data.id 用 gte/lte 限定合理范围）
   - 对数组字段自动添加 length 断言（如 $.data 数组长度 gte 0）
   - 对可能为空的字符串字段添加 not_empty 断言
   - 对可枚举字段使用 in 断言限定合法取值
7. 仅输出纯 JSON 数组，不要加 markdown 代码围栏，不要加任何解释文字。

## 示例
输入：POST /api/login → 200 {code:0, data:{token:"eyJ..."}}
输出：
[{"field":"status_code","operator":"eq","expected":200,"description":"HTTP状态码为200","risk_level":"critical"},{"field":"$.code","operator":"eq","expected":0,"description":"业务码为0表示成功","risk_level":"high"},{"field":"$.data.token","operator":"exists","expected":null,"description":"返回有效的Token","risk_level":"high"},{"field":"$.data.token","operator":"not_empty","expected":null,"description":"Token不为空字符串","risk_level":"medium"}]

输入：GET /api/users → 200 {data:[{id:1,name:"张三"},{id:2,name:"李四"}]}
输出：
[{"field":"status_code","operator":"eq","expected":200,"description":"HTTP状态码为200","risk_level":"critical"},{"field":"$.data","operator":"exists","expected":null,"description":"data字段存在","risk_level":"high"},{"field":"$.data","operator":"length","expected":{"gte":1},"description":"data数组至少包含1个元素","risk_level":"medium"},{"field":"$.data[0].id","operator":"exists","expected":null,"description":"用户ID存在","risk_level":"medium"},{"field":"$.data[0].id","operator":"gte","expected":1,"description":"用户ID为正整数","risk_level":"low"}]
"""

_ASSERT_USER = """\
为以下接口生成断言规则 JSON 数组：

{api_json}"""

_SCENARIO_SYSTEM = """\
你是测试场景设计师。请输出 ApiPulse 新版场景 DSL，场景是扁平 steps 数组，但每个节点必须有显式 type。

输出必须是纯 JSON 数组。每个场景对象包含：
- name: 场景名
- description: 场景说明
- test_goal: 测试目标
- coverage_tags: 覆盖标签数组，如 happy_path/error_path/boundary/auth/performance/condition/loop
- steps: 节点数组

节点类型：
1. start: 固定 step_id="start"，用于定义 start_params，depends_on=[]
2. api: 真实 HTTP 调用，必须有 api_id、name、depends_on、extract、override_params、override_headers、assertions
3. condition: 条件容器，必须有 condition，子节点用 parent_id 指向该 condition 节点
4. loop: 循环容器，必须有 loop，格式 {"mode":"count|list","count":N,"list_ref":"{{step.var}}","item_alias":"item"}，子节点用 parent_id 指向该 loop 节点
5. end: 固定 step_id="end"，depends_on 指向所有叶子节点

变量引用统一使用双花括号：
- 上游步骤提取变量：{{step_id.var_name}}
- 环境变量：{{env.KEY}}
- 循环变量：{{loop.item}}、{{loop.index}} 或 {{loop.<item_alias>}}

断言格式统一为：
{"source":"response|status|header","path":"$.code 或 status_code 或 header 名","operator":"eq|ne|gt|gte|lt|lte|contains|exists|not_empty|response_time_lt","expected":任意值}

要求：
- 每个场景必须包含 start 和 end。
- api 节点若无上游依赖则 depends_on=["start"]。
- extract 只提取后续会使用的变量，后续必须用 {{step_id.var}} 引用。
- 不要输出 expect_failure/failure_asserts，预期失败用 assertions 表达。
- 仅输出 JSON，不要 markdown，不要解释。
"""

_SCENARIO_USER = """\
根据以下接口列表，设计 2-3 个端到端场景化测试用例：

{apis_json}"""


# P1-1: 数据模板 AI 增强 prompt
_DATA_TEMPLATE_SYSTEM = """\
你是测试数据工程师。你的任务是根据接口样本数据和现有字段配置，增强数据模板的字段生成规则，使其能生成更真实、更全面的测试数据（含边界值和异常值）。

规则：
1. 为每个字段选择最合适的 faker_method（从白名单选）：name/email/phone_number/url/uuid4/iso8601/password/address/word/sentence/paragraph/random_int/random_float/boolean/md5/sha256/pystr/user_name/company_name/job/credit_card_number/iban/swift/ean13/ipv4/ipv6/mac_address/user_agent/iso8601/date_time_this_month/credit_card_expire/latitude/longitude。
2. 对数值字段，根据业务语义设置合理的 boundary_min/boundary_max（如 age→0~150，quantity→1~9999）。
3. 对需要异常测试的字段，提供针对性的 invalid_values 候选：
   - email 字段 → ["not_an_email", "@nodomain.com", "a@b", ""]
   - phone 字段 → ["abc", "123", "00000000000"]
   - id 字段 → [-1, 0, 99999999]
   - url 字段 → ["not_a_url", "ftp://x", ""]
   - 金额字段 → [-0.01, 999999999.99, "abc"]
4. 为关键字段设置合理的注入率：null_rate（0~0.2）、invalid_rate（0~0.15），非关键字段保持 0。
5. 保留原模板中用户已配置的字段名，仅增强生成规则。
6. 仅输出纯 JSON 对象，格式：{"fields":[{"name":"...","faker_method":"...","boundary_min":0,"boundary_max":100,"invalid_values":[...],"invalid_rate":0.1,"null_rate":0,"description":"..."}],"summary":"一句话概述增强内容"}，不要加 markdown 围栏。"""

_DATA_TEMPLATE_USER = """\
根据以下数据模板现状和接口样本，增强字段生成规则：

{template_json}"""

_MONITOR_SYSTEM = """\
你是 SRE 与 API 测试专家。请为 ApiPulse 项目生成巡检监控配置建议。

规则：
1. 只输出纯 JSON 对象：{"monitors":[...],"summary":"..."}。
2. 每个 monitor 必须包含 name/target_type/target_id/interval/cron/risk_level/max_consecutive_failures/diff_threshold/diff_ignore_paths/asserts/alert_channels/description。
3. target_type 只能是 api/scenario/data_factory，target_id 必须来自输入 candidates。
4. interval 可用 1m/5m/15m/30m/1h/6h/12h/1d；高风险优先 5m 或 15m，低风险 1h 或更低频。
5. risk_level 只能是 low/medium/high/critical。
6. asserts 使用字段 field/operator/expected/description/risk_level，operator 使用 eq/ne/gt/gte/lt/lte/contains/exists/not_empty/response_time_lt。
7. 不要直接创建监控，只给配置建议。"""

_MONITOR_USER = """\
根据以下项目监控上下文生成巡检配置：

{monitor_context}"""


# P1-4: 场景步骤内联 AI 辅助 prompt（推荐断言 + extract）
_STEP_RECOMMEND_SYSTEM = """\
你是 API 测试专家。根据接口的响应样本，为场景步骤推荐断言规则和变量提取规则。

规则：
1. 推荐断言（asserts）：
   - 必含 status_code 断言（operator=eq）
   - 对响应体核心字段（如 code/id/token）推荐 eq/exists/not_empty 断言
   - operator 取值：eq/ne/gt/lt/gte/lte/contains/exists/not_exists/empty/not_empty/in/not_in/regex/type_match/length
   - risk_level：critical（status_code）/ high（核心字段）/ medium（辅助字段）/ low（次要）
2. 推荐提取（extract）：仅推荐后续步骤可能需要的变量（如 token/id），格式 {变量名: jsonpath}
3. 不要推荐过多，断言 3-6 条、提取 0-3 个即可
4. 仅输出纯 JSON：{"asserts":[{"field":"...","operator":"...","expected":...,"risk_level":"...","description":"..."}],"extract":{"var":"$.path"},"summary":"一句话概述"}，不要 markdown 围栏"""

_STEP_RECOMMEND_USER = """\
根据以下接口的响应样本，推荐场景步骤的断言和提取规则：

{api_json}"""


def _safe_truncate_json(obj: Any, max_chars: int = 1500) -> Any:
    """安全截断 JSON 序列化结果到指定字符数，避免大样本撑爆 prompt。"""
    if obj is None:
        return None
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
        if len(s) <= max_chars:
            return obj
        # 截断后返回字符串提示（无法安全还原为对象，直接返回截断文本）
        return s[:max_chars] + "...(truncated)"
    except Exception:
        return str(obj)[:max_chars]


# P1-6: task_type → 代码默认 prompt 映射（DB 无记录时兜底）。
# 在模块级常量定义完成后填充，使 _get_prompt 能回退到这些默认值。
# 填充为类属性而非模块常量，避免循环引用和加载顺序问题。
def _init_default_prompts():
    AiAnalyzerService._DEFAULT_PROMPTS = {
        "doc": _DOC_SYSTEM,
        "asserts": _ASSERT_SYSTEM,
        "scenario": _SCENARIO_SYSTEM,
        "data_template": _DATA_TEMPLATE_SYSTEM,
        "monitor": _MONITOR_SYSTEM,
    }


def _build_scenario_type_instruction(scenario_type: str, api_count: int) -> str:
    """根据场景生成类型构建额外的系统指令，追加到 _SCENARIO_SYSTEM 之后"""
    if scenario_type == "single":
        # 单接口场景：为该 API 生成 3-4 个独立测试场景（正常/异常/边界/鉴权），每个场景仅 1 步
        return f"""
## 特殊要求（单接口场景）
你正在为单个 API 生成测试矩阵。请生成 4-5 个场景，每个场景结构必须是 start → api → end，覆盖：
1. 正常调用 happy_path
2. 缺少必填参数 missing_required
3. 非法类型 invalid_type
4. 边界值 boundary
5. 可选：鉴权异常 auth 或性能阈值 performance
每个 api 节点必须有 assertions；预期失败场景也用 assertions 表达失败响应。
"""
    elif scenario_type == "complex":
        # 复杂场景：生成含条件分支/循环的测试场景，至少 1 个使用 condition/loop_var/loop_count 特性
        return f"""
## 特殊要求（复杂场景）
你正在设计包含条件分支和循环子图的复杂测试场景。请生成 {min(api_count + 1, 3)}-{min(api_count + 2, 4)} 个场景。
至少 1 个场景必须包含 condition 或 loop 容器节点：
1. condition 节点格式：{{"type":"condition","step_id":"cond_1","condition":{{"variable":"step_1.role","operator":"eq","value":"admin","on_false":"skip"}}}}
2. loop 节点格式：{{"type":"loop","step_id":"loop_1","loop":{{"mode":"list","list_ref":"{{{{step_1.items}}}}","item_alias":"item"}}}}
3. 容器子节点必须设置 parent_id 指向 condition/loop 节点，且子节点 depends_on 可以依赖容器或容器内前置节点。
4. loop 子节点引用当前项用 {{{{loop.item}}}} 或 {{{{loop.<item_alias>}}}}。
"""
    elif scenario_type == "multi":
        # 多接口串联场景：将选定 API 串联为调用链，通过数据传递（extract + ${}）关联各步骤
        return f"""
## 特殊要求（多接口串联场景）
你正在将{api_count}个API串联为调用链测试场景。请生成2-3个场景，每个场景要求：
1. **多步骤串联**：每个场景至少包含{min(api_count, 3)}个步骤，步骤之间通过 depends_on 建立依赖关系
2. **数据传递**：前一步骤通过 extract 提取响应数据（如 $.data.id, $.data.token），后续步骤通过 {{{{step_id.var_name}}}} 引用提取值
3. **执行顺序**：单链场景使用串行依赖（step2.depends_on=["step1"]），分支场景允许多步骤依赖同一步骤（并行执行）
4. **断言验证**：每个步骤设置 assertions 验证关键响应字段，关键步骤验证业务状态码
   - 示例 assertion: {{"source":"response","path": "$.code", "operator": "eq", "expected": 0}} 或 {{"source":"response","path": "$.data.id", "operator": "exists"}}
5. **覆盖要点**：正常调用链（主流程）、参数传递边界（如空/null值传递）、依赖失败处理（前一步骤预期失败时后续步骤的行为）
每个场景需确保步骤间 api_id 不重复（同一 API 可在不同场景使用，但不在同一场景内重复调用自身）。
"""
    # 空字符串或未知类型：不追加额外指令
    return ""


class AiAnalyzerService:
    def __init__(self, db: AsyncIOMotorDatabase, redis: Redis, ws_manager=None,
                 knowledge: KnowledgeService | None = None,
                 memory = None):  # MemoryService | None，4-tier 记忆服务
        s = get_settings()
        self._db = db
        self._redis = redis
        self._ws = ws_manager  # WebSocket 管理器，用于实时推送状态变更
        self._knowledge = knowledge  # ReMe 记忆系统服务，None 时静默跳过记忆检索/提取
        self._memory = memory  # P4: 4-tier 记忆服务(L2/L3)，None 时静默跳过
        self._client = AsyncOpenAI(
            api_key=s.openai_api_key,
            base_url=s.openai_base_url,
            # 超时从 settings 读取（默认 120s），本地模型(Ollama等)处理复杂 prompt 可能较慢
            timeout=httpx.Timeout(s.openai_timeout, connect=10.0),
        )
        self._model = s.openai_model
        self._base_url = s.openai_base_url
        self._temperature = s.openai_temperature
        self._max_tokens = s.openai_max_tokens
        self._runtime_llm_config: dict[str, Any] = {}
        self._api_col = db["api_dsls"]
        self._scenario_col = db["scenarios"]
        self._generation_col = db["generation_versions"]  # Phase 1: AI 生成内容版本化存储

    # ── 分析准备（共享逻辑） ─────────────────────────────

    async def _prepare_analysis(self, api_id: str, force: bool = False) -> tuple[ApiDSL | None, dict[str, Any] | None, str, str]:
        """
        加载 API 文档 → 校验状态 → 广播 RUNNING → 构建 api_json → 检索记忆。
        返回 (api, doc, api_json_str, memory_ctx)；失败时返回 (None, None, "", "")。
        """
        doc = await self._api_col.find_one({"id": api_id})
        if not doc:
            # API 文档不存在（可能已删除）→ 返回 None 终止分析
            logger.warning("API not found: {}", api_id)
            return None, None, "", ""

        try:
            api = ApiDSL(**doc)
        except Exception as e:
            # 反序列化失败（文档格式损坏）→ 终止分析，避免传播损坏数据
            logger.error("Failed to deserialize ApiDSL {}: {}", api_id, e)
            return None, None, "", ""

        # 已分析完成的接口默认跳过，force=True 时强制重分析（覆盖已有结果）
        if api.analysis_status in (AnalysisStatus.APPLIED, AnalysisStatus.DONE) and not force:
            logger.info("API {} already analyzed, skip", api_id)
            return None, doc, "", ""  # doc 非 None 表示 skip（调用方据此区分"跳过"与"失败"）

        await self._set_status(api_id, AnalysisStatus.RUNNING)

        # 响应体大小检查：超过 100KB 截断，避免超出 LLM 上下文窗口
        response_body = api.response.body
        MAX_RESP_BYTES = 100 * 1024  # 100KB
        response_body_str = json.dumps(response_body, ensure_ascii=False, default=str) if response_body else ""
        if len(response_body_str.encode("utf-8")) > MAX_RESP_BYTES:
            # 响应体过大 → 截断到 ~100KB，避免 LLM token 超限
            logger.warning("API {} response body {} bytes exceeds {} byte limit, truncating",
                           api_id, len(response_body_str.encode("utf-8")), MAX_RESP_BYTES)
            # 保留前 80KB + 截断提示，确保关键字段（如 code, data, list 首元素）不丢失
            truncated = response_body_str.encode("utf-8")[:MAX_RESP_BYTES - 200].decode("utf-8", errors="replace")
            response_body_str = truncated + f"\n...(truncated, original {len(response_body_str.encode('utf-8'))} bytes)"
            # 尝试从截断后的字符串恢复为 JSON 对象，供 LLM 分析结构；失败则用占位对象
            try:
                response_body = json.loads(truncated)
            except Exception:
                # 截断位置在 JSON 中间导致无法解析 → 用占位对象保留原始文本
                response_body = {"_truncated": True, "_text": truncated}

        api_summary = {
            "id": api.id,
            "method": api.request.method,
            "url": api.request.url,
            "path": api.request.path,
            "query_params": api.request.query_params,
            "body": api.request.body,
            "status_code": api.response.status_code,
            "response_body": response_body,
        }
        api_json_str = json.dumps(api_summary, ensure_ascii=False, default=str)

        # ── ReMe 记忆检索 ──
        memory_ctx = ""
        knowledge = getattr(self, "_knowledge", None)
        if knowledge is not None:
            # 知识服务可用 → 检索相关记忆条目，供 LLM 参考已有分析模式
            try:
                response_body_keys = []
                if isinstance(api.response.body, dict):
                    # 响应体为 dict 时提取顶层 key 名作为检索特征
                    response_body_keys = list(api.response.body.keys())
                context = {
                    "path": api.request.path,
                    "method": api.request.method,
                    "query_params_keys": [p.get("name", "") for p in (api.request.query_params or [])],
                    "response_body_keys": response_body_keys,
                    "summary": (api.doc.summary if api.doc else ""),
                }
                entries = await knowledge.retrieve(api.project_id or "default", context, limit=6)
                memory_ctx = knowledge.format_context(entries)
            except Exception as e:
                # 记忆检索失败不阻塞分析流程（非关键路径）
                logger.warning("Memory retrieval failed for {}: {}", api_id, e)
        # else: knowledge 为 None → 静默跳过，不检索记忆

        # P4: L2/L3 记忆检索（4-tier 记忆系统），补充项目级别和会话级别的上下文
        memory = getattr(self, "_memory", None)
        if memory is not None:
            try:
                project_id = api.project_id or "default"
                mem_results = await memory.retrieve(project_id, api.request.path, limit=6)
                l2_entries = mem_results.get("l2", []) if isinstance(mem_results, dict) else []
                l3_entries = mem_results.get("l3", []) if isinstance(mem_results, dict) else []
                if l2_entries or l3_entries:
                    # 将 L2/L3 记忆条目格式化为上下文文本，追加到 memory_ctx
                    extra_parts: list[str] = []
                    for e in l2_entries[:3]:
                        extra_parts.append(
                            f"[项目记忆] {e.get('title', '')}: {e.get('content', '')[:300]}"
                        )
                    for e in l3_entries[:3]:
                        extra_parts.append(
                            f"[会话记忆] {e.get('summary', '')[:300]}"
                        )
                    if extra_parts:
                        l23_ctx = "## 项目/会话记忆\n" + "\n".join(extra_parts)
                        memory_ctx = (memory_ctx + "\n\n" + l23_ctx) if memory_ctx else l23_ctx
            except Exception as e:
                # L2/L3 记忆检索失败不阻塞分析流程（非关键路径）
                logger.warning("L2/L3 memory retrieval failed for {}: {}", api_id, e)
        # else: memory 为 None → 静默跳过

        return api, doc, api_json_str, memory_ctx

    # ── GenerationVersion 持久化辅助（Phase 1） ─────────

    async def _save_generation_version(
        self, api_id: str, gen_type: GenerationType, content: dict,
        summary: str, model: str, latency_ms: int,
        input_tokens: int, output_tokens: int, prompt: str,
        api_ids: list[str] | None = None, project_id: str = "default",
        source: str = "analyzer", job_id: str = "",
    ) -> str:
        """将 AI 生成内容保存为 GenerationVersion (status=pending_review)，并 WebSocket 广播通知"""
        gv = GenerationVersion(
            id="",
            api_id=api_id,
            type=gen_type,
            status=GenerationStatus.PENDING_REVIEW,
            content=content,
            summary=summary,
            model=model,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            prompt=prompt,
            api_ids=api_ids or [api_id],
            project_id=project_id,
            source=source,
            job_id=job_id,
        )
        doc = gv.model_dump()
        doc.pop("id", None)  # 让 MongoDB 自动生成 _id，避免空字符串冲突
        result = await self._generation_col.insert_one(doc)
        gv_id = str(result.inserted_id)
        logger.info("GenerationVersion saved: id={} type={} api_id={}", gv_id, gen_type.value, api_id)
        # WebSocket 通知前端"新生成内容待审核"
        await self._broadcast("ai_analysis", {
            "type": "generation_pending_review",
            "generation_id": gv_id,
            "api_id": api_id,
            "gen_type": gen_type.value,
            "summary": summary,
            "project_id": project_id,
            "job_id": job_id,
        })
        if job_id:
            target_ids = api_ids or ([api_id] if api_id else [])
            await AiJobService(self._db).mark_pending_review(
                job_id=job_id,
                type=gen_type.value,
                project_id=project_id,
                source=source,
                target_ids=target_ids,
                generation_ids=[gv_id],
            )
        return gv_id

    async def apply_version(
        self, generation_id: str, reviewer_id: str | None = None,
        review_feedback: str | None = None,
        partial_fields: list[str] | None = None,
    ) -> bool:
        """
        审核通过后，将 GenerationVersion 内容写入目标 DSL。
        - partial_fields: 非空时表示部分接受（仅应用指定字段），状态设为 partially_accepted
        - 返回 True 表示应用成功，False 表示版本不存在
        """
        from bson import ObjectId

        # 从 MongoDB 加载版本记录（ObjectId 格式兼容纯字符串）
        gv_doc = await self._generation_col.find_one({"_id": ObjectId(generation_id)})
        if not gv_doc:
            # 尝试字符串 id 回退（兼容部分旧格式）
            gv_doc = await self._generation_col.find_one({"id": generation_id})
        if not gv_doc:
            logger.warning("apply_version: generation {} not found", generation_id)
            return False

        # _id 字段兼容转换：MongoDB 的 _id 可能是 ObjectId 或 str
        if "_id" in gv_doc and "id" not in gv_doc:
            gv_doc["id"] = str(gv_doc.pop("_id"))
        gv = GenerationVersion(**gv_doc)
        now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        target_status = GenerationStatus.PARTIALLY_ACCEPTED if partial_fields else GenerationStatus.ACCEPTED

        # 根据类型将内容写入目标 DSL
        if gv.type == GenerationType.DOC:
            # 文档类型：写入 api_dsls 的 doc 字段
            apply_content = gv.content
            if partial_fields:
                # 部分接受支持顶层字段和列表内字段两种选择器：
                # summary / description / tags 直接覆盖；params:name、response_fields:name 仅合并指定字段。
                current_doc = {}
                existing = await self._api_col.find_one({"id": gv.api_id}, {"_id": 0, "doc": 1, "tags": 1})
                if existing:
                    current_doc = existing.get("doc") or {}
                selected = set(partial_fields)
                apply_content = dict(current_doc)
                for key in ("summary", "description", "tags"):
                    if key in selected and key in gv.content:
                        apply_content[key] = gv.content[key]
                for list_key in ("params", "response_fields"):
                    selected_names = {
                        item.split(":", 1)[1]
                        for item in selected
                        if item.startswith(f"{list_key}:") and ":" in item
                    }
                    if list_key in selected:
                        # 兼容旧前端：勾选 params/response_fields 表示整组采纳。
                        selected_names = {f.get("name") for f in gv.content.get(list_key, []) if isinstance(f, dict) and f.get("name")}
                    if not selected_names:
                        continue
                    merged = {
                        f.get("name"): f
                        for f in (current_doc.get(list_key) or [])
                        if isinstance(f, dict) and f.get("name")
                    }
                    for field in gv.content.get(list_key, []) or []:
                        if isinstance(field, dict) and field.get("name") in selected_names:
                            merged[field["name"]] = {**merged.get(field["name"], {}), **field}
                    apply_content[list_key] = list(merged.values())
            await self._api_col.update_one(
                {"id": gv.api_id},
                {"$set": {"doc": apply_content, "tags": apply_content.get("tags", [])}},
            )
            logger.info("apply_version: doc applied to api_id={} partial={}", gv.api_id, partial_fields is not None)

        elif gv.type == GenerationType.ASSERTS:
            # 断言类型：写入 api_dsls 的 asserts 字段
            # content 中 asserts 可能是顶层的数组或嵌套在 "asserts" key 下
            rules = gv.content.get("asserts", gv.content)
            if isinstance(rules, dict):
                # LLM 可能将断言规则放在非标准位置 → 尝试提取
                rules = rules.get("asserts", list(rules.values()))
            if partial_fields:
                # 部分接受断言：仅保留用户勾选的字段
                rules = [r for r in rules if r.get("field") in partial_fields]
            await self._api_col.update_one(
                {"id": gv.api_id},
                {"$set": {"asserts": rules}},
            )
            logger.info("apply_version: {} asserts applied to api_id={} partial={}",
                         len(rules) if isinstance(rules, list) else 0, gv.api_id, partial_fields is not None)

        elif gv.type == GenerationType.SCENARIO:
            # 场景类型：从 content 提取场景列表，逐个创建 ScenarioDSL
            scenarios_data = gv.content.get("scenarios", gv.content)
            if isinstance(scenarios_data, dict):
                # 单个场景对象 → 包装为列表统一处理
                scenarios_data = [scenarios_data]
            if not isinstance(scenarios_data, list):
                logger.warning("apply_version: scenario content is not a list, type={}", type(scenarios_data).__name__)
                return False

            for s in scenarios_data:
                if not isinstance(s, dict):
                    continue
                raw_steps = s.get("steps", [])
                steps = []
                for step in raw_steps:
                    if not step.get("name"):
                        step["name"] = ""
                    steps.append(ScenarioStep(**step))
                scenario = ScenarioDSL(
                    id=str(uuid.uuid4()),
                    name=s.get("name", "未命名场景"),
                    description=s.get("description", ""),
                    steps=steps,
                    source_api_ids=gv.api_ids,
                    ai_generated=True,
                    project_id=gv.project_id,
                    tags=s.get("coverage_tags", []),
                    scenario_type=s.get("scenario_type", ""),
                    created_at=now,
                    updated_at=now,
                )
                await self._scenario_col.insert_one(scenario.model_dump())
                logger.info("apply_version: scenario '{}' created from generation {}", scenario.name, generation_id)

        elif gv.type == GenerationType.DATA_TEMPLATE:
            # 数据模板审核采用字段级 merge，解决“部分接受会丢字段”的风险。
            # AI 只覆盖同名字段，未覆盖或未选择的原字段必须保留。
            content = gv.content or {}
            template_id = content.get("template_id", "")
            fields_data = [f for f in content.get("fields", []) if isinstance(f, dict) and f.get("name")]
            if not template_id or not fields_data:
                logger.warning("apply_version: data_template missing template_id or fields")
                return False
            if partial_fields:
                fields_data = [f for f in fields_data if f.get("name") in partial_fields]
                if not fields_data:
                    logger.warning("apply_version: data_template partial accept selected no valid fields")
                    return False

            tmpl_doc = await self._db["data_templates"].find_one({"id": template_id})
            if tmpl_doc:
                current_fields = [f for f in tmpl_doc.get("fields", []) if isinstance(f, dict) and f.get("name")]
                merged_by_name = {f.get("name"): f for f in current_fields}
                for field in fields_data:
                    name = field.get("name")
                    # 同名字段保留原有未知扩展键，再用 AI 建议覆盖明确字段。
                    merged_by_name[name] = {**merged_by_name.get(name, {}), **field}
                merged_fields = list(merged_by_name.values())
                await self._db["data_templates"].update_one(
                    {"id": template_id},
                    {"$set": {
                        "fields": merged_fields,
                        "source": "ai_enhanced",
                        "job_id": gv.job_id,
                        "updated_at": now,
                    }},
                )
                logger.info("apply_version: data_template {} merged with {} AI fields",
                            template_id, len(fields_data))
            else:
                # 模板已被删除时仍可从审核版本恢复，project_id/job_id/source 一并落库。
                from models.dsl import DataTemplate, FieldTemplate
                try:
                    fields = [FieldTemplate(**f) for f in fields_data]
                    new_tmpl = DataTemplate(
                        id=template_id, name=content.get("name", "AI 增强模板"),
                        api_id=gv.api_id, fields=fields, project_id=gv.project_id,
                        source="ai_enhanced", job_id=gv.job_id,
                        created_at=now, updated_at=now,
                    )
                    await self._db["data_templates"].insert_one(new_tmpl.model_dump())
                    logger.info("apply_version: data_template {} recreated (was deleted)", template_id)
                except Exception as e:
                    logger.error("apply_version: failed to recreate data_template {}: {}", template_id, e)
                    return False

        elif gv.type == GenerationType.MONITOR:
            content = gv.content or {}
            monitors_data = content.get("monitors", content)
            if isinstance(monitors_data, dict):
                monitors_data = [monitors_data]
            if not isinstance(monitors_data, list):
                logger.warning("apply_version: monitor content is not a list")
                return False
            selected_ids = set(partial_fields or [])
            created_ids: list[str] = []
            for idx, raw in enumerate(monitors_data):
                if not isinstance(raw, dict):
                    continue
                if selected_ids:
                    selector = raw.get("id") or raw.get("target_id") or raw.get("name") or str(idx)
                    if selector not in selected_ids:
                        continue
                target_type = raw.get("target_type") or "api"
                target_id = raw.get("target_id") or raw.get("api_id") or ""
                if target_type not in {"api", "scenario", "data_factory"} or not target_id:
                    continue
                api_id = target_id if target_type == "api" else ""
                monitor = MonitorDSL(
                    id=str(uuid.uuid4()),
                    name=raw.get("name") or f"AI 巡检 {target_id[:8]}",
                    api_id=api_id,
                    target_type=target_type,
                    target_id=target_id,
                    interval=raw.get("interval") or "5m",
                    cron=raw.get("cron") or "",
                    asserts=[AssertRule(**r) for r in raw.get("asserts", []) if isinstance(r, dict)],
                    alert_channels=raw.get("alert_channels") or [],
                    enabled=bool(raw.get("enabled", True)),
                    risk_level=raw.get("risk_level") or RiskLevel.MEDIUM,
                    max_consecutive_failures=int(raw.get("max_consecutive_failures") or 3),
                    diff_threshold=int(raw.get("diff_threshold") or 3),
                    diff_ignore_paths=raw.get("diff_ignore_paths") or [],
                    project_id=gv.project_id,
                    owner=reviewer_id or raw.get("owner", ""),
                    environment_id=raw.get("environment_id") or "",
                    source="ai_generated",
                    job_id=gv.job_id,
                    updated_by=reviewer_id or "",
                    created_at=now,
                    updated_at=now,
                )
                await self._db["monitors"].insert_one(monitor.model_dump())
                monitor_service = getattr(__import__("api.state", fromlist=["_monitor_service"]), "_monitor_service", None)
                if monitor_service and monitor.enabled:
                    monitor_service._add_job(monitor)
                created_ids.append(monitor.id)
            if not created_ids:
                logger.warning("apply_version: monitor generation had no valid selected configs")
                return False
            await self._broadcast("ai_analysis", {
                "type": "monitor_applied",
                "project_id": gv.project_id,
                "job_id": gv.job_id,
                "generation_id": generation_id,
                "monitor_ids": created_ids,
                "status": "applied",
            })

        elif gv.type == GenerationType.CHAT_SUGGESTION:
            # chat_suggestion 是通用聊天建议/知识，无对应DSL实体，
            # 仅更新 GenerationVersion 审核状态，不写入任何 DSL 集合
            pass

        # 更新 GenerationVersion 审核状态
        await self._generation_col.update_one(
            {"_id": gv_doc.get("_id", ObjectId(generation_id))},
            {"$set": {
                "status": target_status.value,
                "reviewed_at": now,
                "reviewer_id": reviewer_id,
                "review_feedback": review_feedback,
            }},
        )
        if gv.api_id:
            await self._api_col.update_one(
                {"id": gv.api_id},
                {"$set": {
                    "analysis_status": AnalysisStatus.APPLIED,
                    "analysis_error": "",
                    "updated_at": now,
                }},
            )
            await self._broadcast("ai_analysis", {
                "type": "status",
                "api_id": gv.api_id,
                "status": AnalysisStatus.APPLIED.value,
                "error": "",
                "project_id": gv.project_id,
            })
        logger.info("apply_version: generation {} status → {}", generation_id, target_status.value)

        # ── 审核采纳后自动触发下游 AI 流程 ──
        # 仅在完整采纳（非部分采纳）时触发，根据产出类型决定下游队列
        if target_status == GenerationStatus.ACCEPTED:
            await self._trigger_downstream_ai(gv, project_id=gv.project_id)

        return True

    async def _trigger_downstream_ai(self, gv: GenerationVersion, project_id: str) -> None:
        """
        审核采纳后，根据 auto_trigger_ai 设置决定是否自动入队下游 AI 任务。
        触发链：scenario → data_template + monitor；data_template → monitor
        doc/asserts 已由 analyze_api 自动触发 scenario，此处不再重复
        """
        # 读取 auto_trigger_ai 设置，默认开启
        auto_trigger = True
        try:
            doc = await self._db["settings"].find_one({"key": "general_settings"})
            if doc:
                auto_trigger = doc.get("auto_trigger_ai", True)
        except Exception as e:
            logger.warning("Failed to read auto_trigger_ai setting, defaulting to True: {}", e)

        if not auto_trigger:
            logger.info("auto_trigger_ai disabled, skipping downstream AI trigger for {}", gv.id)
            return

        try:
            if gv.type == GenerationType.SCENARIO:
                # 场景采纳 → 触发数据模板生成 + 巡检生成
                for api_id in gv.target_ids or []:
                    # 数据模板：去重检查，避免同一 API 重复入队
                    if await self._has_active_job(api_id, "data_template", project_id):
                        logger.info("Auto-trigger skip dedup: data_template already queued/running for api={}", api_id)
                    else:
                        dt_job_id = f"data_template:{api_id}:{uuid.uuid4().hex[:8]}"
                        dt_payload = {"api_ids": [api_id], "project_id": project_id, "job_id": dt_job_id, "status": "queued"}
                        await self._redis.rpush(DATA_TEMPLATE_QUEUE, json.dumps(dt_payload, ensure_ascii=False))
                        await AiJobService(self._db).mark_queued(
                            job_id=dt_job_id, type="data_template", project_id=project_id,
                            source="auto_trigger", target_ids=[api_id], queue_key=DATA_TEMPLATE_QUEUE, payload=dt_payload,
                        )
                        logger.info("Auto-triggered data_template for api={}", api_id)
                    # 巡检：去重检查，避免同一 API 重复入队
                    if await self._has_active_job(api_id, "monitor", project_id):
                        logger.info("Auto-trigger skip dedup: monitor already queued/running for api={}", api_id)
                    else:
                        monitor_job_id = f"monitor:{api_id}:{uuid.uuid4().hex[:8]}"
                        monitor_payload = {"api_ids": [api_id], "project_id": project_id, "job_id": monitor_job_id, "status": "queued"}
                        await self._redis.rpush(AI_MONITOR_QUEUE, json.dumps(monitor_payload, ensure_ascii=False))
                        await AiJobService(self._db).mark_queued(
                            job_id=monitor_job_id, type="monitor", project_id=project_id,
                            source="auto_trigger", target_ids=[api_id], queue_key=AI_MONITOR_QUEUE, payload=monitor_payload,
                        )
                        logger.info("Auto-triggered monitor for api={}", api_id)

            elif gv.type == GenerationType.DATA_TEMPLATE:
                # 数据模板采纳 → 触发巡检生成
                for api_id in gv.target_ids or []:
                    # 巡检：去重检查，避免同一 API 重复入队
                    if await self._has_active_job(api_id, "monitor", project_id):
                        logger.info("Auto-trigger skip dedup: monitor already queued/running for api={}", api_id)
                    else:
                        monitor_job_id = f"monitor:{api_id}:{uuid.uuid4().hex[:8]}"
                        monitor_payload = {"api_ids": [api_id], "project_id": project_id, "job_id": monitor_job_id, "status": "queued"}
                        await self._redis.rpush(AI_MONITOR_QUEUE, json.dumps(monitor_payload, ensure_ascii=False))
                        await AiJobService(self._db).mark_queued(
                            job_id=monitor_job_id, type="monitor", project_id=project_id,
                            source="auto_trigger", target_ids=[api_id], queue_key=AI_MONITOR_QUEUE, payload=monitor_payload,
                        )
                        logger.info("Auto-triggered monitor for api={}", api_id)

            # doc/asserts/monitor 类型：无需额外触发下游
            # - doc/asserts: analyze_api 已自动入队 scenario 队列
            # - monitor: 终端产物，无下游
        except Exception as e:
            # 入队失败不回滚审核状态，仅记录 warn 日志
            logger.warning("Failed to auto-trigger downstream AI for gen={} type={}: {}", gv.id, gv.type, e)

    async def _has_active_job(self, api_id: str, job_type: str, project_id: str) -> bool:
        """
        检查 ai_jobs 中是否已有针对同一 api_id + type 的 queued/running 任务。
        用于自动触发的去重：避免下游流程重复入队同一 API 的同类任务。
        """
        try:
            existing = await self._db["ai_jobs"].find_one({
                "target_ids": api_id,
                "type": job_type,
                "project_id": project_id,
                "status": {"$in": ["queued", "running"]},
            })
            return existing is not None
        except Exception as e:
            logger.warning("_has_active_job check failed for api_id={} type={}: {}", api_id, job_type, e)
            # 检查失败时返回 False（宁可重复入队，也不阻塞流程）
            return False

    @staticmethod
    def _safe_param(p: dict) -> ParamDoc:
        """
        宽容解析参数文档：小模型可能输出不完整的字段，
        丢失必填字段时用推导值填充而非崩溃，确保文档解析尽可能成功。
        """
        if not p.get("name"):
            # name 缺失 → 按优先级回退到 field/key/path，都没有则填 "unknown"
            p["name"] = p.get("field") or p.get("key") or p.get("path") or "unknown"
        if not p.get("location"):
            # location 缺失 → 用 in 字段回退，都没有则默认 "body"
            p["location"] = p.get("in") or "body"
        if not p.get("type"):
            # type 缺失 → 从 expected/example 值的 Python 类型推导
            expected = p.get("expected") or p.get("example")
            if isinstance(expected, bool):
                p["type"] = "boolean"
            elif isinstance(expected, int):
                p["type"] = "integer"
            elif isinstance(expected, float):
                p["type"] = "number"
            elif isinstance(expected, list):
                p["type"] = "array"
            elif isinstance(expected, dict):
                p["type"] = "object"
            else:
                # 字符串或 None → 默认 "string"
                p["type"] = "string"
        return ParamDoc(
            name=p.get("name", "unknown"),
            location=p.get("location", "body"),
            type=p.get("type", "string"),
            required=p.get("required", False),
            description=p.get("description", ""),
            example=p.get("example") or p.get("expected"),
        )

    @staticmethod
    def _structured_error_detail(exc: Exception) -> str:
        """结构化输出失败时补齐原始输出摘要，方便 Job/DLQ 面板排查。"""
        if isinstance(exc, StructuredOutputError):
            detail = str(exc)
            if exc.raw_output_preview:
                detail = f"{detail}; raw_output_preview={exc.raw_output_preview[:300]}"
            return detail[:800]
        return str(exc)[:800]

    # ── 仅文档分析 ──────────────────────────────────────

    async def analyze_doc(self, api_id: str, force: bool = False, job_id: str = "") -> bool:
        """仅执行 AI 文档生成（不生成断言），由 queue:ai_analyze_doc worker 调用"""
        s = get_settings()  # 修复 NameError: 方法内需独立获取 settings
        api, doc, api_json_str, memory_ctx = await self._prepare_analysis(api_id, force)
        if api is None:
            # api=None 时：doc 非 None → 已分析完成跳过（视为成功）；doc 为 None → 真实错误
            return True if doc is not None else False

        # Phase 1: 记录 LLM 调用耗时，用于审计和成本分析
        t0 = time.time()
        # 仅调用文档 LLM（doc 输出较长，max_tokens 从 settings 读取）
        try:
            doc_raw = await self._call_llm(
                _DOC_USER.format(api_json=api_json_str),
                self._build_system_prompt(_DOC_SYSTEM, memory_ctx),
                max_tokens=s.openai_max_tokens_doc,
                task_type="doc",
            )
            if not doc_raw:
                # LLM 返回空内容 → 标记失败，不进入解析流程
                logger.error("Doc LLM returned empty response for {}", api_id)
                await self._set_status(api_id, AnalysisStatus.FAILED, "Doc LLM returned empty response")
                return False
        except Exception as e:
            # LLM 调用异常（网络/API错误等）→ 标记失败，由 worker 层重试
            logger.error("Doc LLM call failed for {}: {}", api_id, e)
            await self._set_status(api_id, AnalysisStatus.FAILED, str(e)[:500])
            return False

        # Phase 1: 计算调用耗时和 token 估算
        latency_ms = int((time.time() - t0) * 1000)
        input_tokens = self._estimate_tokens(
            self._build_system_prompt(_DOC_SYSTEM, memory_ctx) +
            _DOC_USER.format(api_json=api_json_str)
        )
        output_tokens = self._estimate_tokens(doc_raw)

        # Phase 1: 解析文档后保存为 GenerationVersion（待审核），不再直接覆盖 DSL
        try:
            doc_data = parse_structured_output("doc", doc_raw)
            doc_warnings = self._validate_doc_result(doc_data)
            if doc_warnings:
                # 结构不完整但不阻塞流程（仅记录警告，供调试 LLM 输出质量）
                logger.warning("Doc validation warnings for {}: {}", api_id, doc_warnings)

            api_doc = ApiDoc(
                summary=doc_data.get("summary", ""),
                description=doc_data.get("description", ""),
                params=[self._safe_param(p) for p in doc_data.get("params", []) if isinstance(p, dict)],
                response_fields=[self._safe_param(p) for p in doc_data.get("response_fields", []) if isinstance(p, dict)],
                tags=doc_data.get("tags", []),
                generated_at=datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None),
            )
            # Phase 1: 保存为待审核版本，用户通过审核中心 accept/reject 后才生效
            summary = f"文档: {api_doc.summary}" if api_doc.summary else "文档生成"
            await self._save_generation_version(
                api_id=api_id,
                gen_type=GenerationType.DOC,
                content=api_doc.model_dump(),
                summary=summary,
                model=self._model,
                latency_ms=latency_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                prompt=_DOC_USER.format(api_json=api_json_str),
                project_id=doc.get("project_id", "default"),
                job_id=job_id,
            )
        except Exception as e:
            detail = self._structured_error_detail(e)
            logger.warning("Doc parse failed for {}: {}", api_id, detail)
            await self._set_status(api_id, AnalysisStatus.FAILED, f"AI文档生成失败: {detail[:200]}")
            return False

        # 文档解析成功 → 待审核；审核通过后才标记 applied，避免误导用户生成内容已生效。
        await self._api_col.update_one(
            {"id": api_id},
            {"$set": {
                "analysis_status": AnalysisStatus.PENDING_REVIEW,
                "updated_at": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None),
            }},
        )
        await self._set_status(api_id, AnalysisStatus.PENDING_REVIEW)
        logger.info("API {} doc analyzed: summary='{}', saved as GenerationVersion", api_id, api_doc.summary)
        # 持久化 AI 操作日志
        await self._log_ai_operation(
            op_type="analyze_doc",
            status="success",
            api_ids=[api_id],
            message="文档解析成功（待审核）",
            project_id=doc.get("project_id", "default"),
        )
        # P4: 记录 L2 项目记忆（fire-and-forget，不阻塞主流程）
        memory = getattr(self, "_memory", None)
        if memory is not None:
            project_id = doc.get("project_id", "default")
            api_path = api.request.path if api else doc.get("request", {}).get("path", api_id)
            safe_fire_and_forget(
                memory.record_l2(
                    project_id, "doc_analysis",
                    f"API 文档分析: {api_doc.summary or api_path}",
                    f"分析了 {api.request.method} {api_path}，生成文档摘要: {api_doc.summary}",
                    tags=api_doc.tags or [],
                    source="ai_analyzer",
                ),
                name="memory.record_l2:analyze_doc",
            )
        return True

    # ── 仅断言分析 ──────────────────────────────────────

    async def analyze_asserts(self, api_id: str, force: bool = False, job_id: str = "") -> bool:
        """仅执行 AI 断言生成（不生成文档），由 queue:ai_analyze_asserts worker 调用"""
        s = get_settings()  # 修复 NameError: 方法内需独立获取 settings
        api, doc, api_json_str, memory_ctx = await self._prepare_analysis(api_id, force)
        if api is None:
            # api=None 时：doc 非 None → 已分析完成跳过（视为成功）；doc 为 None → 真实错误
            return True if doc is not None else False

        # Phase 1: 记录 LLM 调用耗时，用于审计和成本分析
        t0 = time.time()
        # 仅调用断言 LLM（asserts 输出较短，max_tokens 从 settings 读取）
        try:
            assert_raw = await self._call_llm(
                _ASSERT_USER.format(api_json=api_json_str),
                self._build_system_prompt(_ASSERT_SYSTEM, memory_ctx),
                max_tokens=s.openai_max_tokens_asserts,
                task_type="asserts",
            )
            if not assert_raw:
                # LLM 返回空内容 → 标记失败
                logger.error("Assert LLM returned empty response for {}", api_id)
                await self._set_status(api_id, AnalysisStatus.FAILED, "Assert LLM returned empty response")
                return False
        except Exception as e:
            # LLM 调用异常 → 标记失败，由 worker 层重试
            logger.error("Assert LLM call failed for {}: {}", api_id, e)
            await self._set_status(api_id, AnalysisStatus.FAILED, str(e)[:500])
            return False

        # Phase 1: 计算调用耗时和 token 估算
        latency_ms = int((time.time() - t0) * 1000)
        input_tokens = self._estimate_tokens(
            self._build_system_prompt(_ASSERT_SYSTEM, memory_ctx) +
            _ASSERT_USER.format(api_json=api_json_str)
        )
        output_tokens = self._estimate_tokens(assert_raw)

        # Phase 1: 解析断言后保存为 GenerationVersion（待审核），不再直接覆盖 DSL
        rules: list[dict] = []  # 初始化，避免分支未赋值时后续日志报错
        try:
            rules = parse_structured_output("asserts", assert_raw)
            assert_data = rules
            assert_warnings = self._validate_assert_result(assert_data)
            if assert_warnings:
                # 结构/字段不完整，记录警告但不阻塞流程
                logger.warning("Assert validation warnings for {}: {}", api_id, assert_warnings)
            # Phase 1: 保存为待审核版本（force 或尚无人工配置时生成）
            if force or not api.asserts:
                summary = f"断言规则 {len(rules)} 条"
                await self._save_generation_version(
                    api_id=api_id,
                    gen_type=GenerationType.ASSERTS,
                    content={"asserts": rules},
                    summary=summary,
                    model=self._model,
                    latency_ms=latency_ms,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    prompt=_ASSERT_USER.format(api_json=api_json_str),
                    project_id=doc.get("project_id", "default"),
                    job_id=job_id,
                )
            # else: 已有手动配置的断言 → 静默跳过（保留人工配置，不覆盖）
        except Exception as e:
            detail = self._structured_error_detail(e)
            logger.warning("Assert parse failed for {}: {}", api_id, detail)
            await self._set_status(api_id, AnalysisStatus.FAILED, f"断言生成失败: {detail[:200]}")
            return False

        # 断言解析成功 → 待审核；审核通过后才写入 DSL 并标记 applied。
        await self._api_col.update_one(
            {"id": api_id},
            {"$set": {
                "analysis_status": AnalysisStatus.PENDING_REVIEW,
                "updated_at": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None),
            }},
        )
        await self._set_status(api_id, AnalysisStatus.PENDING_REVIEW)
        logger.info("API {} asserts analyzed: {} rules, saved as GenerationVersion", api_id, len(rules))
        # 持久化 AI 操作日志
        await self._log_ai_operation(
            op_type="analyze_asserts",
            status="success",
            api_ids=[api_id],
            message=f"断言解析成功（待审核，{len(rules)} 条）",
            project_id=doc.get("project_id", "default"),
        )
        # P4: 记录 L2 项目记忆（fire-and-forget，不阻塞主流程）
        memory = getattr(self, "_memory", None)
        if memory is not None:
            project_id = doc.get("project_id", "default")
            api_path = api.request.path if api else doc.get("request", {}).get("path", api_id)
            safe_fire_and_forget(
                memory.record_l2(
                    project_id, "asserts_analysis",
                    f"API 断言生成: {api.request.method} {api_path}",
                    f"为 {api.request.method} {api_path} 生成 {len(rules)} 条断言规则",
                    tags=["asserts", "quality"],
                    source="ai_analyzer",
                ),
                name="memory.record_l2:analyze_asserts",
            )
        return True

    # ── 单接口全量分析（文档 + 断言，兼容旧队列） ─────

    async def analyze_api(self, api_id: str, force: bool = False, job_id: str = "") -> bool:
        """全量分析：文档 + 断言（兼容 queue:ai_analyze 消费者）"""
        s = get_settings()  # 修复 NameError: 方法内需独立获取 settings
        api, doc, api_json_str, memory_ctx = await self._prepare_analysis(api_id, force)
        if api is None:
            # api=None 时：doc 非 None → 已分析完成跳过（视为成功）；doc 为 None → 真实错误
            return True if doc is not None else False

        # Phase 1: 记录 LLM 调用耗时
        t0 = time.time()
        generation_ids: list[str] = []  # 收集本次分析生成的版本ID，用于 auto_review_flow 关闭时自动采纳
        # 文档生成 + 断言生成串行调用（doc=1500, asserts=1000），先文档后断言避免并发限流
        # P0-4: 若开启流式，用 _call_llm_stream 逐 chunk 广播，前端打字机效果
        # P1-6: prompt 从 DB 读取（_get_prompt），支持在线编辑版本化，DB 无记录回退代码默认值
        doc_runtime = resolve_llm_config(getattr(self, "_runtime_llm_config", {}), "doc")
        assert_runtime = resolve_llm_config(getattr(self, "_runtime_llm_config", {}), "asserts")
        use_stream_doc = doc_runtime.stream
        use_stream_asserts = assert_runtime.stream
        # P1-6: 并行读取 doc/asserts 的 system prompt（DB 激活版本或代码默认值）
        doc_sys_prompt = await self._get_prompt("doc")
        assert_sys_prompt = await self._get_prompt("asserts")
        try:
            if use_stream_doc:
                # 流式：优先使用队列 job_id，前端可精确追踪本次任务；旧任务回退 api_id。
                doc_raw = await self._call_llm_stream(
                    _DOC_USER.format(api_json=api_json_str),
                    self._build_system_prompt(doc_sys_prompt, memory_ctx),
                    max_tokens=s.openai_max_tokens_doc,
                    job_id=job_id or api_id, task_type="doc",
                )
            else:
                doc_raw = await self._call_llm(
                    _DOC_USER.format(api_json=api_json_str),
                    self._build_system_prompt(doc_sys_prompt, memory_ctx),
                    max_tokens=s.openai_max_tokens_doc,
                    task_type="doc",
                )
            if use_stream_asserts:
                assert_raw = await self._call_llm_stream(
                    _ASSERT_USER.format(api_json=api_json_str),
                    self._build_system_prompt(assert_sys_prompt, memory_ctx),
                    max_tokens=s.openai_max_tokens_asserts,
                    job_id=job_id or api_id, task_type="asserts",
                )
            else:
                assert_raw = await self._call_llm(
                    _ASSERT_USER.format(api_json=api_json_str),
                    self._build_system_prompt(assert_sys_prompt, memory_ctx),
                    max_tokens=s.openai_max_tokens_asserts,
                    task_type="asserts",
                )
            if not doc_raw or not assert_raw:
                # 任一 LLM 返回空 → 整体标记失败（不保存不完整结果）
                logger.error("LLM returned empty response for {}: doc_empty={} assert_empty={}", api_id, not doc_raw, not assert_raw)
                await self._set_status(api_id, AnalysisStatus.FAILED, "LLM returned empty response")
                return False
        except Exception as e:
            # LLM 调用异常 → 标记失败，由 worker 重试
            logger.error("LLM call failed for {}: {}", api_id, e)
            await self._set_status(api_id, AnalysisStatus.FAILED, str(e)[:500])
            return False

        # Phase 1: 计算耗时和 token 估算（串行调用总耗时）
        latency_ms = int((time.time() - t0) * 1000)
        user_doc_prompt = _DOC_USER.format(api_json=api_json_str)
        user_assert_prompt = _ASSERT_USER.format(api_json=api_json_str)
        project_id = doc.get("project_id", "default")

        # 解析结果：Phase 1 改为保存 GenerationVersion 而非直接写入 DSL
        doc_parsed_ok = False
        assert_parsed_ok = False
        doc_error = ""
        assert_error = ""

        # 解析文档 → 保存为 GenerationVersion
        try:
            doc_data = parse_structured_output("doc", doc_raw)
            doc_warnings = self._validate_doc_result(doc_data)
            if doc_warnings:
                logger.warning("Doc validation warnings for {}: {}", api_id, doc_warnings)

            api_doc = ApiDoc(
                summary=doc_data.get("summary", ""),
                description=doc_data.get("description", ""),
                params=[self._safe_param(p) for p in doc_data.get("params", []) if isinstance(p, dict)],
                response_fields=[self._safe_param(p) for p in doc_data.get("response_fields", []) if isinstance(p, dict)],
                tags=doc_data.get("tags", []),
                generated_at=datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None),
            )
            doc_input_tokens = self._estimate_tokens(
                self._build_system_prompt(doc_sys_prompt, memory_ctx) + user_doc_prompt
            )
            doc_output_tokens = self._estimate_tokens(doc_raw)
            doc_summary = f"文档: {api_doc.summary}" if api_doc.summary else "文档生成"
            gv_id = await self._save_generation_version(
                api_id=api_id,
                gen_type=GenerationType.DOC,
                content=api_doc.model_dump(),
                summary=doc_summary,
                model=self._model,
                latency_ms=latency_ms,
                input_tokens=doc_input_tokens,
                output_tokens=doc_output_tokens,
                prompt=user_doc_prompt,
                project_id=project_id,
                job_id=job_id,
            )
            generation_ids.append(gv_id)
            doc_parsed_ok = True
        except Exception as e:
            doc_error = self._structured_error_detail(e)[:500]
            logger.warning("Doc parse failed for {}: {}", api_id, doc_error)

        # 解析断言 → 保存为 GenerationVersion
        rules: list[dict] = []
        try:
            rules = parse_structured_output("asserts", assert_raw)
            assert_data = rules
            assert_warnings = self._validate_assert_result(assert_data)
            if assert_warnings:
                logger.warning("Assert validation warnings for {}: {}", api_id, assert_warnings)
            # Phase 1: force 或尚无人工配置时才保存断言版本
            if force or not api.asserts:
                assert_input_tokens = self._estimate_tokens(
                    self._build_system_prompt(assert_sys_prompt, memory_ctx) + user_assert_prompt
                )
                assert_output_tokens = self._estimate_tokens(assert_raw)
                gv_id = await self._save_generation_version(
                    api_id=api_id,
                    gen_type=GenerationType.ASSERTS,
                    content={"asserts": rules},
                    summary=f"断言规则 {len(rules)} 条",
                    model=self._model,
                    latency_ms=latency_ms,
                    input_tokens=assert_input_tokens,
                    output_tokens=assert_output_tokens,
                    prompt=user_assert_prompt,
                    project_id=project_id,
                    job_id=job_id,
                )
                generation_ids.append(gv_id)
                assert_parsed_ok = True
        except Exception as e:
            assert_error = self._structured_error_detail(e)[:500]
            logger.warning("Assert parse failed for {}: {}", api_id, assert_error)

        # 编译失败原因：区分"有错误信息"和"空内容"两种情况
        failed_parts: list[str] = []
        if not doc_parsed_ok and doc_error:
            # 文档解析失败且有具体错误 → 附带错误详情
            failed_parts.append(f"AI文档生成失败: {doc_error}")
        elif not doc_parsed_ok:
            # 文档解析失败但无具体错误 → LLM 返回空内容
            failed_parts.append("AI文档生成失败: LLM返回空内容")
        if not assert_parsed_ok and assert_error:
            failed_parts.append(f"断言生成失败: {assert_error}")
        elif not assert_parsed_ok:
            failed_parts.append("断言生成失败: LLM返回空内容")
        analysis_error = "; ".join(failed_parts)

        if doc_parsed_ok or assert_parsed_ok:
            # 检查 auto_review_flow 设置：关闭时自动采纳，跳过审核流程
            auto_review = True
            try:
                doc_setting = await self._db["settings"].find_one({"key": "general_settings"})
                if doc_setting:
                    auto_review = doc_setting.get("auto_review_flow", True)
            except Exception as e:
                logger.warning("Failed to read auto_review_flow setting, defaulting to True: {}", e)

            if not auto_review and generation_ids:
                # 关闭审核流程 → 自动采纳所有生成版本，API 直接置 APPLIED
                logger.info("auto_review_flow disabled, auto-applying {} generation(s) for API {}", len(generation_ids), api_id)
                for gv_id in generation_ids:
                    try:
                        await self.apply_version(gv_id)
                    except Exception as e:
                        logger.warning("Auto-apply version {} failed: {}", gv_id, e)
                # 部分失败仍需记录 analysis_error
                if analysis_error:
                    await self._api_col.update_one(
                        {"id": api_id},
                        {"$set": {"analysis_error": analysis_error}},
                    )
                await self._set_status(api_id, AnalysisStatus.APPLIED, error=analysis_error)
                await self._log_ai_operation(
                    op_type="analyze",
                    status="success",
                    api_ids=[api_id],
                    message="文档+断言已生成（自动采纳）",
                    error=analysis_error,
                    project_id=project_id,
                )
            else:
                # 至少一个模块成功 → 标记待审核，不再直接写入 DSL（已保存为 GenerationVersion）
                update: dict[str, Any] = {"updated_at": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)}
                update["analysis_status"] = AnalysisStatus.PENDING_REVIEW
                if analysis_error:
                    # 存在部分失败 → 记录错误信息到 analysis_error 字段，前端可据此提示
                    update["analysis_error"] = analysis_error
                await self._api_col.update_one({"id": api_id}, {"$set": update})
                logger.info(
                    "API {} analyzed: doc_ok={} asserts_ok={} failures='{}', saved as GenerationVersions",
                    api_id, doc_parsed_ok, assert_parsed_ok, analysis_error,
                )
                await self._set_status(api_id, AnalysisStatus.PENDING_REVIEW, error=analysis_error)
                await self._log_ai_operation(
                    op_type="analyze",
                    status="success",
                    api_ids=[api_id],
                    message=analysis_error or "文档+断言已生成（待审核）",
                    error=analysis_error,
                    project_id=project_id,
                )
            # ── 后台提取记忆：不阻塞主流程，fire-and-forget ──
            knowledge = getattr(self, "_knowledge", None)
            if knowledge is not None:
                safe_fire_and_forget(knowledge.extract_from_api(api_id, self._call_llm), name="knowledge.extract_from_api")
            # else: knowledge 为 None → 静默跳过
            # P4: 记录 L2 项目记忆（fire-and-forget，分析结论持久化为项目知识）
            memory = getattr(self, "_memory", None)
            if memory is not None:
                api_path = api.request.path if api else doc.get("request", {}).get("path", api_id)
                api_tags = (api.doc.tags if api and api.doc else []) or []
                if doc_parsed_ok:
                    safe_fire_and_forget(
                        memory.record_l2(
                            project_id, "doc_analysis",
                            f"API 文档分析: {api.request.method} {api_path}",
                            f"分析了 {api.request.method} {api_path}，生成文档摘要: {api_doc.summary if doc_parsed_ok else 'N/A'}",
                            tags=api_tags,
                            source="ai_analyzer",
                        ),
                        name="memory.record_l2:analyze_api:doc",
                    )
                if assert_parsed_ok:
                    safe_fire_and_forget(
                        memory.record_l2(
                            project_id, "asserts_analysis",
                            f"API 断言生成: {api.request.method} {api_path}",
                            f"为 {api.request.method} {api_path} 生成 {len(rules)} 条断言规则",
                            tags=["asserts", "quality"],
                            source="ai_analyzer",
                        ),
                        name="memory.record_l2:analyze_api:asserts",
                    )
            # ── 自动触发场景生成：分析成功后入队场景队列 ──
            try:
                scenario_job_id = f"scenario:{api_id}:{uuid.uuid4().hex[:8]}"
                scenario_payload = {
                    "api_ids": [api_id],
                    "project_id": project_id,
                    "job_id": scenario_job_id,
                    "status": "queued",
                    "source_job_id": job_id,
                }
                await self._redis.rpush(AI_SCENARIO_QUEUE, json.dumps(scenario_payload, ensure_ascii=False))
                await AiJobService(self._db).mark_queued(
                    job_id=scenario_job_id,
                    type="scenario",
                    project_id=project_id,
                    source="analyzer_auto",
                    target_ids=[api_id],
                    queue_key=AI_SCENARIO_QUEUE,
                    payload=scenario_payload,
                )
                logger.info("Enqueued scenario generation for {}", api_id)
            except Exception as e:
                # 入队失败不阻塞分析结果（场景生成可后续手动触发）
                logger.warning("Failed to enqueue scenario gen for {}: {}", api_id, e)
            return True
        else:
            # 两个模块均失败 → 标记 FAILED，触发 worker 重试/DLQ 机制
            update: dict[str, Any] = {"updated_at": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)}
            update["analysis_status"] = AnalysisStatus.FAILED
            update["analysis_error"] = analysis_error or "AI文档和断言生成均失败"
            await self._api_col.update_one({"id": api_id}, {"$set": update})
            await self._set_status(api_id, AnalysisStatus.FAILED, update["analysis_error"])
            await self._log_ai_operation(
                op_type="analyze",
                status="failed",
                api_ids=[api_id],
                message=update["analysis_error"],
                error=analysis_error or "AI文档和断言生成均失败",
                project_id=project_id,
            )
            logger.warning("API {} analysis completely failed: {}", api_id, update["analysis_error"])
            return False

    # ── P1-1: 数据工厂 AI 增强 ────────────────────────────

    async def infer_data_template(self, template_id: str, project_id: str = "default", job_id: str = "") -> str | None:
        """
        P1-1: AI 增强数据模板字段配置。
        解决问题：data_factory.factory.infer_template 是纯正则推断（_infer_fields），
        只能基于字段名做简单语义匹配，无法理解业务语义、无法生成针对性边界值和异常值候选。

        AI 增强能力：
        1. 语义字段识别：结合字段名 + 示例值 + 业务上下文，更准确地匹配 faker 方法
        2. 边界值建议：根据字段类型和业务约束推断合理的 boundary_min/max
        3. invalid_values 候选：按字段类型生成针对性异常值（email→非邮箱格式、id→负数等）
        4. 注入率建议：为关键字段推荐合理的 null_rate/invalid_rate

        流程：加载模板 + 关联 API 样本 → 构建 prompt → LLM → GenerationVersion(data_template) 审核。
        不直接覆盖模板，走审核闭环保证用户可控。
        """
        t0 = time.time()
        s = get_settings()

        # 1. 加载数据模板
        tmpl_doc = await self._db["data_templates"].find_one({"id": template_id})
        if not tmpl_doc:
            logger.warning("infer_data_template: template {} not found", template_id)
            return None

        # 2. 加载关联 API 的请求/响应样本，作为 AI 推断的上下文
        api_id = tmpl_doc.get("api_id", "")
        api_doc = await self._db["api_dsls"].find_one({"id": api_id}) if api_id else None
        api_summary = ""
        sample_body = None
        if api_doc:
            api_summary = f"{api_doc.get('request', {}).get('method', '')} {api_doc.get('request', {}).get('path', '')}"
            sample_body = api_doc.get("request", {}).get("body")
            # 优先用响应体作为样本（响应通常比请求更结构化）
            resp_body = api_doc.get("response", {}).get("body")
            if resp_body:
                sample_body = resp_body

        # 3. 构建当前模板现状 JSON（供 AI 理解要增强什么）
        current_fields = tmpl_doc.get("fields", [])
        current_json = json.dumps({
            "template_name": tmpl_doc.get("name", ""),
            "api": api_summary,
            "sample_data": _safe_truncate_json(sample_body, 1500),
            "current_fields": current_fields,
        }, ensure_ascii=False, default=str)

        # 4. 构建 prompt 并调用 LLM
        user_prompt = _DATA_TEMPLATE_USER.format(template_json=current_json)
        system_prompt = await self._get_prompt("data_template")
        try:
            raw = await self._call_llm_stream(
                user_prompt, system_prompt,
                max_tokens=s.openai_max_tokens_asserts,  # 复用 asserts 的 token 预算
                job_id=template_id, task_type="data_template",
            )
        except Exception as e:
            logger.error("infer_data_template LLM failed for {}: {}", template_id, e)
            return None

        if not raw or not raw.strip():
            logger.warning("infer_data_template: empty LLM response for {}", template_id)
            return None

        # 5. 解析并校验 LLM 输出；坏结构抛出异常，由 worker 进入可见重试/DLQ。
        enhanced = parse_structured_output("data_template", raw)
        valid_fields = enhanced["fields"]

        # 7. 保存为 GenerationVersion（type=data_template），走审核闭环
        latency_ms = int((time.time() - t0) * 1000)
        content = {
            "template_id": template_id,
            "name": tmpl_doc.get("name", ""),
            "fields": valid_fields,
            "description": enhanced.get("summary") or "AI 增强字段配置",
        }
        summary = enhanced.get("summary") or f"AI 增强模板：{len(valid_fields)} 个字段"
        gv_id = await self._save_generation_version(
            api_id=api_id,
            gen_type=GenerationType.DATA_TEMPLATE,
            content=content,
            summary=summary,
            model=self._model,
            latency_ms=latency_ms,
            input_tokens=self._estimate_tokens(user_prompt),
            output_tokens=self._estimate_tokens(raw),
            prompt=user_prompt,
            project_id=project_id,
            source="data_factory",
            job_id=job_id,
        )
        logger.info("infer_data_template: generation {} saved for template {}", gv_id, template_id)
        return gv_id

    # ── P1-4: 场景步骤内联 AI 辅助 ────────────────────────

    async def recommend_step_config(self, api_id: str) -> dict[str, Any]:
        """
        P1-4: 为场景步骤推荐断言和 extract 规则（内联辅助，非审核闭环）。
        解决问题：StepEditor 的 Tests/Extract Tab 纯手工编辑，测试工程师需手动写 jsonpath 和断言。

        流程：加载 API 最近响应样本 → 构建 prompt → LLM → 返回建议（asserts + extract）。
        区别于 analyze_asserts：这是轻量级内联建议，直接返回给前端即时应用，不走 GenerationVersion。
        用户在 StepEditor 内确认后填入，无需审核流程（场景编辑本身就是即时操作）。
        """
        s = get_settings()
        # 1. 加载 API 定义和最近执行记录的响应样本
        api_doc = await self._db["api_dsls"].find_one({"id": api_id})
        if not api_doc:
            return {"error": "API not found", "asserts": [], "extract": {}}

        # 优先用最近一次成功执行的响应体作为样本（比 API 定义的 response 更真实）
        sample_body = None
        exec_doc = await self._db["executions"].find_one(
            {"api_id": api_id, "passed": True},
            sort=[("started_at", -1)],
        )
        if exec_doc and exec_doc.get("steps"):
            sample_body = exec_doc["steps"][0].get("response_received", {}).get("body")
        # 回退到 API 定义的响应体
        if sample_body is None:
            sample_body = api_doc.get("response", {}).get("body")

        # 2. 构建 prompt
        api_summary = {
            "method": api_doc.get("request", {}).get("method", ""),
            "path": api_doc.get("request", {}).get("path", ""),
            "status_code": api_doc.get("response", {}).get("status_code", 0),
            "sample_response": _safe_truncate_json(sample_body, 1500),
        }
        user_prompt = _STEP_RECOMMEND_USER.format(api_json=json.dumps(api_summary, ensure_ascii=False, default=str))
        system_prompt = _STEP_RECOMMEND_SYSTEM

        # 3. 调用 LLM（非流式，内联建议需要快速返回）
        try:
            raw = await self._call_llm(
                user_prompt, system_prompt,
                max_tokens=s.openai_max_tokens_asserts,
                task_type="asserts",
            )
        except Exception as e:
            logger.error("recommend_step_config LLM failed for {}: {}", api_id, e)
            return {"error": str(e)[:200], "asserts": [], "extract": {}}

        if not raw or not raw.strip():
            return {"error": "empty response", "asserts": [], "extract": {}}

        # 4. 解析建议；内联辅助保留有效断言/extract，坏 JSON 返回 error 不影响主流程。
        try:
            recommends = parse_structured_output("step_recommend", raw)
        except Exception as e:
            return {"error": self._structured_error_detail(e)[:200], "asserts": [], "extract": {}}

        logger.info("recommend_step_config: api={} → {} asserts, {} extract",
                     api_id, len(recommends["asserts"]), len(recommends["extract"]))
        return recommends

    # ── 场景用例生成 ──────────────────────────────────────

    async def generate_scenarios(
        self, api_ids: list[str], project_id: str = "default", scenario_type: str = "", job_id: str = ""
    ) -> list[str]:
        s = get_settings()  # 修复 NameError: 方法内需独立获取 settings
        docs = await self._api_col.find({"id": {"$in": api_ids}, "project_id": project_id}).to_list(length=100)
        if not docs:
            # 所有 api_id 都不存在（可能已删除）→ 返回空列表，worker 层会广播失败
            return []

        apis_summary = [
            {
                "id": d["id"],
                "name": d.get("name", ""),
                "method": d["request"]["method"],
                "path": d["request"]["path"],
                "summary": (d.get("doc") or {}).get("summary", ""),
                "params": [(p["name"], p["location"])
                           for p in (d.get("doc") or {}).get("params", [])],
            }
            for d in docs
        ]

        # ── ReMe 记忆检索：场景生成时检索更多记忆（limit=10），获取更丰富的模式参考 ──
        scenario_memory_ctx = ""
        knowledge = getattr(self, "_knowledge", None)
        if knowledge is not None and apis_summary:
            try:
                # 聚合所有 API 的信息作为检索上下文，而非仅用第一个 API
                all_paths = [a.get("path", "") for a in apis_summary if a.get("path")]
                all_methods = [a.get("method", "") for a in apis_summary if a.get("method")]
                all_summaries = [a.get("summary", "") for a in apis_summary if a.get("summary")]
                context = {
                    "path": all_paths[0] if all_paths else "",
                    "paths": all_paths,
                    "method": ", ".join(set(all_methods)),
                    "summary": " | ".join(all_summaries[:3]),  # 取前3个摘要拼接
                    "api_count": len(apis_summary),
                }
                entries = await knowledge.retrieve(project_id, context, limit=10)
                scenario_memory_ctx = knowledge.format_context(entries)
            except Exception as e:
                # 检索失败不阻塞场景生成（非关键路径）
                logger.warning("Scenario memory retrieval failed: {}", e)
        # else: knowledge 为 None 或 apis_summary 为空 → 静默跳过

        # 场景生成输出较复杂（多场景+多步骤），max_tokens 从 settings 读取
        # 根据 scenario_type 构建类型特定的生成指令
        type_instruction = _build_scenario_type_instruction(scenario_type, len(apis_summary))
        # Phase 1: 记录 LLM 调用耗时，用于 GenerationVersion 的 latency_ms 字段
        t0 = time.time()
        try:
            raw = await self._call_llm(
                _SCENARIO_USER.format(
                    apis_json=json.dumps(apis_summary, ensure_ascii=False)
                ),
                self._build_system_prompt(_SCENARIO_SYSTEM + type_instruction, scenario_memory_ctx),
                max_tokens=s.openai_max_tokens_scenario,
                task_type="scenario",
            )
            scenarios_data = parse_structured_output("scenario", raw)
            # 结构化输出验证
            scenario_warnings = self._validate_scenario_result(scenarios_data)
            if scenario_warnings:
                # 结构不完整但不阻塞流程（记录警告供调试）
                logger.warning("Scenario validation warnings: {}", scenario_warnings)
        except Exception as e:
            logger.error("Scenario generation LLM failed: {}", e)
            # 让异常传播到 worker，由重试/DLQ 机制处理，而非静默返回空列表
            raise

        # Phase 1: 计算 LLM 调用耗时和 token 估算（与 doc/asserts 一致）
        latency_ms = int((time.time() - t0) * 1000)
        input_tokens = self._estimate_tokens(
            self._build_system_prompt(_SCENARIO_SYSTEM + type_instruction, scenario_memory_ctx)
            + _SCENARIO_USER.format(apis_json=json.dumps(apis_summary, ensure_ascii=False))
        )
        output_tokens = self._estimate_tokens(raw)

        # 构建 api_id → API 名称映射，供步骤名回退使用
        api_name_map = {d["id"]: d.get("name", "") for d in docs}
        valid_api_ids = {d["id"] for d in docs}

        gen_version_ids: list[str] = []
        for idx, s in enumerate(scenarios_data):
            try:
                s = self._normalize_scenario_graph(s, scenario_type, api_ids, api_name_map, valid_api_ids, idx)
                raw_steps = s.get("steps", [])
                steps = []
                for step in raw_steps:
                    # LLM 未提供 step.name 时，从对应 API 的 name 回退（保证步骤有可读标识）
                    if not step.get("name"):
                        step["name"] = api_name_map.get(step.get("api_id", ""), "")
                    # 修复 Bug 4a：LLM 未提供 step_id 时生成默认值，避免 ScenarioStep ValidationError
                    if not step.get("step_id"):
                        step["step_id"] = f"step_{int(time.time()*1000)}_{idx}"
                    steps.append(ScenarioStep(**step))
                # Phase 1: 不再直接插入场景 DSL，而是保存为待审核的 GenerationVersion
                # 场景的 DSL 构建信息保留在 content 中，审核通过后由 apply_version() 写入
                scenario_content = {
                    "name": s.get("name", "未命名场景"),
                    "description": s.get("description", ""),
                    "test_goal": s.get("test_goal", ""),
                    "coverage_tags": s.get("coverage_tags", []),
                    "steps": [step.model_dump() for step in steps],
                    "scenario_type": scenario_type,
                }
                scenario_summary = f"场景: {scenario_content['name']} ({len(steps)} 步骤)"
                gv_id = await self._save_generation_version(
                    api_id=api_ids[0] if api_ids else "",
                    gen_type=GenerationType.SCENARIO,
                    content=scenario_content,
                    summary=scenario_summary,
                    model=self._model,
                    latency_ms=latency_ms,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    prompt=_SCENARIO_USER.format(apis_json=json.dumps(apis_summary, ensure_ascii=False)),
                    api_ids=api_ids,
                    project_id=project_id,
                    job_id=job_id,
                )
                gen_version_ids.append(gv_id)
                logger.info("Scenario GenerationVersion saved: '{}' ({})", scenario_content["name"], gv_id)
            except Exception as e:
                # 单个场景保存失败不影响其他场景（部分成功优于全部失败）
                # exc_info=True 输出完整 traceback，方便排查 LLM 返回错误类型等问题
                logger.warning("Failed to save scenario GenerationVersion: %s", e, exc_info=True)

        return gen_version_ids

    def _normalize_scenario_graph(
        self,
        scene: dict[str, Any],
        scenario_type: str,
        api_ids: list[str],
        api_name_map: dict[str, str],
        valid_api_ids: set[str],
        scene_index: int = 0,
    ) -> dict[str, Any]:
        """将 LLM 输出确定性修复为新版显式图 DSL。"""
        if not isinstance(scene, dict):
            scene = {}
        raw_steps = scene.get("steps") if isinstance(scene.get("steps"), list) else []
        steps: list[dict[str, Any]] = []

        has_start = any(s.get("type") == "start" or s.get("step_id") == "start" for s in raw_steps if isinstance(s, dict))
        if not has_start:
            steps.append({"type": "start", "step_id": "start", "name": "START", "depends_on": [], "start_params": []})

        used_ids = {"start", "end"}
        last_top = "start"
        for i, raw in enumerate(raw_steps):
            if not isinstance(raw, dict):
                continue
            step = dict(raw)
            stype = step.get("type") or ("start" if step.get("step_id") == "start" else "end" if step.get("step_id") == "end" else "api")
            step["type"] = stype
            if stype == "start":
                step["step_id"] = "start"
                step.setdefault("name", "START")
                step["depends_on"] = []
                # LLM 可能返回非 list 类型的 start_params，改用类型安全赋值
                if not isinstance(step.get("start_params"), list):
                    step["start_params"] = []
                if not any(s.get("step_id") == "start" for s in steps):
                    steps.append(step)
                continue
            if stype == "end":
                continue

            sid = step.get("step_id") or f"{stype}_{i + 1}"
            base_sid = sid
            n = 2
            while sid in used_ids:
                sid = f"{base_sid}_{n}"
                n += 1
            used_ids.add(sid)
            step["step_id"] = sid
            step.setdefault("name", api_name_map.get(step.get("api_id", ""), sid))
            # 修复 Bug 4a：LLM 可能返回错误类型（如 depends_on 为字符串），
            # setdefault 无法覆盖已有错误值，改用类型安全赋值确保 Pydantic 校验通过
            if not isinstance(step.get("depends_on"), list):
                step["depends_on"] = [last_top] if not step.get("parent_id") else []
            if not isinstance(step.get("extract"), dict):
                step["extract"] = {}
            if not isinstance(step.get("override_params"), dict):
                step["override_params"] = {}
            if not isinstance(step.get("override_headers"), dict):
                step["override_headers"] = {}
            if not isinstance(step.get("assertions"), list):
                step["assertions"] = []

            if stype == "api":
                if step.get("api_id") not in valid_api_ids:
                    step["api_id"] = api_ids[min(i, len(api_ids) - 1)] if api_ids else ""
                if not step["assertions"]:
                    step["assertions"] = [{"source": "status", "path": "status_code", "operator": "gte", "expected": 200}]
            elif stype == "condition":
                step.setdefault("api_id", "")
                # LLM 可能返回非 dict 的 condition，setdefault 无法覆盖错误值
                if not isinstance(step.get("condition"), dict):
                    step["condition"] = {"variable": "", "operator": "exists", "value": None, "on_false": "skip"}
            elif stype == "loop":
                step.setdefault("api_id", "")
                # LLM 可能返回非 dict 的 loop（如字符串），setdefault/get 无法正确处理
                if not isinstance(step.get("loop"), dict):
                    if step.get("loop_var"):
                        step["loop"] = {"mode": "list", "list_ref": f"{{{{{step['loop_var']}}}}}", "item_alias": "item"}
                    else:
                        step["loop"] = {"mode": "count", "count": step.get("loop_count") or 2, "list_ref": "", "item_alias": "item"}
            steps.append(step)
            if not step.get("parent_id"):
                last_top = sid

        leaf_ids = [s["step_id"] for s in steps if s.get("type") not in {"start", "condition", "loop"} and s.get("step_id") != "end"]
        if not leaf_ids:
            api_id = api_ids[0] if api_ids else ""
            leaf_ids = ["step_1"]
            steps.append({
                "type": "api", "step_id": "step_1", "api_id": api_id,
                "name": api_name_map.get(api_id, "API 调用"), "depends_on": ["start"],
                "extract": {}, "override_params": {}, "override_headers": {},
                "assertions": [{"source": "status", "path": "status_code", "operator": "gte", "expected": 200}],
            })
        steps.append({"type": "end", "step_id": "end", "name": "END", "depends_on": leaf_ids})

        scene["steps"] = steps
        scene.setdefault("name", f"AI 场景 {scene_index + 1}")
        scene.setdefault("description", scene.get("test_goal", "AI 生成场景"))
        scene.setdefault("coverage_tags", [scenario_type or "multi"])
        return scene

    async def generate_monitors(
        self,
        project_id: str,
        target_type: str = "",
        target_ids: list[str] | None = None,
        goal: str = "",
        risk_preference: str = "",
        schedule_preference: str = "",
        job_id: str = "",
    ) -> str | None:
        """AI 生成巡检监控配置，结果只保存为 GenerationVersion 待审核。"""
        target_ids = target_ids or []
        t0 = time.time()
        candidates: list[dict[str, Any]] = []

        async def add_api_candidates():
            q: dict[str, Any] = {"project_id": project_id}
            if target_ids:
                q["id"] = {"$in": target_ids}
            docs = await self._db["api_dsls"].find(q, {"_id": 0}).limit(30).to_list(30)
            for d in docs:
                req = d.get("request") or {}
                candidates.append({
                    "target_type": "api",
                    "target_id": d.get("id", ""),
                    "name": d.get("name", ""),
                    "method": req.get("method", ""),
                    "path": req.get("path", ""),
                    "analysis_status": d.get("analysis_status", ""),
                    "assert_count": len(d.get("asserts") or []),
                    "quality_score": d.get("quality_score", 0),
                })

        async def add_scenario_candidates():
            q: dict[str, Any] = {"project_id": project_id}
            if target_ids:
                q["id"] = {"$in": target_ids}
            docs = await self._db["scenarios"].find(q, {"_id": 0}).limit(30).to_list(30)
            for d in docs:
                candidates.append({
                    "target_type": "scenario",
                    "target_id": d.get("id", ""),
                    "name": d.get("name", ""),
                    "step_count": len(d.get("steps") or []),
                    "scenario_type": d.get("scenario_type", ""),
                })

        async def add_template_candidates():
            q: dict[str, Any] = {"project_id": project_id}
            if target_ids:
                q["id"] = {"$in": target_ids}
            docs = await self._db["data_templates"].find(q, {"_id": 0}).limit(30).to_list(30)
            for d in docs:
                candidates.append({
                    "target_type": "data_factory",
                    "target_id": d.get("id", ""),
                    "name": d.get("name", ""),
                    "field_count": len(d.get("fields") or []),
                    "source": d.get("source", ""),
                })

        if target_type == "api" or not target_type:
            await add_api_candidates()
        if target_type == "scenario":
            await add_scenario_candidates()
        if target_type == "data_factory":
            await add_template_candidates()
        if not candidates:
            return None

        existing = await self._db["monitors"].find(
            {"project_id": project_id},
            {"_id": 0, "target_type": 1, "target_id": 1, "risk_level": 1, "interval": 1, "cron": 1},
        ).to_list(200)
        recent_alerts = await self._db["alert_records"].find(
            {"project_id": project_id},
            {"_id": 0, "monitor_id": 1, "title": 1, "risk_level": 1, "ai_severity": 1},
        ).sort("sent_at", -1).limit(10).to_list(10)
        channels = await self._db["alert_channels"].find(
            {"project_id": project_id, "enabled": True},
            {"_id": 0, "id": 1, "name": 1, "type": 1, "url": 1},
        ).limit(10).to_list(10)
        context = {
            "goal": goal,
            "risk_preference": risk_preference,
            "schedule_preference": schedule_preference,
            "candidates": candidates,
            "existing_monitors": existing,
            "recent_alerts": recent_alerts,
            "alert_channels": [{k: c.get(k) for k in ("id", "name", "type")} for c in channels],
        }
        user_prompt = _MONITOR_USER.format(monitor_context=json.dumps(context, ensure_ascii=False, default=str))
        raw = ""
        try:
            raw = await self._call_llm(user_prompt, await self._get_prompt("monitor"), max_tokens=1800, task_type="monitor")
        except Exception as e:
            logger.warning("generate_monitors LLM failed: {}", e)
            raise
        parsed = parse_structured_output("monitor", raw)

        candidate_by_id = {c["target_id"]: c for c in candidates}
        raw_monitors = parsed.get("monitors") or []

        monitors: list[dict[str, Any]] = []
        for item in raw_monitors:
            if not isinstance(item, dict):
                continue
            target_id = item.get("target_id") or item.get("api_id") or ""
            cand = candidate_by_id.get(target_id)
            if not cand:
                continue
            target_type = cand["target_type"]
            monitors.append({
                "id": item.get("id") or f"{target_type}:{target_id}",
                "name": item.get("name") or f"AI 巡检 {target_id[:8]}",
                "target_type": target_type,
                "target_id": target_id,
                "api_id": target_id if target_type == "api" else "",
                "interval": item.get("interval") or "5m",
                "cron": item.get("cron") or "",
                "risk_level": item.get("risk_level") if item.get("risk_level") in {"low", "medium", "high", "critical"} else "medium",
                "max_consecutive_failures": int(item.get("max_consecutive_failures") or 3),
                "diff_threshold": int(item.get("diff_threshold") or 3),
                "diff_ignore_paths": item.get("diff_ignore_paths") if isinstance(item.get("diff_ignore_paths"), list) else [],
                "asserts": item.get("asserts") if isinstance(item.get("asserts"), list) else [],
                "alert_channels": [c for c in (item.get("alert_channels") or []) if isinstance(c, str)],
                "enabled": bool(item.get("enabled", True)),
                "description": item.get("description", ""),
                "project_id": project_id,
            })

        if not monitors:
            return None
        latency_ms = int((time.time() - t0) * 1000)
        content = {"monitors": monitors, "summary": parsed.get("summary") or f"生成 {len(monitors)} 个巡检建议"}
        gv_id = await self._save_generation_version(
            api_id=monitors[0].get("api_id", ""),
            gen_type=GenerationType.MONITOR,
            content=content,
            summary=content["summary"],
            model=self._model,
            latency_ms=latency_ms,
            input_tokens=self._estimate_tokens(user_prompt),
            output_tokens=self._estimate_tokens(raw or json.dumps(content, ensure_ascii=False)),
            prompt=user_prompt,
            api_ids=[m["target_id"] for m in monitors if m["target_type"] == "api"],
            project_id=project_id,
            job_id=job_id,
        )
        return gv_id

    # ── Worker：多队列并发（analyze/doc/asserts + scenario） ──

    async def run_all_workers(self):
        """
        并发运行所有 AI worker，使用 supervisor 模式自动重启异常退出的 worker。
        解决旧版 FIRST_COMPLETED 的缺陷：任一 worker 崩溃会导致所有 worker 被取消。
        每个 worker 在独立 supervisor 协程中运行，异常退出时自动重启（记录日志）。
        仅在收到 CancelledError（服务关闭信号）时退出。
        """
        logger.info("Starting AI workers (analyze + analyze_doc + analyze_asserts + scenario + data_template + monitor)")

        async def _run_with_restart_inner(worker_coro, worker_name: str):
            """在无限循环中运行 worker，异常时自动重启（CancelledError 除外）"""
            while True:
                try:
                    await worker_coro
                except asyncio.CancelledError:
                    # 服务关闭信号 → 优雅退出，不再重启
                    logger.info("AI worker {} cancelled, exiting", worker_name)
                    break
                except Exception as e:
                    # 未知异常 → 记录日志并自动重启
                    logger.error("AI worker {} crashed: {}, restarting in 5s...", worker_name, e)
                    await asyncio.sleep(5)

        async def _supervised_worker(worker_fn, worker_name: str):
            """为单个 worker 创建带自动重启的监督协程"""
            await _run_with_restart_inner(worker_fn(), worker_name)

        # 每个 worker 在独立协程中运行，互不影响
        await asyncio.gather(
            asyncio.create_task(_supervised_worker(self._run_analyze_worker, "analyze")),
            asyncio.create_task(_supervised_worker(self._run_analyze_doc_worker, "analyze_doc")),
            asyncio.create_task(_supervised_worker(self._run_analyze_asserts_worker, "analyze_asserts")),
            asyncio.create_task(_supervised_worker(self._run_scenario_worker, "scenario")),
            asyncio.create_task(_supervised_worker(self._run_data_template_worker, "data_template")),
            asyncio.create_task(_supervised_worker(self._run_monitor_worker, "monitor")),
        )

    async def _run_analyze_worker(self, concurrency: int = 3):
        """
        queue:ai_analyze 消费者 —— API 文档+断言分析。
        状态变更通过 analyze_api 内部的 _set_status 广播。
        """
        logger.info("AI analyze worker started (concurrency={})", concurrency)
        sem = asyncio.Semaphore(concurrency)

        async def process_one(task_raw: bytes | str):
            async with sem:
                fail_count = 0
                try:
                    task = json.loads(task_raw)
                    api_id = task["api_id"]
                    fail_count = task.get("fail_count", 0)
                    force = task.get("force", False)  # force=True 时强制重分析已完成的 API
                    job_id = task.get("job_id") or f"api:{api_id}"
                    project_id = task.get("project_id", "default")
                except Exception as e:
                    # 消息格式损坏 → 丢弃（无法重试），记录日志
                    logger.error("Bad task payload: {}", e)
                    return

                try:
                    await AiJobService(self._db).mark_running(
                        job_id=job_id,
                        type="ai_analyze",
                        project_id=project_id,
                        source="analyzer_worker",
                        target_ids=[api_id],
                        queue_key=AI_ANALYZE_QUEUE,
                        retry_count=fail_count,
                        payload=task,
                    )
                    ok = await asyncio.wait_for(
                        self.analyze_api(api_id, force=force, job_id=job_id),
                        timeout=600,
                    )
                    if not ok:
                        # 分析失败（非异常）→ 增加 fail_count 重试
                        await self._requeue_or_dlq(api_id, fail_count + 1, job_id=job_id, project_id=project_id)
                except asyncio.TimeoutError:
                    # 任务执行超时 → 增加 fail_count 重试，释放信号量槽位
                    logger.error("Analyze task timed out (600s) for api_id={}", api_id)
                    await self._requeue_or_dlq(api_id, fail_count + 1, job_id=job_id, project_id=project_id, error="timeout")
                except RateLimitError:
                    # 限流错误：暂停60s后重入队，不增加 fail_count（限制侧问题，非任务本身失败）
                    logger.warning("Rate limit hit, requeue api_id={}", api_id)
                    await asyncio.sleep(60)
                    await self._requeue_or_dlq(api_id, fail_count, job_id=job_id, project_id=project_id)
                except APIError as e:
                    # API 错误（如 500）→ 增加 fail_count 重试
                    logger.error("OpenAI API error for {}: {}", api_id, e)
                    await self._requeue_or_dlq(api_id, fail_count + 1, job_id=job_id, project_id=project_id, error=str(e))
                except Exception as e:
                    # 未知错误 → 保守策略：增加 fail_count 重试
                    logger.error("Worker task error for {}: {}", api_id, e)
                    await self._requeue_or_dlq(api_id, fail_count + 1, job_id=job_id, project_id=project_id, error=str(e))

        while True:
            try:
                result = await self._redis.blpop(AI_ANALYZE_QUEUE, timeout=5)
                if result:
                    # 队列中有消息 → 创建新协程处理（不阻塞主循环继续取消息）
                    _, task_raw = result
                    asyncio.create_task(process_one(task_raw))
                # result 为 None（超时）→ 继续循环等待
            except asyncio.CancelledError:
                # 收到取消信号 → 优雅退出循环
                logger.info("AI analyze worker cancelled")
                break
            except Exception as e:
                # blpop 异常 → 短暂休眠后重试，避免快速失败循环
                logger.error("Analyze worker loop error: {}", e)
                await asyncio.sleep(2)

    async def _run_analyze_doc_worker(self, concurrency: int = 3):
        """
        queue:ai_analyze_doc 消费者 —— 仅 AI 文档生成（不生成断言）。
        状态变更通过 analyze_doc 内部的 _set_status 广播。
        """
        logger.info("AI analyze doc worker started (concurrency={})", concurrency)
        sem = asyncio.Semaphore(concurrency)

        async def process_one(task_raw: bytes | str):
            async with sem:
                fail_count = 0
                try:
                    task = json.loads(task_raw)
                    api_id = task["api_id"]
                    fail_count = task.get("fail_count", 0)
                    force = task.get("force", False)
                    job_id = task.get("job_id") or f"doc:{api_id}"
                    project_id = task.get("project_id", "default")
                except Exception as e:
                    logger.error("Bad doc task payload: {}", e)
                    return

                try:
                    await AiJobService(self._db).mark_running(
                        job_id=job_id,
                        type="doc",
                        project_id=project_id,
                        source="analyzer_worker",
                        target_ids=[api_id],
                        queue_key="queue:ai_analyze_doc",
                        retry_count=fail_count,
                        payload=task,
                    )
                    ok = await asyncio.wait_for(
                        self.analyze_doc(api_id, force=force, job_id=job_id),
                        timeout=600,
                    )
                    if not ok:
                        # 文档分析失败（非异常）→ 增加 fail_count 重试
                        await self._requeue_or_dlq(api_id, fail_count + 1, target_queue="queue:ai_analyze_doc", job_id=job_id, project_id=project_id)
                except asyncio.TimeoutError:
                    # 文档分析超时 → 增加 fail_count 重试，释放信号量槽位
                    logger.error("Doc worker task timed out (600s) for api_id={}", api_id)
                    await self._requeue_or_dlq(api_id, fail_count + 1, target_queue="queue:ai_analyze_doc", job_id=job_id, project_id=project_id, error="timeout")
                except RateLimitError:
                    # 限流错误：暂停60s后重入队，不增加 fail_count（限制侧问题，非任务本身失败）
                    logger.warning("Doc worker rate limit hit, requeue api_id={}", api_id)
                    await asyncio.sleep(60)
                    await self._requeue_or_dlq(api_id, fail_count, target_queue="queue:ai_analyze_doc", job_id=job_id, project_id=project_id)
                except APIError as e:
                    # API 错误（如 500）→ 增加 fail_count 重试
                    logger.error("OpenAI API error for doc {}: {}", api_id, e)
                    await self._requeue_or_dlq(api_id, fail_count + 1, target_queue="queue:ai_analyze_doc", job_id=job_id, project_id=project_id, error=str(e))
                except Exception as e:
                    # 未知错误 → 保守策略：增加 fail_count 重试
                    logger.error("Doc worker task error for {}: {}", api_id, e)
                    await self._requeue_or_dlq(api_id, fail_count + 1, target_queue="queue:ai_analyze_doc", job_id=job_id, project_id=project_id, error=str(e))

        while True:
            try:
                result = await self._redis.blpop("queue:ai_analyze_doc", timeout=5)
                if result:
                    # 队列中有消息 → 创建新协程处理（不阻塞主循环继续取消息）
                    _, task_raw = result
                    asyncio.create_task(process_one(task_raw))
                # result 为 None（超时）→ 继续循环等待
            except asyncio.CancelledError:
                # 收到取消信号 → 优雅退出循环
                logger.info("AI analyze doc worker cancelled")
                break
            except Exception as e:
                # blpop 异常 → 短暂休眠后重试，避免快速失败循环
                logger.error("Doc worker loop error: {}", e)
                await asyncio.sleep(2)

    async def _run_analyze_asserts_worker(self, concurrency: int = 3):
        """
        queue:ai_analyze_asserts 消费者 —— 仅 AI 断言生成（不生成文档）。
        状态变更通过 analyze_asserts 内部的 _set_status 广播。
        """
        logger.info("AI analyze asserts worker started (concurrency={})", concurrency)
        sem = asyncio.Semaphore(concurrency)

        async def process_one(task_raw: bytes | str):
            async with sem:
                fail_count = 0
                try:
                    task = json.loads(task_raw)
                    api_id = task["api_id"]
                    fail_count = task.get("fail_count", 0)
                    force = task.get("force", False)
                    job_id = task.get("job_id") or f"asserts:{api_id}"
                    project_id = task.get("project_id", "default")
                except Exception as e:
                    logger.error("Bad asserts task payload: {}", e)
                    return

                try:
                    await AiJobService(self._db).mark_running(
                        job_id=job_id,
                        type="asserts",
                        project_id=project_id,
                        source="analyzer_worker",
                        target_ids=[api_id],
                        queue_key="queue:ai_analyze_asserts",
                        retry_count=fail_count,
                        payload=task,
                    )
                    ok = await asyncio.wait_for(
                        self.analyze_asserts(api_id, force=force, job_id=job_id),
                        timeout=600,
                    )
                    if not ok:
                        # 断言分析失败（非异常）→ 增加 fail_count 重试
                        await self._requeue_or_dlq(api_id, fail_count + 1, target_queue="queue:ai_analyze_asserts", job_id=job_id, project_id=project_id)
                except asyncio.TimeoutError:
                    # 断言分析超时 → 增加 fail_count 重试，释放信号量槽位
                    logger.error("Asserts worker task timed out (600s) for api_id={}", api_id)
                    await self._requeue_or_dlq(api_id, fail_count + 1, target_queue="queue:ai_analyze_asserts", job_id=job_id, project_id=project_id, error="timeout")
                except RateLimitError:
                    # 限流错误：暂停60s后重入队，不增加 fail_count（限制侧问题，非任务本身失败）
                    logger.warning("Asserts worker rate limit hit, requeue api_id={}", api_id)
                    await asyncio.sleep(60)
                    await self._requeue_or_dlq(api_id, fail_count, target_queue="queue:ai_analyze_asserts", job_id=job_id, project_id=project_id)
                except APIError as e:
                    # API 错误（如 500）→ 增加 fail_count 重试
                    logger.error("OpenAI API error for asserts {}: {}", api_id, e)
                    await self._requeue_or_dlq(api_id, fail_count + 1, target_queue="queue:ai_analyze_asserts", job_id=job_id, project_id=project_id, error=str(e))
                except Exception as e:
                    # 未知错误 → 保守策略：增加 fail_count 重试
                    logger.error("Asserts worker task error for {}: {}", api_id, e)
                    await self._requeue_or_dlq(api_id, fail_count + 1, target_queue="queue:ai_analyze_asserts", job_id=job_id, project_id=project_id, error=str(e))

        while True:
            try:
                result = await self._redis.blpop("queue:ai_analyze_asserts", timeout=5)
                if result:
                    # 队列中有消息 → 创建新协程处理（不阻塞主循环继续取消息）
                    _, task_raw = result
                    asyncio.create_task(process_one(task_raw))
                # result 为 None（超时）→ 继续循环等待
            except asyncio.CancelledError:
                # 收到取消信号 → 优雅退出循环
                logger.info("AI analyze asserts worker cancelled")
                break
            except Exception as e:
                # blpop 异常 → 短暂休眠后重试，避免快速失败循环
                logger.error("Asserts worker loop error: {}", e)
                await asyncio.sleep(2)

    async def _run_scenario_worker(self):
        """
        queue:ai_scenario 消费者 —— 后台异步生成场景用例。
        路由 POST /scenarios/generate 只负责入队并立即返回 {queued:true}，
        该 worker 完成 LLM 调用后广播 {type: "scenario_done", generation_ids: [...]}
        或 {type: "scenario_failed", api_ids: [...], error: "..."}。
        场景不再直接插入 scenarios 集合，改为保存为 GenerationVersion（status=pending_review）。
        """
        # 场景生成也限制并发（LLM 调用较慢，避免同时生成过多场景导致 API 限流）
        scenario_sem = asyncio.Semaphore(2)
        logger.info("AI scenario worker started (concurrency=2)")

        async def process_one(task_raw: bytes | str):
            """信号量控制并发 + 异常捕获广播失败事件 + 重试/DLQ 机制"""
            async with scenario_sem:
                task = json.loads(task_raw)
                api_ids = task.get("api_ids", [])
                project_id = task.get("project_id", "default")
                fail_count = task.get("fail_count", 0)
                scenario_type = task.get("scenario_type", "")  # single/multi/complex，控制生成策略
                job_id = task.get("job_id") or str(uuid.uuid4())
                user_id = task.get("user_id", "")
                try:
                    await AiJobService(self._db).mark_running(
                        job_id=job_id,
                        type="scenario",
                        project_id=project_id,
                        source="scenario_worker",
                        target_ids=api_ids,
                        queue_key=AI_SCENARIO_QUEUE,
                        retry_count=fail_count,
                        payload=task,
                        user_id=user_id,
                    )
                    await self._broadcast(f"ai_analysis:{project_id}", {
                        "type": "scenario_generation",
                        "project_id": project_id,
                        "job_id": job_id,
                        "scenario_type": scenario_type,
                        "api_ids": api_ids,
                        "status": "running",
                    })
                    gen_version_ids = await asyncio.wait_for(
                        self.generate_scenarios(api_ids, project_id, scenario_type, job_id=job_id),
                        timeout=600,
                    )
                    # Phase 1: 广播场景生成版本 ID（待审核），不再广播实际场景 ID
                    # 修复 Bug 4b：仅在 gen_version_ids 非空时广播 pending_review，避免空版本广播
                    if gen_version_ids:
                        await self._broadcast(f"ai_analysis:{project_id}", {
                            "type": "scenario_done",
                            "project_id": project_id,
                            "job_id": job_id,
                            "status": "pending_review",
                            "scenario_type": scenario_type,
                            "generation_ids": gen_version_ids,
                            "api_ids": api_ids,
                        })
                        # 持久化 AI 操作日志：场景生成成功（GenerationVersion 待审核）
                        await self._log_ai_operation(
                            op_type="scenario",
                            status="success",
                            api_ids=api_ids,
                            generation_ids=gen_version_ids,
                            message=f"成功生成 {len(gen_version_ids)} 个场景版本（待审核）",
                            project_id=project_id,
                        )
                        await AiJobService(self._db).mark_pending_review(
                            job_id=job_id,
                            type="scenario",
                            project_id=project_id,
                            source="scenario_worker",
                            target_ids=api_ids,
                            generation_ids=gen_version_ids,
                            user_id=user_id,
                        )
                    else:
                        # gen_version_ids 为空时广播失败并标记 job 为 failed
                        logger.warning("Scenario generation produced no versions for api_ids={}", api_ids)
                        await self._broadcast(f"ai_analysis:{project_id}", {
                            "type": "scenario_generation",
                            "project_id": project_id,
                            "job_id": job_id,
                            "status": "failed",
                            "scenario_type": scenario_type,
                            "api_ids": api_ids,
                            "error": "No valid scenario versions generated (all ScenarioStep validation failed)",
                        })
                        await AiJobService(self._db).mark_failed(
                            job_id=job_id,
                            type="scenario",
                            project_id=project_id,
                            source="scenario_worker",
                            target_ids=api_ids,
                            error="No valid scenario versions generated",
                            user_id=user_id,
                        )
                except asyncio.TimeoutError:
                    # 场景生成超时 → 增加 fail_count 重试，释放信号量槽位
                    logger.error("Scenario worker task timed out (600s) for api_ids={}", api_ids)
                    await self._broadcast(f"ai_analysis:{project_id}", {
                        "type": "scenario_generation",
                        "project_id": project_id,
                        "job_id": job_id,
                        "scenario_type": scenario_type,
                        "api_ids": api_ids,
                        "status": "retry",
                        "error": "timeout",
                    })
                    await self._requeue_scenario_or_dlq(api_ids, project_id, fail_count + 1, scenario_type, job_id=job_id, user_id=user_id)
                except RateLimitError:
                    # 限流错误：暂停60s后重入队，不增加 fail_count（限制侧问题，非任务本身失败）
                    logger.warning("Scenario worker rate limit hit, requeue api_ids={}", api_ids)
                    await self._broadcast(f"ai_analysis:{project_id}", {
                        "type": "scenario_generation",
                        "project_id": project_id,
                        "job_id": job_id,
                        "scenario_type": scenario_type,
                        "api_ids": api_ids,
                        "status": "retry",
                        "error": "rate_limited",
                    })
                    await asyncio.sleep(60)
                    await self._requeue_scenario_or_dlq(api_ids, project_id, fail_count, scenario_type, job_id=job_id, user_id=user_id)
                except APIError as e:
                    # API 错误（如 500）→ 增加 fail_count 重试
                    logger.error("Scenario worker API error for {}: {}", api_ids, e)
                    await self._broadcast(f"ai_analysis:{project_id}", {
                        "type": "scenario_generation",
                        "project_id": project_id,
                        "job_id": job_id,
                        "scenario_type": scenario_type,
                        "api_ids": api_ids,
                        "status": "retry",
                        "error": str(e)[:500],
                    })
                    await self._requeue_scenario_or_dlq(api_ids, project_id, fail_count + 1, scenario_type, job_id=job_id, user_id=user_id)
                except Exception as e:
                    # 未知错误 → 增加 fail_count 重试；超过上限时广播失败 + 持久化错误信息
                    error_detail = self._structured_error_detail(e)
                    logger.error("Scenario generation failed for {}: {}", api_ids, error_detail)
                    await self._requeue_scenario_or_dlq(api_ids, project_id, fail_count + 1, scenario_type, job_id=job_id, user_id=user_id, error=error_detail)
                    # 只在超过最大重试次数时广播失败事件（避免每次重试都广播造成噪音）
                    if fail_count + 1 >= MAX_RETRY:
                        await self._broadcast(f"ai_analysis:{project_id}", {
                            "type": "scenario_failed",
                            "project_id": project_id,
                            "job_id": job_id,
                            "scenario_type": scenario_type,
                            "status": "dlq",
                            "api_ids": api_ids,
                            "error": error_detail[:500],
                        })
                        # 持久化场景生成失败原因到 API 文档，即使错过 WS toast 也能在详情页看到
                        try:
                            await self._api_col.update_many(
                                {"id": {"$in": api_ids}},
                                {"$set": {"scenario_error": error_detail[:500]}},
                            )
                        except Exception as ex:
                            logger.warning("Failed to persist scenario_error: {}", ex)
                        # 持久化 AI 操作日志：场景生成失败（超过重试上限）
                        await self._log_ai_operation(
                            op_type="scenario",
                            status="failed",
                            api_ids=api_ids,
                            message=f"场景生成失败: {error_detail[:100]}",
                            error=error_detail[:500],
                            project_id=project_id,
                        )

        while True:
            try:
                result = await self._redis.blpop(AI_SCENARIO_QUEUE, timeout=10)
                if result:
                    # 队列中有消息 → 创建新协程处理（不阻塞主循环继续取消息）
                    _, task_raw = result
                    asyncio.create_task(process_one(task_raw))
                # result 为 None（超时）→ 继续循环等待
            except asyncio.CancelledError:
                # 收到取消信号 → 优雅退出循环
                logger.info("AI scenario worker cancelled")
                break
            except Exception as e:
                # blpop 异常 → 短暂休眠后重试，避免快速失败循环
                logger.error("Scenario worker loop error: {}", e)
                await asyncio.sleep(2)

    # ── P1-1: 数据模板 AI 增强 worker ─────────────────────

    async def _run_data_template_worker(self, concurrency: int = 2):
        """
        queue:data_template 消费者 —— AI 增强数据模板字段配置。
        与 analyze/scenario worker 同构：BLPOP 取任务 → 信号量并发 → infer_data_template → GenerationVersion。
        失败重试 3 次后进 DLQ。
        """
        DATA_TEMPLATE_QUEUE = "queue:data_template"
        logger.info("Data template AI worker started (concurrency={})", concurrency)
        sem = asyncio.Semaphore(concurrency)

        async def process_one(task_raw: bytes | str):
            async with sem:
                try:
                    task = json.loads(task_raw)
                    template_id = task["template_id"]
                    project_id = task.get("project_id", "default")
                    job_id = task.get("job_id") or f"data_template:{template_id}"
                    fail_count = task.get("fail_count", 0)
                except Exception as e:
                    logger.error("Bad data_template task payload: {}", e)
                    return
                try:
                    await AiJobService(self._db).mark_running(
                        job_id=job_id,
                        type="data_template",
                        project_id=project_id,
                        source="data_template_worker",
                        target_ids=[template_id],
                        queue_key=DATA_TEMPLATE_QUEUE,
                        retry_count=fail_count,
                        payload=task,
                    )
                    # 任务开始广播 running，前端可从 queued 进入可见进度。
                    await self._broadcast("ai_analysis", {
                        "type": "data_template_job",
                        "project_id": project_id,
                        "template_id": template_id,
                        "job_id": job_id,
                        "status": "running",
                    })
                    gv_id = await asyncio.wait_for(
                        self.infer_data_template(template_id, project_id, job_id=job_id),
                        timeout=600,
                    )
                    if gv_id:
                        await self._broadcast("ai_analysis", {
                            "type": "data_template_job",
                            "project_id": project_id,
                            "template_id": template_id,
                            "job_id": job_id,
                            "generation_id": gv_id,
                            "status": "pending_review",
                        })
                        logger.info("Data template {} enhanced → generation {}", template_id, gv_id)
                    else:
                        # 推断返回 None（模板不存在/LLM 空/解析失败）→ 重试
                        await self._requeue_data_template(template_id, project_id, fail_count + 1, job_id=job_id, error="AI returned no generation")
                except asyncio.TimeoutError:
                    # 数据模板分析超时 → 增加 fail_count 重试，释放信号量槽位
                    logger.error("Data template worker timed out (600s) for template_id={}", template_id)
                    await self._requeue_data_template(template_id, project_id, fail_count + 1, job_id=job_id, error="timeout")
                except (RateLimitError, APIError) as e:
                    await asyncio.sleep(30)
                    await self._requeue_data_template(template_id, project_id, fail_count, job_id=job_id, error=str(e))
                except Exception as e:
                    error_detail = self._structured_error_detail(e)
                    logger.error("Data template worker error for {}: {}", template_id, error_detail)
                    await self._requeue_data_template(template_id, project_id, fail_count + 1, job_id=job_id, error=error_detail)

        while True:
            try:
                result = await self._redis.blpop(DATA_TEMPLATE_QUEUE, timeout=10)
                if result:
                    _, task_raw = result
                    asyncio.create_task(process_one(task_raw))
            except asyncio.CancelledError:
                logger.info("Data template AI worker cancelled")
                break
            except Exception as e:
                logger.error("Data template worker loop error: {}", e)
                await asyncio.sleep(2)

    async def _run_monitor_worker(self, concurrency: int = 2):
        """queue:ai_monitor 消费者 —— 生成巡检监控配置建议并进入审核中心。"""
        logger.info("AI monitor worker started (concurrency={})", concurrency)
        sem = asyncio.Semaphore(concurrency)

        async def process_one(task_raw: bytes | str):
            async with sem:
                try:
                    task = json.loads(task_raw)
                    project_id = task.get("project_id", "default")
                    target_type = task.get("target_type", "")
                    target_ids = task.get("target_ids", [])
                    job_id = task.get("job_id") or f"monitor:{uuid.uuid4().hex[:8]}"
                    fail_count = int(task.get("fail_count", 0))
                except Exception as e:
                    logger.error("Bad monitor task payload: {}", e)
                    return
                try:
                    await AiJobService(self._db).mark_running(
                        job_id=job_id,
                        type="monitor",
                        project_id=project_id,
                        source="monitor_worker",
                        target_ids=target_ids,
                        queue_key=AI_MONITOR_QUEUE,
                        retry_count=fail_count,
                        payload=task,
                        user_id=task.get("user_id", ""),
                    )
                    await self._broadcast(f"ai_analysis:{project_id}", {
                        "type": "monitor_generation",
                        "project_id": project_id,
                        "job_id": job_id,
                        "target_ids": target_ids,
                        "status": "running",
                    })
                    gv_id = await asyncio.wait_for(
                        self.generate_monitors(
                            project_id=project_id,
                            target_type=target_type,
                            target_ids=target_ids,
                            goal=task.get("goal", ""),
                            risk_preference=task.get("risk_preference", ""),
                            schedule_preference=task.get("schedule_preference", ""),
                            job_id=job_id,
                        ),
                        timeout=600,
                    )
                    if not gv_id:
                        await self._requeue_monitor_or_dlq(task, fail_count + 1, "no monitor generation")
                        return
                    await self._broadcast(f"ai_analysis:{project_id}", {
                        "type": "monitor_generation",
                        "project_id": project_id,
                        "job_id": job_id,
                        "generation_id": gv_id,
                        "target_ids": target_ids,
                        "status": "pending_review",
                    })
                    await self._log_ai_operation(
                        op_type="monitor",
                        status="success",
                        generation_ids=[gv_id],
                        message="成功生成巡检配置建议（待审核）",
                        project_id=project_id,
                    )
                except asyncio.TimeoutError:
                    # 巡检配置生成超时 → 增加 fail_count 重试，释放信号量槽位
                    logger.error("Monitor worker task timed out (600s) for target_ids={}", target_ids)
                    await self._requeue_monitor_or_dlq(task, fail_count + 1, "timeout")
                except (RateLimitError, APIError) as e:
                    await asyncio.sleep(30)
                    await self._requeue_monitor_or_dlq(task, fail_count, str(e))
                except Exception as e:
                    error_detail = self._structured_error_detail(e)
                    logger.error("Monitor generation worker error: {}", error_detail)
                    await self._requeue_monitor_or_dlq(task, fail_count + 1, error_detail)

        while True:
            try:
                result = await self._redis.blpop(AI_MONITOR_QUEUE, timeout=10)
                if result:
                    _, task_raw = result
                    asyncio.create_task(process_one(task_raw))
            except asyncio.CancelledError:
                logger.info("AI monitor worker cancelled")
                break
            except Exception as e:
                logger.error("AI monitor worker loop error: {}", e)
                await asyncio.sleep(2)

    async def _requeue_monitor_or_dlq(self, task: dict[str, Any], fail_count: int, error: str = "") -> None:
        project_id = task.get("project_id", "default")
        job_id = task.get("job_id", "")
        task = {**task, "fail_count": fail_count}
        if fail_count < MAX_RETRY:
            await self._redis.rpush(AI_MONITOR_QUEUE, json.dumps(task, ensure_ascii=False))
            await AiJobService(self._db).mark_retry(
                job_id=job_id,
                type="monitor",
                project_id=project_id,
                source="monitor_worker",
                target_ids=task.get("target_ids", []),
                queue_key=AI_MONITOR_QUEUE,
                retry_count=fail_count,
                error=error,
                payload=task,
                user_id=task.get("user_id", ""),
            )
            await self._broadcast(f"ai_analysis:{project_id}", {
                "type": "monitor_generation",
                "project_id": project_id,
                "job_id": job_id,
                "target_ids": task.get("target_ids", []),
                "status": "retry",
                "error": error,
            })
        else:
            dlq_payload = {**task, "error": error}
            await self._redis.rpush(AI_MONITOR_DLQ, json.dumps(dlq_payload, ensure_ascii=False))
            await AiJobService(self._db).mark_dlq(
                job_id=job_id,
                type="monitor",
                project_id=project_id,
                source="monitor_worker",
                target_ids=task.get("target_ids", []),
                queue_key=AI_MONITOR_QUEUE,
                retry_count=fail_count,
                error=error or "max retry exceeded",
                payload=dlq_payload,
                user_id=task.get("user_id", ""),
            )
            await self._broadcast(f"ai_analysis:{project_id}", {
                "type": "monitor_generation",
                "project_id": project_id,
                "job_id": job_id,
                "target_ids": task.get("target_ids", []),
                "status": "dlq",
                "error": error or "max retry exceeded",
            })

    async def _requeue_data_template(self, template_id: str, project_id: str, fail_count: int, job_id: str = "", error: str = "") -> None:
        """数据模板任务重试/DLQ，与 _requeue_or_dlq 同构但载荷结构不同。"""
        DATA_TEMPLATE_QUEUE = "queue:data_template"
        DATA_TEMPLATE_DLQ = "queue:data_template:dlq"
        if fail_count < MAX_RETRY:
            payload = json.dumps({
                "template_id": template_id, "project_id": project_id,
                "job_id": job_id, "fail_count": fail_count,
            })
            await self._redis.rpush(DATA_TEMPLATE_QUEUE, payload)
            await AiJobService(self._db).mark_retry(
                job_id=job_id,
                type="data_template",
                project_id=project_id,
                source="data_template_worker",
                target_ids=[template_id],
                queue_key=DATA_TEMPLATE_QUEUE,
                retry_count=fail_count,
                error=error,
                payload=json.loads(payload),
            )
            await self._broadcast("ai_analysis", {
                "type": "data_template_job",
                "project_id": project_id,
                "template_id": template_id,
                "job_id": job_id,
                "status": "retry",
                "error": error,
            })
            logger.info("Data template {} requeued (fail_count={})", template_id, fail_count)
        else:
            payload = json.dumps({
                "template_id": template_id, "project_id": project_id,
                "job_id": job_id, "fail_count": fail_count,
                "error": error or "max retry exceeded",
            })
            await self._redis.rpush(DATA_TEMPLATE_DLQ, payload)
            await AiJobService(self._db).mark_dlq(
                job_id=job_id,
                type="data_template",
                project_id=project_id,
                source="data_template_worker",
                target_ids=[template_id],
                queue_key=DATA_TEMPLATE_QUEUE,
                retry_count=fail_count,
                error=error or "max retry exceeded",
                payload=json.loads(payload),
            )
            await self._broadcast("ai_analysis", {
                "type": "data_template_job",
                "project_id": project_id,
                "template_id": template_id,
                "job_id": job_id,
                "status": "dlq",
                "error": error or "max retry exceeded",
            })
            logger.error("Data template {} moved to DLQ after {} retries", template_id, fail_count)

    async def _requeue_or_dlq(self, api_id: str, fail_count: int, target_queue: str | None = None, job_id: str = "", project_id: str = "default", error: str = "") -> None:
        """重试或移入死信队列；target_queue 指定重试入队的目标队列（默认 AI_ANALYZE_QUEUE）"""
        if target_queue is None:
            target_queue = AI_ANALYZE_QUEUE
        if fail_count < MAX_RETRY:
            # 未超过重试上限 → 重新入队等待下一次处理
            payload = json.dumps({
                "api_id": api_id,
                "project_id": project_id,
                "fail_count": fail_count,
                "target_queue": target_queue,
                "job_id": job_id or f"api:{api_id}",
                "status": "retry",
                "ts": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None).isoformat(),
            })
            await self._redis.rpush(target_queue, payload)
            await AiJobService(self._db).mark_retry(
                job_id=job_id or f"api:{api_id}",
                type={"queue:ai_analyze_doc": "doc", "queue:ai_analyze_asserts": "asserts"}.get(target_queue, "ai_analyze"),
                project_id=project_id,
                source="analyzer_worker",
                target_ids=[api_id],
                queue_key=target_queue,
                retry_count=fail_count,
                error=error,
                payload=json.loads(payload),
            )
            logger.warning("Requeued api_id={} to {} (fail_count={})", api_id, target_queue, fail_count)
        else:
            # 超过最大重试次数 → 移入死信队列 + 标记 FAILED 状态（需人工介入恢复）
            payload = json.dumps({
                "api_id": api_id,
                "project_id": project_id,
                "fail_count": fail_count,
                "target_queue": target_queue,
                "job_id": job_id or f"api:{api_id}",
                "status": "dlq",
                "error": error or f"Exceeded {MAX_RETRY} retries",
                "ts": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None).isoformat(),
            })
            await self._redis.rpush(AI_DLQ, payload)
            await AiJobService(self._db).mark_dlq(
                job_id=job_id or f"api:{api_id}",
                type={"queue:ai_analyze_doc": "doc", "queue:ai_analyze_asserts": "asserts"}.get(target_queue, "ai_analyze"),
                project_id=project_id,
                source="analyzer_worker",
                target_ids=[api_id],
                queue_key=target_queue,
                retry_count=fail_count,
                error=error or f"Exceeded {MAX_RETRY} retries",
                payload=json.loads(payload),
            )
            await self._set_status(api_id, AnalysisStatus.FAILED, f"Exceeded {MAX_RETRY} retries")
            logger.error("api_id={} moved to DLQ after {} failures", api_id, fail_count)

    async def _requeue_scenario_or_dlq(self, api_ids: list[str], project_id: str, fail_count: int, scenario_type: str = "", job_id: str = "", user_id: str = "", error: str = "") -> None:
        """场景生成任务的重试/DLQ 逻辑，保留 scenario_type 以便重试时沿用原策略"""
        if fail_count < MAX_RETRY:
            # 未超过重试上限 → 重新入队，保留 scenario_type 确保重试时沿用相同生成策略
            payload = json.dumps({
                "api_ids": api_ids,
                "project_id": project_id,
                "fail_count": fail_count,
                "scenario_type": scenario_type,
                "job_id": job_id,
                "user_id": user_id,
                "error": error,
                "ts": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None).isoformat(),
            })
            await self._redis.rpush(AI_SCENARIO_QUEUE, payload)
            await AiJobService(self._db).mark_retry(
                job_id=job_id,
                type="scenario",
                project_id=project_id,
                source="scenario_worker",
                target_ids=api_ids,
                queue_key=AI_SCENARIO_QUEUE,
                retry_count=fail_count,
                error=error,
                payload=json.loads(payload),
                user_id=user_id,
            )
            logger.warning("Scenario requeued api_ids={} (fail_count={})", api_ids, fail_count)
        else:
            # 超过最大重试次数 → 移入场景死信队列（需人工介入恢复）
            payload = json.dumps({
                "api_ids": api_ids,
                "project_id": project_id,
                "fail_count": fail_count,
                "scenario_type": scenario_type,
                "job_id": job_id,
                "user_id": user_id,
                "error": error or f"Exceeded {MAX_RETRY} retries",
                "ts": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None).isoformat(),
            })
            await self._redis.rpush(AI_SCENARIO_DLQ, payload)
            await AiJobService(self._db).mark_dlq(
                job_id=job_id,
                type="scenario",
                project_id=project_id,
                source="scenario_worker",
                target_ids=api_ids,
                queue_key=AI_SCENARIO_QUEUE,
                retry_count=fail_count,
                error=error or f"Exceeded {MAX_RETRY} retries",
                payload=json.loads(payload),
                user_id=user_id,
            )
            logger.error("Scenario api_ids={} moved to DLQ after {} failures", api_ids, fail_count)

    # ── AI 操作日志持久化辅助 ─────────────────────────

    async def _log_ai_operation(
        self,
        op_type: str,
        status: str,
        api_ids: list[str] | None = None,
        scenario_ids: list[str] | None = None,
        generation_ids: list[str] | None = None,
        message: str = "",
        error: str = "",
        project_id: str = "default",
    ) -> None:
        """
        持久化 AI 操作日志到 ai_operation_logs 集合。
        失败时仅记录 warning，不阻塞主流程。
        """
        try:
            from services.ai_operation_log_service import AiOperationLogService
            from models.ai_operation_log import AiOperationType, AiOperationStatus
            svc = AiOperationLogService(self._api_col.database)
            # Phase 1: generation_ids 通过 extra 字段存储（不影响现有 schema）
            extra = None
            if generation_ids:
                extra = {"generation_ids": generation_ids}
            await svc.log_operation(
                type=AiOperationType(op_type),
                status=AiOperationStatus(status),
                api_ids=api_ids or [],
                scenario_ids=scenario_ids or [],
                message=message,
                error=error,
                extra=extra,
                project_id=project_id,
            )
        except Exception as e:
            logger.warning("Failed to write AI operation log: {}", e)

    # ── WebSocket 状态广播辅助 ─────────────────────────

    async def _broadcast(self, key: str, data: dict) -> None:
        """安全广播，无 ws_manager 时静默跳过。
        P0-4: 用 getattr 容错，避免测试环境或未注入 _ws 的实例 AttributeError。"""
        if key == "ai_analysis" or key.startswith("ai_analysis:"):
            # 统一 AI 事件语义：前端按 project_id/job_id/type/status 追踪所有 AI 任务。
            data.setdefault("project_id", "default")
            data.setdefault("job_id", data.get("api_id") or data.get("generation_id") or "")
            data.setdefault("type", "ai_event")
            data.setdefault("status", "running")
        ws = getattr(self, "_ws", None)
        if ws is not None:
            try:
                if key == "ai_analysis" and data.get("project_id"):
                    key = f"ai_analysis:{data['project_id']}"
                await ws.broadcast(key, data)
            except Exception as e:
                logger.warning("WS broadcast failed: {}", e)

    async def _set_status(self, api_id: str, status: AnalysisStatus, error: str = "") -> None:
        """
        更新 api_dsls 的 analysis_status 并通过 WebSocket 广播状态变更。
        前端通过 /ws/ai-analysis 订阅，实时收到 {type: "status", api_id, status, error} 事件。
        """
        update = {"analysis_status": status}
        if error:
            update["analysis_error"] = error
        await self._api_col.update_one({"id": api_id}, {"$set": update})
        doc = await self._api_col.find_one({"id": api_id}, {"project_id": 1})
        await self._broadcast("ai_analysis", {
            "type": "status",
            "api_id": api_id,
            "status": status.value,
            "error": error,
            "project_id": (doc or {}).get("project_id", "default"),
        })

    # ── DLQ 管理接口（供 routes.py 调用） ─────────────────

    async def list_dlq(self) -> list[dict]:
        """列出死信队列中的所有任务"""
        items = await self._redis.lrange(AI_DLQ, 0, -1)
        return [json.loads(item) for item in items]

    async def retry_dlq(self, index: int) -> bool:
        """将 DLQ 中指定索引的任务重新入队到主队列"""
        items = await self._redis.lrange(AI_DLQ, 0, -1)
        if index < 0 or index >= len(items):
            # 索引越界 → 返回 False 表示操作失败
            return False
        item = items[index]
        # 用占位符标记删除（Redis list 不支持按索引直接删除元素）
        placeholder = "__DELETED__"
        await self._redis.lset(AI_DLQ, index, placeholder)
        await self._redis.lrem(AI_DLQ, 1, placeholder)
        # 重置 fail_count 重新入队，给任务一次全新的重试机会
        task = json.loads(item)
        task["fail_count"] = 0
        await self._redis.rpush(AI_ANALYZE_QUEUE, json.dumps(task))
        await self._set_status(task["api_id"], AnalysisStatus.QUEUED)
        logger.info("DLQ retry: api_id={}", task["api_id"])
        return True

    async def remove_dlq(self, index: int) -> bool:
        """从 DLQ 中删除指定索引的任务"""
        items = await self._redis.lrange(AI_DLQ, 0, -1)
        if index < 0 or index >= len(items):
            # 索引越界 → 返回 False
            return False
        placeholder = "__DELETED__"
        await self._redis.lset(AI_DLQ, index, placeholder)
        await self._redis.lrem(AI_DLQ, 1, placeholder)
        logger.info("DLQ removed: index={}", index)
        return True

    # ── 场景 DLQ 管理接口 ─────────────────────────────

    async def list_scenario_dlq(self) -> list[dict]:
        """列出场景死信队列中的所有任务"""
        items = await self._redis.lrange(AI_SCENARIO_DLQ, 0, -1)
        return [json.loads(item) for item in items]

    async def retry_scenario_dlq(self, index: int) -> bool:
        """将场景 DLQ 中指定索引的任务重新入队到主队列"""
        items = await self._redis.lrange(AI_SCENARIO_DLQ, 0, -1)
        if index < 0 or index >= len(items):
            return False
        item = items[index]
        placeholder = "__DELETED__"
        await self._redis.lset(AI_SCENARIO_DLQ, index, placeholder)
        await self._redis.lrem(AI_SCENARIO_DLQ, 1, placeholder)
        # 重置 fail_count 重新入队
        task = json.loads(item)
        task["fail_count"] = 0
        await self._redis.rpush(AI_SCENARIO_QUEUE, json.dumps(task))
        logger.info("Scenario DLQ retry: api_ids={}", task.get("api_ids"))
        return True

    async def remove_scenario_dlq(self, index: int) -> bool:
        """从场景 DLQ 中删除指定索引的任务"""
        items = await self._redis.lrange(AI_SCENARIO_DLQ, 0, -1)
        if index < 0 or index >= len(items):
            return False
        placeholder = "__DELETED__"
        await self._redis.lset(AI_SCENARIO_DLQ, index, placeholder)
        await self._redis.lrem(AI_SCENARIO_DLQ, 1, placeholder)
        logger.info("Scenario DLQ removed: index={}", index)
        return True

    # ── 运行时刷新 LLM 客户端（从 DB 读取配置） ─────────

    async def refresh_client(self) -> None:
        """
        从 MongoDB settings 集合读取 llm_config，重建 AsyncOpenAI 客户端。
        若 DB 中无配置则回退到 .env 设置，支持运行时切换 API key / base_url / model。
        """
        self._runtime_llm_config = await self._db["settings"].find_one({"key": "llm_config"}) or {}
        runtime = resolve_llm_config(self._runtime_llm_config)
        self._client = runtime.build_client()
        self._model = runtime.model
        self._temperature = runtime.temperature
        self._max_tokens = runtime.max_tokens
        logger.info(
            "LLM client refreshed: model={} temp={} max_tokens={}",
            self._model, self._temperature, self._max_tokens,
        )

    # ── 用户反馈 ─────────────────────────────────────────

    async def _record_user_feedback(
        self, api_id: str,
        edit_field: str, old_value: Any, new_value: Any,
    ) -> bool:
        """
        将用户手动编辑 AI 生成内容的行为记录为隐式反馈。
        - 写入 ai_feedback 集合供知识系统后续分析
        - 当知识服务可用时，降低相关记忆条目的置信度（小幅）
        返回 True 表示反馈记录成功。
        """
        if not old_value or not new_value:
            # 空值不记录（无意义的反馈，无法从中学习）
            return False
        # 值未变化时不记录（用户未实际修改）
        if old_value == new_value:
            return False

        # 写入 ai_feedback 集合（隐式反馈记录）
        feedback_doc = {
            "api_id": api_id,
            "edit_field": edit_field,       # 如 "doc.params", "doc.response_fields", "asserts"
            "old_value": str(old_value)[:500],   # 截断以防过长
            "new_value": str(new_value)[:500],
            "source": "implicit",            # 隐式反馈（用户手动编辑触发）
            "created_at": datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None),
        }
        try:
            await self._db["ai_feedback"].insert_one(feedback_doc)
            logger.info("Implicit feedback recorded for api={} field={}", api_id, edit_field)
        except Exception as e:
            # 写入失败不阻塞主流程（反馈记录是辅助功能）
            logger.warning("Failed to record implicit feedback for api={}: {}", api_id, e)
            return False

        # 当知识服务可用时，通过隐式反馈小幅调整相关记忆置信度
        if self._knowledge is not None:
            try:
                # 检索与该 API 相关的知识条目（通过 source_api_ids）
                related = await self._db["knowledge_entries"].find(
                    {"source_api_ids": api_id}
                ).to_list(length=20)
                for entry in related:
                    await self._knowledge.submit_feedback(
                        entry["id"], "implicit",
                        meta={
                            "api_id": api_id,
                            "edit_field": edit_field,
                            "old_preview": str(old_value)[:100],
                            "new_preview": str(new_value)[:100],
                        },
                    )
            except Exception as e:
                # 知识更新失败不阻塞反馈记录主流程
                logger.warning("Failed to update knowledge for implicit feedback: {}", e)
        # else: knowledge 为 None → 跳过知识更新

        return True

    # ── LLM 工具 ─────────────────────────────────────────

    # 每个 token 约对应 3-4 个字符（中文约 1.5-2 字符），保守估计取 3
    _CHARS_PER_TOKEN = 3
    # prompt 总 token 预算上限：超过时裁剪 api_json 内容，避免超出模型上下文窗口
    _MAX_PROMPT_TOKENS = 12000

    async def _call_llm(
        self, user_prompt: str, system_prompt: str | None = None,
        max_tokens: int | None = None,
        retries: int = 3,
        task_type: str = "",
    ) -> str:
        """
        调用 LLM，支持 system role 消息、可配置 max_tokens、指数退避重试。
        - max_tokens: 覆盖默认值，用于不同提示类型设置不同输出长度
        - retries: 最大重试次数，指数退避 1s→2s→4s
        """
        # 从 settings 读取超时配置，运行时可通过 refresh_client 更新
        s = get_settings()
        timeout = s.openai_timeout

        messages = []
        if system_prompt:
            # 有 system_prompt 时作为首条消息注入，指导 LLM 行为模式
            messages.append({"role": "system", "content": system_prompt})
        # 估算总 token，超过预算时裁剪 user_prompt（保留首部结构 + 尾部关键数据）
        estimated_tokens = self._estimate_tokens(system_prompt or "") + self._estimate_tokens(user_prompt)
        if estimated_tokens > self._MAX_PROMPT_TOKENS:
            # Token 估算超限 → 截断 user_prompt（system_prompt 保持完整）
            logger.warning(
                "Prompt tokens estimated {} exceeds budget {}, truncating user_prompt",
                estimated_tokens, self._MAX_PROMPT_TOKENS,
            )
            user_prompt = self._truncate_prompt(user_prompt, self._MAX_PROMPT_TOKENS - self._estimate_tokens(system_prompt or ""))
        messages.append({"role": "user", "content": user_prompt})

        runtime = resolve_llm_config(getattr(self, "_runtime_llm_config", {}), task_type)
        base_client = getattr(self, "_client", None)
        client = runtime.build_client() if task_type and base_client is not None else base_client
        model = runtime.model if task_type else self._model
        temperature = runtime.temperature if task_type else self._temperature
        effective_max_tokens = runtime.max_tokens if task_type else (max_tokens if max_tokens is not None else self._max_tokens)
        last_error = None

        for attempt in range(retries):
            try:
                resp = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=effective_max_tokens,
                    timeout=timeout,
                )
                # 防御：LLM 可能返回空 choices 数组，避免 IndexError
                if not resp.choices:
                    logger.warning("LLM returned empty choices (attempt {}/{})", attempt + 1, retries)
                    continue  # 视为可重试的临时问题，进入下一次循环
                content = resp.choices[0].message.content or ""
                # 记录实际 token 用量，便于监控成本
                if resp.usage:
                    logger.debug("LLM usage: prompt={} completion={} total={}",
                                 resp.usage.prompt_tokens, resp.usage.completion_tokens, resp.usage.total_tokens)
                return content
            except (RateLimitError, APIError) as e:
                # 可重试错误（限流/服务端错误）→ 指数退避 1s→2s→4s
                last_error = e
                if attempt < retries - 1:
                    wait = 2 ** attempt  # 1s, 2s, 4s
                    logger.warning("LLM call failed (attempt {}/{}), retrying in {}s: {}", attempt + 1, retries, wait, e)
                    await asyncio.sleep(wait)
                else:
                    # 最后一次重试也失败 → 抛出异常，由上层 worker 处理
                    logger.error("LLM call exhausted {} retries: {}", retries, e)
                    raise
            except Exception:
                # 非 OpenAI 错误（如网络超时、连接拒绝）→ 不重试，直接抛出
                raise
        # 所有重试均返回空 choices（未抛出异常但无有效内容）→ 抛出最后一个错误或空字符串
        if last_error:
            raise last_error
        return ""

    async def _call_llm_stream(
        self,
        user_prompt: str,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        job_id: str = "",
        task_type: str = "",
    ) -> str:
        """
        P0-4: 流式调用 LLM，逐 chunk 通过 WS 广播 ai_chunk 事件，前端打字机效果展示。
        解决问题：此前 _call_llm 全程非流式，长任务（场景生成 4096 tokens）用户只能盯着
        running 标签干等，体验差。

        实现要点：
        1. stream=True 让 OpenAI SDK 返回 async iterator
        2. 累积所有 chunk 拼成完整 content（与 _call_llm 返回值语义一致，便于上层复用解析逻辑）
        3. 每个 chunk 广播 {type:"ai_chunk", job_id, delta, done:false}，
           结束广播 {done:true, full_content_length}
        4. 重试逻辑简化：流式场景下重试会导致前端已渲染内容需回滚，故仅尝试 1 次，
           失败则回退到非流式 _call_llm（带重试）保证可靠性
        5. job_id 关联具体任务（api_id/scenario_id），前端按 job_id 过滤只渲染当前关注任务
        """
        s = get_settings()
        timeout = s.openai_timeout

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        # token 估算截断（与非流式保持一致）
        estimated_tokens = self._estimate_tokens(system_prompt or "") + self._estimate_tokens(user_prompt)
        if estimated_tokens > self._MAX_PROMPT_TOKENS:
            user_prompt = self._truncate_prompt(
                user_prompt, self._MAX_PROMPT_TOKENS - self._estimate_tokens(system_prompt or "")
            )
        messages.append({"role": "user", "content": user_prompt})

        runtime = resolve_llm_config(getattr(self, "_runtime_llm_config", {}), task_type)
        base_client = getattr(self, "_client", None)
        client = runtime.build_client() if task_type and base_client is not None else base_client
        model = runtime.model if task_type else self._model
        temperature = runtime.temperature if task_type else self._temperature
        effective_max_tokens = runtime.max_tokens if task_type else (max_tokens if max_tokens is not None else self._max_tokens)
        full_content_parts: list[str] = []

        # P0-4: 测试环境或未注入 _client 的实例（如 __new__ 构造）无 HTTP 能力，
        # 直接回退到 _call_llm（通常已被 mock），避免 AttributeError 并保持测试隔离。
        if client is None:
            return await self._call_llm(
                user_prompt, system_prompt, max_tokens=max_tokens, retries=3, task_type=task_type
            )

        try:
            # stream=True 返回 async iterator，逐 chunk 消费
            stream = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=effective_max_tokens,
                timeout=timeout,
                stream=True,
            )
            async for chunk in stream:
                # 防御：部分 chunk 可能无 choices（如首尾的 role/done 标记）
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                # content 可能为 None（非内容 chunk，如 tool_calls）
                if delta and delta.content:
                    full_content_parts.append(delta.content)
                    # 广播增量给前端（job_id 让前端过滤只渲染当前任务）
                    await self._broadcast("ai_analysis", {
                        "type": "ai_chunk",
                        "job_id": job_id,
                        "task_type": task_type,
                        "delta": delta.content,
                        "done": False,
                    })
        except (RateLimitError, APIError) as e:
            # 流式首次失败 → 回退到非流式 _call_llm（带重试，保证可靠性）
            # 已广播的 chunk 前端会保留，回退后用 done 事件覆盖最终结果
            logger.warning(
                "Stream LLM failed for job {}, fallback to non-stream: {}", job_id, e
            )
            return await self._call_llm(
                user_prompt, system_prompt, max_tokens=max_tokens, retries=3, task_type=task_type
            )

        full_content = "".join(full_content_parts)
        # 广播完成事件，前端据此结束打字机动画并标记生成完成
        await self._broadcast("ai_analysis", {
            "type": "ai_chunk",
            "job_id": job_id,
            "task_type": task_type,
            "delta": "",
            "done": True,
            "full_content_length": len(full_content),
        })
        return full_content

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """粗略估算 token 数：按字符数 ÷ 3（兼容中英文混合）"""
        return max(1, len(text) // 3)

    @staticmethod
    def _truncate_prompt(prompt: str, max_tokens: int) -> str:
        """
        裁剪过长的 prompt：保留完整模板结构 + 截断 JSON 数据部分。
        策略：在 "api_json" 标记处将 prompt 分为模板头和 JSON 数据，保留 JSON 前部。
        max_tokens 为允许的最大 token 数。
        """
        max_chars = max_tokens * 3
        if len(prompt) <= max_chars:
            # 未超限 → 原样返回
            return prompt
        # 查找 JSON 数据起始位置（各种可能的标记），优先按标记分割以保留模板头
        json_markers = ['"api_json":', '"apis_json":', '以下接口', '```', '{']
        for marker in json_markers:
            idx = prompt.find(marker)
            if idx > 0:
                # 找到了标记 → 保留标记前的模板头 + 截断后的 JSON 数据
                header = prompt[:idx + len(marker)]
                data_part = prompt[idx + len(marker):]
                data_budget = max_chars - len(header) - 100  # 预留100字符给截断提示
                if data_budget > 0:
                    # 预算足够保留部分数据 → 拼接返回
                    return header + data_part[:data_budget] + "\n...(truncated)"
                # data_budget <= 0 → 标记载入过长，继续尝试下一个标记
        # 无明确标记时直接截断尾部（简单截断，保留前部内容）
        logger.warning("Prompt truncated: {} chars → {} chars", len(prompt), max_chars)
        return prompt[:max_chars]

    @staticmethod
    def _build_system_prompt(base_prompt: str, memory_context: str) -> str:
        """
        将记忆上下文注入 system prompt。
        无记忆上下文时原样返回 base_prompt，避免不必要的 token 消耗。
        """
        if not memory_context:
            # 无记忆 → 原样返回，不浪费 token
            return base_prompt
        # 有记忆 → 拼接到 system prompt 末尾，指导 LLM 保持风格一致
        return base_prompt + f"\n\n## 项目记忆（来自已分析接口）\n{memory_context}\n\n请参考以上模式，保持风格一致。不确定时遵循项目记忆。"

    # P1-6: task_type → 代码默认 prompt 映射（DB 无记录时兜底，保证可用性）
    # 与 models/prompt_template.py 的 PROMPT_TASK_TYPES 一一对应
    _DEFAULT_PROMPTS: dict[str, str] = {}  # 类加载时填充（见类末尾）

    # P1-6: prompt 内存缓存（task_type → content），避免每次分析查 DB。
    # 缓存有效期 _PROMPT_CACHE_TTL 秒，过期或 invalidate 后重新查 DB。
    _PROMPT_CACHE: dict[str, tuple[str, float]] = {}
    _PROMPT_CACHE_TTL: float = 60.0  # 60 秒缓存，平衡时效性与 DB 压力

    async def _get_prompt(self, task_type: str) -> str:
        """
        P1-6: 按 task_type 获取 system prompt，优先 DB 激活版本，回退代码默认值。
        带内存缓存（60s TTL），避免每次分析都查 DB。
        DB 不可用或无记录时回退到代码内 _DEFAULT_PROMPTS，保证服务可用性。
        """
        import time
        now = time.time()
        cached = self._PROMPT_CACHE.get(task_type)
        if cached and (now - cached[1]) < self._PROMPT_CACHE_TTL:
            # 缓存命中且未过期 → 直接返回
            return cached[0]
        # 缓存未命中或过期 → 查 DB
        try:
            doc = await self._db["prompt_templates"].find_one(
                {"task_type": task_type, "active": True}
            )
            if isinstance(doc, dict) and doc.get("content"):
                content = doc["content"]
                self._PROMPT_CACHE[task_type] = (content, now)
                return content
        except Exception as e:
            # DB 查询失败 → 记录警告，回退默认值（不阻断分析流程）
            logger.debug("Prompt DB lookup failed for {}, fallback to default: {}", task_type, e)
        # DB 无记录或查询失败 → 回退代码默认 prompt
        default = self._DEFAULT_PROMPTS.get(task_type, "")
        self._PROMPT_CACHE[task_type] = (default, now)
        return default

    @classmethod
    def invalidate_prompt_cache(cls, task_type: str = "") -> None:
        """P1-6: 失效 prompt 缓存。prompt 更新后调用，确保下次分析读到新版本。
        task_type 为空时清空全部缓存。"""
        if task_type:
            cls._PROMPT_CACHE.pop(task_type, None)
        else:
            cls._PROMPT_CACHE.clear()

    @staticmethod
    def _safe_parse_json(text: str) -> Any:
        # 委托给共享工具函数，处理 LLM 非标准 JSON（trailing commas、Python literals 等）
        from ai_analyzer.utils import safe_fire_and_forget, safe_parse_json
        return safe_parse_json(text)

    # 字段名非法字符（仅允许字母、数字、下划线、点号、中划线、$、[]）
    _FIELD_NAME_RE = re.compile(r'^[\w.\-\[\]$]+$')

    @staticmethod
    def _validate_doc_result(data: dict) -> list[str]:
        """
        验证文档解析结果的结构完整性，返回警告列表。
        增加字段名合法性检查（特殊字符、空值、非法格式）。
        不阻塞流程，仅记录以便调试 LLM 输出质量。
        """
        warnings = []
        if not isinstance(data, dict):
            # 非 dict 类型 → 无法验证结构，直接返回错误
            return ["doc result is not a dict"]
        if not data.get("summary"):
            warnings.append("doc missing 'summary'")
        if "params" not in data:
            warnings.append("doc missing 'params'")
        elif not isinstance(data["params"], list):
            # params 存在但类型错误 → 记录警告，跳过逐项检查
            warnings.append("doc 'params' is not a list")
        else:
            # params 是合法列表 → 逐项检查每个元素的字段名合法性
            for i, p in enumerate(data["params"]):
                if not isinstance(p, dict):
                    warnings.append(f"params[{i}] is not a dict")
                    continue  # 非 dict → 跳过字段名检查
                name = p.get("name", "")
                # 检查字段名为空或包含非法字符（防止特殊字符导致后续处理异常）
                if not name or not isinstance(name, str):
                    warnings.append(f"params[{i}] has empty/invalid name: {name!r}")
                elif not AiAnalyzerService._FIELD_NAME_RE.match(name):
                    warnings.append(f"params[{i}] name contains illegal chars: {name!r}")
        if "response_fields" not in data:
            warnings.append("doc missing 'response_fields'")
        elif not isinstance(data["response_fields"], list):
            warnings.append("doc 'response_fields' is not a list")
        else:
            # response_fields 是合法列表 → 逐项检查
            for i, f in enumerate(data["response_fields"]):
                if not isinstance(f, dict):
                    warnings.append(f"response_fields[{i}] is not a dict")
                    continue  # 非 dict → 跳过字段名检查
                name = f.get("name", "")
                if not name or not isinstance(name, str):
                    warnings.append(f"response_fields[{i}] has empty/invalid name: {name!r}")
                elif not AiAnalyzerService._FIELD_NAME_RE.match(name):
                    warnings.append(f"response_fields[{i}] name contains illegal chars: {name!r}")
        return warnings

    # 合法操作符集合（与 models/dsl.py AssertRule 保持同步，共 22 种）
    _VALID_OPERATORS: set[str] = {
        "eq", "ne", "gt", "gte", "lt", "lte",
        "contains", "not_contains", "starts_with", "ends_with", "regex",
        "exists", "not_exists", "empty", "not_empty",
        "in", "not_in",
        "type_match",
        "length",
        "json_schema",
        "header_eq", "header_contains",
        "response_time_lt",
    }

    @staticmethod
    def _validate_assert_result(data: Any) -> list[str]:
        """
        验证断言解析结果的结构完整性，返回警告列表。
        增加 operator 合法性验证 + 操作符/字段组合合理性检查。
        """
        warnings = []
        if not isinstance(data, list):
            # 非 list 类型 → 无法验证断言列表结构，直接返回错误
            return ["assert result is not a list"]
        if len(data) == 0:
            # 空列表：没有断言规则，记录警告但不阻塞（可能 API 确实无可测内容）
            warnings.append("assert result is empty")
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                # 元素非 dict → 跳过逐项检查（无法获取 field/operator 字段）
                warnings.append(f"assert[{i}] is not a dict")
                continue
            field = item.get("field", "")
            operator = item.get("operator", "")
            if not field:
                # 缺少 field：断言无法定位目标字段，LLM 输出不完整
                warnings.append(f"assert[{i}] missing 'field'")
            if not operator:
                # 缺少 operator：断言无法确定比较方式，LLM 输出不完整
                warnings.append(f"assert[{i}] missing 'operator'")
            else:
                # operator 存在 → 进行合法性及字段/操作符组合校验
                if operator not in AiAnalyzerService._VALID_OPERATORS:
                    # 非法操作符：LLM 生成了不在预定义集合中的操作符（如 equls→应为eq）
                    warnings.append(f"assert[{i}] has unknown operator: {operator!r}")
                # status_code 是 HTTP 状态码数值字段，应使用 eq/ne/exist 比较（不适合 contains/regex 等）
                if field == "status_code" and operator not in ("eq", "ne", "exists"):
                    warnings.append(f"assert[{i}] status_code with operator={operator!r}, expected eq/ne")
                # $response_time_ms 是响应耗时（毫秒），只能用 response_time_lt 做阈值比较
                if field == "$response_time_ms" and operator != "response_time_lt":
                    warnings.append(f"assert[{i}] $response_time_ms should use response_time_lt, got {operator!r}")
                # type_match 要求 expected 为合法 JSON 类型名（int/float/str/bool/list/dict/null）
                if operator == "type_match":
                    valid_types = {"int", "float", "str", "bool", "list", "dict", "null"}
                    expected = item.get("expected")
                    if expected and expected not in valid_types:
                        warnings.append(f"assert[{i}] type_match expected={expected!r}, should be one of {valid_types}")
                # in/not_in 要求 expected 必须是列表（检查值是否在集合中）
                if operator in ("in", "not_in") and not isinstance(item.get("expected"), list):
                    warnings.append(f"assert[{i}] {operator} expected should be a list, got {type(item.get('expected')).__name__}")
        return warnings

    @staticmethod
    def _validate_scenario_result(data: Any) -> list[str]:
        """
        验证场景生成结果的结构完整性，返回警告列表。
        检查项：基本结构、step_id/api_id、depends_on 引用、condition/loop 字段、assertions 格式
        """
        warnings = []
        if not isinstance(data, (list, dict)):
            # 非 list/dict → 无法验证场景结构，直接返回错误
            return ["scenario result is not list/dict"]
        # LLM 可能对单 API 场景返回 dict 而非 list → 统一包装为列表迭代
        items = [data] if isinstance(data, dict) else data
        if len(items) == 0:
            # 空列表：无场景数据，记录警告（可能 API 无需场景测试）
            warnings.append("scenario result is empty")
        for i, s in enumerate(items):
            if not isinstance(s, dict):
                # 元素非 dict → 跳过逐项检查（无法获取 name/steps 字段）
                warnings.append(f"scenario[{i}] is not a dict")
                continue
            if not s.get("name"):
                # 缺少场景名称 → 场景无法在前端展示标识
                warnings.append(f"scenario[{i}] missing 'name'")
            steps = s.get("steps", [])
            if not isinstance(steps, list) or len(steps) == 0:
                # steps 缺失或为空：场景没有执行步骤，无法被 DAG 引擎调度
                warnings.append(f"scenario[{i}] missing 'steps'")
                continue  # 跳过后续检查（无步骤数据无法进一步验证）
            # 收集当前场景所有有效的 step_id，用于 depends_on 引用完整性检查
            step_ids_in_scene = set()
            for step in steps:
                if isinstance(step, dict) and step.get("step_id"):
                    step_ids_in_scene.add(step["step_id"])
            for j, step in enumerate(steps):
                if not isinstance(step, dict):
                    # 步骤元素非 dict → 跳过逐项检查
                    warnings.append(f"scenario[{i}].steps[{j}] is not a dict")
                    continue
                if not step.get("step_id"):
                    # 缺少 step_id：DAG 引擎无法引用该步骤
                    warnings.append(f"scenario[{i}].steps[{j}] missing 'step_id'")
                if not step.get("api_id"):
                    # 缺少 api_id：步骤无法关联到具体 API
                    warnings.append(f"scenario[{i}].steps[{j}] missing 'api_id'")
                # 验证 depends_on 引用完整性：引用的 step_id 必须存在于同一场景中
                deps = step.get("depends_on", [])
                if isinstance(deps, list):
                    for dep in deps:
                        if isinstance(dep, str) and dep not in step_ids_in_scene:
                            warnings.append(
                                f"scenario[{i}].steps[{j}] depends_on references unknown step_id '{dep}'"
                            )
                # 验证 condition 字段结构
                cond = step.get("condition")
                if cond is not None:
                    if not isinstance(cond, dict):
                        warnings.append(f"scenario[{i}].steps[{j}] 'condition' must be dict")
                    elif "variable" not in cond:
                        warnings.append(f"scenario[{i}].steps[{j}] condition missing 'variable'")
                # 验证 loop_var 与 loop_count 互斥
                has_loop_var = bool(step.get("loop_var"))
                has_loop_count = step.get("loop_count") is not None
                if has_loop_var and has_loop_count:
                    warnings.append(
                        f"scenario[{i}].steps[{j}] both loop_var and loop_count set (mutually exclusive)"
                    )
                # 验证 assertions 格式
                assertions = step.get("assertions", [])
                if isinstance(assertions, list):
                    for ai, a in enumerate(assertions):
                        if isinstance(a, dict):
                            if not a.get("path"):
                                warnings.append(
                                    f"scenario[{i}].steps[{j}].assertions[{ai}] missing 'path'"
                                )
        return warnings


# P1-6: 模块加载时填充默认 prompt 映射（放在文件末尾，确保 _DOC_SYSTEM 等常量已定义）
_init_default_prompts()
