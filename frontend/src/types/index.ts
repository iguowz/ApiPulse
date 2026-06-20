// ── 枚举 ─────────────────────────────────────────────────

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'HEAD' | 'OPTIONS' | 'CONNECT' | 'TRACE'
export type AnalysisStatus = 'idle' | 'queued' | 'running' | 'done' | 'failed'
export type ScenarioStatus = 'draft' | 'ready' | 'running' | 'done' | 'failed'
export type RiskLevel = 'low' | 'medium' | 'high' | 'critical'
export type BodyType = 'json' | 'form' | 'text' | 'none' | 'multipart' | 'xml'
export type StepExecState = 'idle' | 'queued' | 'running' | 'passed' | 'failed' | 'skipped'

// ── 请求/响应 DTO ───────────────────────────────────────

export interface RequestDSL {
  method: HttpMethod
  url: string
  path: string
  query_params: Record<string, any>
  headers: Record<string, string>
  body: any
  body_type: BodyType
}

export interface ResponseDSL {
  status_code: number
  headers: Record<string, string>
  body: any
  latency_ms: number
}

// ── 断言规则 ────────────────────────────────────────────

export interface AssertRule {
  field: string
  operator: string  // eq/ne/gt/lt/contains/regex/type_match/json_schema/header_eq/header_contains/response_time_lt/empty/not_empty/in/not_in/length 等
  expected: any
  description: string
  risk_level: RiskLevel
}

// ── AI 生成的文档 ───────────────────────────────────────

export interface ParamDoc {
  name: string
  location: string  // query / body / header / path
  type: string
  required: boolean
  description: string
  example: any
}

export interface ApiDoc {
  summary: string
  description: string
  params: ParamDoc[]
  response_fields: ParamDoc[]
  tags: string[]
  generated_at: string | null
}

// ── API DSL (核心资产) ──────────────────────────────────

export interface ApiDSL {
  id: string
  name: string
  source_har: string
  source_hash: string
  request: RequestDSL
  response: ResponseDSL
  asserts: AssertRule[]
  doc: ApiDoc
  parse_status: string
  analysis_status: AnalysisStatus
  analysis_error: string
  created_at: string
  updated_at: string
  project_id: string
  env: string
  tags: string[]
  base_url_override: string
}

// ── 数据工厂 ────────────────────────────────────────────

export interface FieldTemplate {
  name: string
  faker_method: string | null
  enum_values: any[] | null
  boundary_min: number | null
  boundary_max: number | null
  fixed_value: any
  null_rate: number
  empty_rate: number
  invalid_values: any[] | null
  invalid_rate: number
  nested_template: FieldTemplate[] | null
  description: string
}

export interface DataTemplate {
  id: string
  name: string
  project_id: string
  api_id: string
  source: 'manual' | 'inferred' | 'ai_enhanced'
  job_id?: string
  updated_by?: string
  fields: FieldTemplate[]
  created_at: string
  updated_at: string
}

// ── 场景 ────────────────────────────────────────────────

export interface StepCondition {
  variable: string
  operator: string
  value: any
  on_false: 'skip' | 'fail' | 'continue'
}

export interface StepLoop {
  mode: 'count' | 'list'
  count?: number
  list_ref?: string
  item_alias?: string
}

export interface ScenarioStep {
  step_id: string
  api_id: string
  name: string
  type?: StepType
  parent_id?: string
  depends_on: string[]
  extract: Record<string, string | Record<string, any>>
  override_params: Record<string, any>
  override_headers: Record<string, string>
  retry: number
  retry_delay_s: number
  timeout_s: number
  condition: StepCondition | null
  loop?: StepLoop | null
  loop_var: string | null
  loop_count: number | null
  wait_ms: number  // 步骤执行前等待时间(毫秒)，0 表示不等待
  data_template_id: string
  start_params?: { name: string, type: string, default: any }[]  // Start 步骤定义的入参列表
  // Postman 式编辑器扩展字段
  auth: Record<string, any>
  pre_script: string
  post_script: string
  pre_sql?: Record<string, any>[]
  post_sql?: Record<string, any>[]
  assertions: Record<string, any>[]
}

// 步骤类型：api=普通API步骤, condition=条件容器, loop=循环容器, start=开始步骤, end=结束步骤
export type StepType = 'api' | 'condition' | 'loop' | 'start' | 'end'

// 树形步骤节点，用于前端编辑器展示（condition/loop 容器包含子步骤）
export interface ScenarioStepTree extends ScenarioStep {
  type: StepType
  children: ScenarioStepTree[]
  expanded?: boolean  // UI 展开/折叠状态（仅前端使用）
  parent_id?: string  // 父容器 step_id，用于多层嵌套还原（持久化到后端）
  nesting_level?: number  // 当前嵌套深度，0=顶层, 1=一级容器内...（仅前端使用）
}

export interface ScenarioDSL {
  id: string
  name: string
  description: string
  steps: ScenarioStep[]
  source_api_ids: string[]
  status: ScenarioStatus
  ai_generated: boolean
  created_at: string
  updated_at: string
  project_id: string
  tags: string[]
  owner: string
}

// ── 巡检 ────────────────────────────────────────────────

export interface MonitorDSL {
  id: string
  name: string
  api_id: string
  target_type: string
  target_id: string
  interval: string
  cron: string
  asserts: AssertRule[]
  alert_channels: string[]
  enabled: boolean
  risk_level: RiskLevel
  max_consecutive_failures: number
  alert_on_recovery: boolean
  diff_threshold: number
  diff_ignore_paths: string[]
  silence_until: string | null
  created_at: string
  updated_at: string
  project_id: string
  owner: string
}

// ── 执行 ────────────────────────────────────────────────

export interface StepResult {
  step_id: string
  api_id: string
  name: string  // 步骤名称，用于前端结果 tab 展示
  request_sent: Record<string, any>
  response_received: Record<string, any>
  assert_results: Record<string, any>[]
  passed: boolean
  latency_ms: number
  error: string
  extracted_vars: Record<string, any>
  sql_results: Record<string, any>
  script_results: Record<string, any>[]
  skipped: boolean
  loop_index: number | null
  attempt: number
}

export interface ExecutionRecord {
  id: string
  scenario_id: string
  api_id: string
  type: 'single' | 'scenario' | 'monitor'
  steps: StepResult[]
  passed: boolean
  started_at: string
  finished_at: string | null
  duration_ms: number
  trigger: 'manual' | 'scheduler' | 'monitor'
  executor: string
  project_id: string
  failure_reason: string
  execution_ip: string
}

// ── 告警 ────────────────────────────────────────────────

export interface AlertRecord {
  id: string
  monitor_id: string
  api_id: string
  execution_id: string
  project_id: string
  risk_level: RiskLevel
  title: string
  message: string
  channels: string[]
  sent_at: string
  resolved_at: string | null
  is_recovery: boolean
}

// ── 通用分页 ────────────────────────────────────────────

export interface PaginatedResponse<T> {
  items: T[]
  total: number
}

export interface AiJobStatusCounts {
  completed: number
  running: number
  queued: number
  failed: number
}

export interface OverviewStats {
  project_id: string
  apis: { total: number; statuses: Record<string, number> }
  scenarios: { total: number; ai_generated: number; statuses: Record<string, number> }
  executions: { total: number; passed: number; failed: number; pass_rate_pct: number; by_type: Record<string, number> }
  monitors: { active: number }
  alerts: { total: number }
  ai_jobs: { scenario: AiJobStatusCounts; monitor: AiJobStatusCounts }
  llm: { model: string | null; configured: boolean }
}

export interface HealthScore {
  api_id: string
  api_name: string
  api_path: string
  method: string
  score: number
  grade: 'excellent' | 'good' | 'fair' | 'poor'
  pass_rate: number
  avg_latency_ms: number
  sample_count: number
}

export interface TrendPoint {
  time: string
  pass_rate: number
  total: number
  passed: number
  avg_latency_ms: number
}

export interface TopFailingApi {
  api_id: string
  api_name: string
  api_path: string
  method: string
  fail_count: number
  total_count: number
  fail_rate: number
}

export interface SlaReport {
  api_id: string
  api_name: string
  api_path: string
  method: string
  total_count: number
  passed_count: number
  availability: number
  meets_target: boolean
}

// ── 项目 ────────────────────────────────────────────────

export interface Project {
  id: string
  name: string
  description: string
  members: string[]
  created_at: string
}

// ── 用户/认证 ───────────────────────────────────────────

export interface User {
  id: string
  username: string
  role: 'admin' | 'editor' | 'viewer' | 'monitor_admin'
  project_id: string
  created_at: string
}

// ── 渠道 ────────────────────────────────────────────────

export interface AlertChannel {
  id: string
  name: string
  type: 'dingtalk' | 'wecom' | 'slack' | 'custom'
  webhook_url: string
  config: Record<string, any>
  project_id: string
  created_at: string
}

// ── 导入差异检测 ────────────────────────────────────────

export type ImportDiffStatus = 'pending' | 'confirmed' | 'auto_fixed' | 'dismissed'

export interface FieldDiff {
  field_path: string
  location: string            // "request" | "response"
  field_type_diff: string     // "string→number" 或 ""
  required_diff: string       // "true→false" 或 ""
  difference_type: string     // "type_changed" | "required_changed" | "added" | "removed"
  existing_value: any
  new_value: any
}

export interface ImportDiffRecord {
  id: string
  existing_api_id: string
  new_api_id: string
  api_path: string
  method: string
  fields_diff: FieldDiff[]
  status: ImportDiffStatus
  severity: string            // "low" | "medium" | "high" | "critical"
  root_cause: string          // "ai_doc_error" | "api_evolution" | "api_breaking_change" | "api_new_field"
  ai_evaluation: Record<string, any>
  project_id: string
  created_at: string
  updated_at: string
}
