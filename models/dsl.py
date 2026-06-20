"""
DSL 数据模型 v2 —— 平台所有模块围绕这套 schema 运转。
v2 新增：
- ScenarioStep: condition / loop / loop_count
- MonitorDSL: risk_level / alert_on_recovery / max_failures
- FieldTemplate: nested_template / null_rate / invalid_rate
- ExecutionRecord: project_id / duration_ms
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── 基础枚举 ──────────────────────────────────────────────

class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"
    CONNECT = "CONNECT"
    TRACE = "TRACE"


class ParseStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


# 将 bool ai_analyzed 替换为枚举，支持前端实时展示分析进度
class AnalysisStatus(str, Enum):
    IDLE = "idle"
    QUEUED = "queued"
    RUNNING = "running"
    PENDING_REVIEW = "pending_review"
    APPLIED = "applied"
    DONE = "done"  # 旧数据兼容：读取时仍可反序列化，新增写入使用 applied/pending_review
    FAILED = "failed"


class ScenarioStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class ScenarioStepType(str, Enum):
    START = "start"
    END = "end"
    API = "api"
    CONDITION = "condition"
    LOOP = "loop"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class BodyType(str, Enum):
    JSON = "json"
    FORM = "form"
    TEXT = "text"
    NONE = "none"
    MULTIPART = "multipart"
    XML = "xml"


# ── 请求/响应结构 ─────────────────────────────────────────

class RequestDSL(BaseModel):
    method: HttpMethod
    url: str
    path: str = ""
    query_params: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any = None
    body_type: str = BodyType.NONE


class ResponseDSL(BaseModel):
    status_code: int
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any = None
    latency_ms: int = 0


# ── 断言规则 ──────────────────────────────────────────────

class AssertRule(BaseModel):
    model_config = ConfigDict(extra="allow")

    field: str
    # 操作符（v4 扩展至 22 种）：
    #   比较: eq / ne / gt / gte / lt / lte
    #   字符串: contains / not_contains / starts_with / ends_with / regex
    #   存在性: exists / not_exists / empty / not_empty
    #   集合: in / not_in
    #   类型: type_match (int/float/str/bool/list/dict/null)
    #   结构: length (数组/字符串长度), json_schema (JSON Schema 校验)
    #   响应头: header_eq / header_contains
    #   性能: response_time_lt (响应时间 ms)
    # 特殊字段: $response_time_ms 自动对延迟断言
    operator: str
    expected: Any = None
    description: str = ""
    risk_level: RiskLevel = RiskLevel.MEDIUM  # 断言失败时的风险等级


# ── 断言操作符单一来源（前后端对齐依据） ───────────────────
# P0-2：抽取为常量供 GET /apis/assert-operators 接口下发，前端动态渲染控件，
# 彻底消除「前端硬编码 15 种 vs 后端 run_asserts 支持 22+ 种」的不一致问题。
# expected_type 决定前端 expected 输入控件的渲染形态：
#   none        → 隐藏 expected 输入（exists/empty 类断言无需期望值）
#   text        → 普通文本输入
#   number      → 数字输入
#   select_type → 类型下拉（type_match 专用，候选 int/float/str/bool/list/dict/null）
#   json        → JSON 编辑器（json_schema 专用）
#   multi       → 多值 tag 输入（in/not_in 专用）
#   header_name → 响应头名输入（header_eq/header_contains 专用，field 列填 header 名）
ASSERT_OPERATORS: list[dict[str, Any]] = [
    # ── 比较类 ──
    {"op": "eq", "group": "compare", "label_key": "assert.operator_eq", "expected_type": "text",
     "help_zh": "实际值等于期望值"},
    {"op": "ne", "group": "compare", "label_key": "assert.operator_ne", "expected_type": "text",
     "help_zh": "实际值不等于期望值"},
    {"op": "gt", "group": "compare", "label_key": "assert.operator_gt", "expected_type": "number",
     "help_zh": "实际值大于期望值（数值比较）"},
    {"op": "gte", "group": "compare", "label_key": "assert.operator_gte", "expected_type": "number",
     "help_zh": "实际值大于等于期望值"},
    {"op": "lt", "group": "compare", "label_key": "assert.operator_lt", "expected_type": "number",
     "help_zh": "实际值小于期望值"},
    {"op": "lte", "group": "compare", "label_key": "assert.operator_lte", "expected_type": "number",
     "help_zh": "实际值小于等于期望值"},
    # ── 字符串类 ──
    {"op": "contains", "group": "string", "label_key": "assert.operator_contains", "expected_type": "text",
     "help_zh": "实际值（转字符串后）包含期望子串"},
    {"op": "not_contains", "group": "string", "label_key": "assert.operator_not_contains", "expected_type": "text",
     "help_zh": "实际值不包含期望子串"},
    {"op": "starts_with", "group": "string", "label_key": "assert.operator_starts_with", "expected_type": "text",
     "help_zh": "实际值以期望字符串开头"},
    {"op": "ends_with", "group": "string", "label_key": "assert.operator_ends_with", "expected_type": "text",
     "help_zh": "实际值以期望字符串结尾"},
    {"op": "regex", "group": "string", "label_key": "assert.operator_regex", "expected_type": "text",
     "help_zh": "实际值匹配期望正则表达式"},
    # ── 存在性类 ──
    {"op": "exists", "group": "existence", "label_key": "assert.operator_exists", "expected_type": "none",
     "help_zh": "字段值非 None（存在）"},
    {"op": "not_exists", "group": "existence", "label_key": "assert.operator_not_exists", "expected_type": "none",
     "help_zh": "字段值为 None（不存在）"},
    {"op": "empty", "group": "existence", "label_key": "assert.operator_empty", "expected_type": "none",
     "help_zh": "字段为空（None/\"\"/[]/{}/0）"},
    {"op": "not_empty", "group": "existence", "label_key": "assert.operator_not_empty", "expected_type": "none",
     "help_zh": "字段非空"},
    # ── 集合类 ──
    {"op": "in", "group": "collection", "label_key": "assert.operator_in", "expected_type": "multi",
     "help_zh": "实际值在期望列表中"},
    {"op": "not_in", "group": "collection", "label_key": "assert.operator_not_in", "expected_type": "multi",
     "help_zh": "实际值不在期望列表中"},
    # ── 类型类 ──
    {"op": "type_match", "group": "type", "label_key": "assert.operator_type_match", "expected_type": "select_type",
     "help_zh": "实际值类型匹配期望类型（int/float/str/bool/list/dict/null）"},
    # ── 结构类 ──
    {"op": "length", "group": "structure", "label_key": "assert.operator_length", "expected_type": "number",
     "help_zh": "数组/字符串/字典长度等于期望值"},
    {"op": "json_schema", "group": "structure", "label_key": "assert.operator_json_schema", "expected_type": "json",
     "help_zh": "响应体符合期望 JSON Schema（需安装 jsonschema 库）"},
    # ── 响应头类 ──
    {"op": "header_eq", "group": "header", "label_key": "assert.operator_header_eq", "expected_type": "header_name",
     "help_zh": "响应头等于期望值（field 列填 header 名，如 content-type）"},
    {"op": "header_contains", "group": "header", "label_key": "assert.operator_header_contains", "expected_type": "header_name",
     "help_zh": "响应头包含期望子串（field 列填 header 名）"},
    # ── 性能类 ──
    {"op": "response_time_lt", "group": "performance", "label_key": "assert.operator_response_time_lt", "expected_type": "number",
     "help_zh": "响应时间（ms）小于期望阈值，field 列固定填 $response_time_ms"},
]

# type_match 操作符支持的类型候选（与 run_asserts 中 type_map 保持一致）
ASSERT_TYPE_CANDIDATES: list[str] = [
    "int", "float", "str", "bool", "list", "dict", "null",
]


def get_assert_operators() -> list[dict[str, Any]]:
    """供 GET /apis/assert-operators 接口下发，返回全量操作符元数据。"""
    return [dict(item) for item in ASSERT_OPERATORS]


# ── AI 生成的文档 ─────────────────────────────────────────

class ParamDoc(BaseModel):
    name: str
    location: str   # query / body / header / path
    type: str
    required: bool = False
    description: str = ""
    example: Any = None


class ApiDoc(BaseModel):
    summary: str = ""
    description: str = ""
    params: list[ParamDoc] = Field(default_factory=list)
    response_fields: list[ParamDoc] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    generated_at: datetime | None = None


# ── 接口 DSL（核心资产） ──────────────────────────────────

class ApiDSL(BaseModel):
    id: str = ""
    name: str = ""
    source_har: str = ""        # 仅记录来源文件名，原始文件已删除
    source_hash: str = ""       # 原始请求 SHA256[:16]，用于去重
    request: RequestDSL
    response: ResponseDSL
    asserts: list[AssertRule] = Field(default_factory=list)
    doc: ApiDoc = Field(default_factory=ApiDoc)
    parse_status: ParseStatus = ParseStatus.PENDING
    # 替换 ai_analyzed: bool，支持 queued/running/done/failed 全流程跟踪
    analysis_status: AnalysisStatus = AnalysisStatus.IDLE
    analysis_error: str = ""  # 失败时记录错误原因
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))
    project_id: str = "default"
    env: str = "test"
    tags: list[str] = Field(default_factory=list)
    base_url_override: str = ""   # 执行时可覆盖 host，支持多环境


# ── 数据模板（数据工厂用） ────────────────────────────────

class FieldTemplate(BaseModel):
    name: str
    faker_method: str | None = None     # 白名单内的 faker 方法名
    enum_values: list[Any] | None = None
    boundary_min: float | None = None
    boundary_max: float | None = None
    fixed_value: Any = None
    # 异常值生成
    null_rate: float = 0.0              # 0~1，生成 None 的概率
    empty_rate: float = 0.0             # 0~1，生成 "" / 0 的概率
    invalid_values: list[Any] | None = None  # 异常值候选池（boundary 超界、类型错误等）
    invalid_rate: float = 0.0           # 0~1，从 invalid_values 采样的概率
    # 嵌套对象
    nested_template: list["FieldTemplate"] | None = None  # 字段值为 object 时递归
    description: str = ""

    @field_validator("null_rate", "empty_rate", "invalid_rate")
    @classmethod
    def _clamp_rate(cls, v: float) -> float:
        return max(0.0, min(1.0, v))


class DataTemplate(BaseModel):
    id: str = ""
    name: str = ""
    api_id: str = ""
    scenario_id: str = ""  # 关联场景ID，新建模版时可选择场景
    project_id: str = "default"
    source: str = "manual"
    job_id: str = ""
    updated_by: str = ""
    fields: list[FieldTemplate] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))


class Dataset(BaseModel):
    """数据工厂沉淀的数据集，保存一次生成结果供后续回归/场景复用。"""
    id: str = ""
    name: str = ""
    template_id: str = ""
    api_id: str = ""
    project_id: str = "default"
    source: str = "generated"
    records: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0
    created_by: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))


# ── 条件分支 ──────────────────────────────────────────────

class StepCondition(BaseModel):
    """步骤执行条件：对上下文变量做简单表达式判断"""
    variable: str           # 上下文变量名
    operator: str           # eq / ne / gt / lt / exists / contains
    value: Any = None
    # 条件不满足时的行为
    on_false: str = "skip"  # skip / fail / continue


class StepLoop(BaseModel):
    """循环容器配置：count 固定次数，list 按变量列表迭代。"""
    mode: str = "count"          # count / list
    count: int = 1
    list_ref: str = ""           # 变量引用，如 {{step_1.items}}
    item_alias: str = "item"     # 注入 loop.item / loop.<alias>


class StartParam(BaseModel):
    """Start 节点入参定义。"""
    name: str
    type: str = "string"
    default: Any = None


# ── 场景步骤（DAG 节点） ──────────────────────────────────

class ScenarioStep(BaseModel):
    step_id: str
    api_id: str = ""
    name: str = ""
    type: ScenarioStepType = ScenarioStepType.API
    parent_id: str = ""
    depends_on: list[str] = Field(default_factory=list)
    extract: dict[str, str] = Field(default_factory=dict)   # {var: jsonpath}
    override_params: dict[str, Any] = Field(default_factory=dict)
    override_headers: dict[str, str] = Field(default_factory=dict)
    retry: int = 0
    retry_delay_s: float = 1.0   # 重试间隔
    timeout_s: int = 30
    # 条件分支
    condition: StepCondition | None = None
    # 循环执行：loop 为新结构，loop_var/loop_count 仅作为前端编辑时的兼容输入，写入时应同步到 loop。
    loop: StepLoop | None = None
    loop_var: str | None = None          # 上下文中的列表变量名，迭代执行
    loop_count: int | None = None        # 固定循环次数（与 loop_var 互斥）
    start_params: list[StartParam] = Field(default_factory=list)
    # DAG 画布节点位置（VueFlow 拖拽保存，0=自动布局）
    pos_x: float = 0.0
    pos_y: float = 0.0
    # 数据工厂注入
    data_template_id: str = ""           # 关联数据模板 ID，执行前自动造数并注入
    # Postman 式编辑器扩展字段 ──────────────
    auth: dict[str, Any] = Field(default_factory=dict)           # 认证配置 {type, ...}
    pre_script: str = ""                   # 前置脚本（Python/JS）
    post_script: str = ""                  # 后置脚本（Python/JS）
    assertions: list[dict[str, Any]] = Field(default_factory=list)  # 断言列表 [{source, path, operator, expected}]
    pre_sql: list[dict[str, Any]] = Field(default_factory=list)      # 请求前 SQL 查询，结果写入 context
    post_sql: list[dict[str, Any]] = Field(default_factory=list)     # 响应后 SQL 查询，可读取响应和提取变量


# ── 场景 DSL ──────────────────────────────────────────────

class ScenarioDSL(BaseModel):
    id: str = ""
    name: str
    description: str = ""
    steps: list[ScenarioStep] = Field(default_factory=list)
    source_api_ids: list[str] = Field(default_factory=list)
    status: ScenarioStatus = ScenarioStatus.DRAFT
    ai_generated: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))
    project_id: str = "default"
    tags: list[str] = Field(default_factory=list)
    owner: str = ""  # 负责人用户名，用于执行失败时通知
    scenario_type: str = ""  # 场景生成类型：single/multi/complex，AI生成时记录


# ── 巡检任务 DSL ──────────────────────────────────────────

class MonitorDSL(BaseModel):
    id: str = ""
    name: str
    api_id: str = ""  # API 监控冗余字段，用于列表查询和展示；主语义使用 target_id
    # 支持多种目标类型：api / scenario / data_factory
    target_type: str = "api"
    target_id: str = ""    # 目标 ID（场景ID、数据工厂模板ID等）
    interval: str = "5m"
    cron: str = ""  # cron 表达式，非空时优先于 interval（如 "0 9 * * *" 每天9点）
    asserts: list[AssertRule | dict[str, Any]] = Field(default_factory=list)
    alert_channels: list[str] = Field(default_factory=list)
    enabled: bool = True
    # 风险等级与告警策略
    risk_level: RiskLevel = RiskLevel.MEDIUM
    max_consecutive_failures: int = 3   # 连续失败 N 次才告警，避免抖动
    alert_on_recovery: bool = True      # 恢复正常时也推送通知
    # 差异检测策略
    diff_threshold: int = 3             # 响应字段变更数阈值
    diff_ignore_paths: list[str] = Field(default_factory=list)  # 忽略的 jsonpath 列表
    # 告警静默期：计划维护期间跳过巡检并抑制告警
    silence_until: datetime | None = None  # 静默截止时间，None=不静默
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))
    project_id: str = "default"
    owner: str = ""  # 负责人用户名，用于告警时 @mention 通知
    environment_id: str = ""  # 绑定的执行环境 ID，执行时加载对应 base_url/headers/variables
    source: str = "manual"  # manual / ai_generated / imported
    job_id: str = ""        # 关联 AI 生成或试跑任务 ID
    updated_by: str = ""    # 最近更新人


# ── 执行结果 ──────────────────────────────────────────────

class StepResult(BaseModel):
    step_id: str
    api_id: str
    name: str = ""  # 步骤名称，用于前端结果 tab 展示
    request_sent: dict[str, Any] = Field(default_factory=dict)
    response_received: dict[str, Any] = Field(default_factory=dict)
    assert_results: list[dict[str, Any]] = Field(default_factory=list)
    passed: bool = False
    latency_ms: int = 0
    error: str = ""
    extracted_vars: dict[str, Any] = Field(default_factory=dict)
    sql_results: dict[str, Any] = Field(default_factory=dict)  # SQL 执行明细，供前端展示每条 SQL 的结果/错误/耗时
    script_results: list[dict[str, Any]] = Field(default_factory=list)  # pre/post 脚本产出与错误，供执行详情解释变量来源
    skipped: bool = False       # 条件分支导致跳过
    loop_index: int | None = None
    attempt: int = 1            # 实际执行的第几次（含重试）


class ExecutionRecord(BaseModel):
    id: str = ""
    scenario_id: str = ""
    api_id: str = ""
    type: str = "single"        # single / scenario / monitor
    steps: list[StepResult] = Field(default_factory=list)
    passed: bool = False
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))
    finished_at: datetime | None = None
    duration_ms: int = 0
    trigger: str = "manual"     # manual / scheduler / monitor
    executor: str = ""          # 执行人（手动=操作者用户名，巡检=monitor.owner）
    project_id: str = "default"
    failure_reason: str = ""    # 汇总失败原因
    execution_ip: str = ""      # 执行请求来源 IP，用于审计追溯
    # Phase 4: AI 失败诊断结果
    diagnosis: dict | None = None         # AI 诊断结果 {root_cause, explanation, suggested_fix, confidence}
    diagnosis_status: str = ""            # "" | "queued" | "running" | "done" | "failed"


# ── 告警记录 ──────────────────────────────────────────────

class AlertRecord(BaseModel):
    id: str = ""
    monitor_id: str
    api_id: str
    execution_id: str
    project_id: str = "default"  # 按项目隔离告警数据，支持前端 Dashboard/Monitor 按项目过滤
    risk_level: RiskLevel
    title: str
    message: str
    channels: list[str] = Field(default_factory=list)
    sent_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))
    resolved_at: datetime | None = None
    is_recovery: bool = False
    # P1-2: AI 告警评估结果（异步填充，不阻塞推送）
    # severity: critical/high/medium/low/noise（noise=误报识别）
    # root_cause: AI 推断的根因一句话
    # confidence: 0~1 置信度
    # assessed_at: 评估完成时间
    ai_severity: str = ""        # "" 未评估 | critical/high/medium/low/noise
    ai_root_cause: str = ""
    ai_confidence: float = 0.0
    ai_assessed_at: datetime | None = None


# ── 执行环境 ──────────────────────────────────────────────

class Environment(BaseModel):
    """执行环境：定义 API 测试运行时的目标服务器、认证头、变量等"""
    id: str = ""
    name: str                                          # 环境名称，如"开发环境"、"生产环境"
    base_url: str = ""                                 # 目标服务器根 URL，如 https://api.dev.example.com
    headers: dict[str, str] = Field(default_factory=dict)  # 执行时额外注入的请求头（如 Authorization）
    variables: dict[str, str] = Field(default_factory=dict) # 变量替换表，执行时将 {{key}} 替换为 value
    auth_templates: list[dict[str, Any]] = Field(default_factory=list)  # 项目级鉴权模板，供 StepEditor 一键套用 token/header/query 组合
    project_id: str = "default"                        # 所属项目
    description: str = ""                              # 环境描述
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))

# ── 导入差异检测 ──────────────────────────────────────────

class ImportDiffStatus(str, Enum):
    """差异记录状态 —— AI 评估前为 pending，评估后根据根因自动流转"""
    PENDING = "pending"          # 待 AI 评估
    CONFIRMED = "confirmed"      # AI 确认有效差异（API 真实变更）
    AUTO_FIXED = "auto_fixed"    # AI 已自动修复（仅文档/断言问题）
    DISMISSED = "dismissed"      # 用户手动忽略


class FieldDiff(BaseModel):
    """单个字段级别的差异详情，记录新旧值及变更类型"""
    field_path: str              # 如 "request.body.username" 或 "response.body.code"
    location: str                # "request" | "response"
    field_type_diff: str = ""    # "string→number" 或 ""
    required_diff: str = ""      # "true→false" 或 ""
    difference_type: str         # "type_changed" | "required_changed" | "added" | "removed"
    existing_value: Any = None   # 已分析 API 中的值
    new_value: Any = None        # 新导入 API 中的值


class ImportDiffRecord(BaseModel):
    """导入差异记录 —— 独立于 AlertRecord，用于 AI 评估和自动修复"""
    id: str = ""
    existing_api_id: str         # 已分析的旧 API ID
    new_api_id: str              # 新导入的 API ID
    api_path: str                # 请求路径（用于快速查找和去重）
    method: str = ""             # HTTP 方法
    fields_diff: list[FieldDiff] = Field(default_factory=list)  # 字段级差异列表
    status: ImportDiffStatus = ImportDiffStatus.PENDING
    severity: str = "low"        # AI 评估的严重程度: low / medium / high / critical
    root_cause: str = ""         # AI 评估的根因: ai_doc_error / api_evolution / api_breaking_change / api_new_field
    ai_evaluation: dict = Field(default_factory=dict)  # 完整的 AI 评估结果
    project_id: str = "default"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))))
