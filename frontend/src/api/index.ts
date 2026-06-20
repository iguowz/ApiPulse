import axios, { type AxiosResponse } from 'axios'

const http = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

// ── 类型 ───────────────────────────────────────────────────

type ID = string | number
type Params = Record<string, any>
type Body = Record<string, any>

// ── Projects ────────────────────────────────────────────────
export const projectApi = {
  list:   (): Promise<any>                  => http.get('/projects'),
  get:    (id: ID): Promise<any>            => http.get(`/projects/${id}`),
  create: (data: Body): Promise<any>        => http.post('/projects', data),
  update: (id: ID, data: Body): Promise<any> => http.put(`/projects/${id}`, data),
  remove: (id: ID): Promise<any>            => http.delete(`/projects/${id}`),
}

// ── HAR ─────────────────────────────────────────────────────
export const harApi = {
  upload: (file: File, projectId: string, { filterHost, filterUrl }: { filterHost?: string; filterUrl?: string } = {}): Promise<any> => {
    const fd = new FormData()
    fd.append('file', file)
    // 拼接域名/URL 过滤参数
    const params = [`project_id=${projectId}`]
    if (filterHost) params.push(`filter_host=${encodeURIComponent(filterHost)}`)
    if (filterUrl) params.push(`filter_url=${encodeURIComponent(filterUrl)}`)
    return http.post(`/har/upload?${params.join('&')}`, fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    })
  },
  quarantine: (): Promise<any> => http.get('/har/quarantine'),
}

// ── APIs ─────────────────────────────────────────────────────
export const apiApi = {
  // P0-2：断言操作符元数据（单一来源，前端动态渲染控件，消除前后端不一致）
  assertOperators: (): Promise<any>                          => http.get('/apis/assert-operators'),
  dryRunAsserts: (data: Body): Promise<any>                   => http.post('/apis/assertions/dry-run', data),
  // P1-5b：手动新建接口（支持完整 ApiDSL 或精简表单）
  create:        (data: Body): Promise<any>                  => http.post('/apis', data),
  list:         (params?: Params): Promise<any>              => http.get('/apis', { params }),
  get:          (id: ID): Promise<any>                       => http.get(`/apis/${id}`),
  update:       (id: ID, data: Body): Promise<any>           => http.put(`/apis/${id}`, data),
  remove:       (id: ID): Promise<any>                       => http.delete(`/apis/${id}`),
  batchDelete:  (ids: ID[]): Promise<any>                    => http.post('/apis/batch_delete', ids),
  importOpenApi:(spec: Body, projectId: string, sourceName?: string): Promise<any> => http.post('/apis/import-openapi', { spec, project_id: projectId, source_name: sourceName }),
  exportOpenApi:(ids: ID[]): Promise<AxiosResponse>          => http.post('/apis/export-openapi', ids, { responseType: 'blob' }),
  run:          (id: ID, body?: Body): Promise<any>          => http.post(`/apis/${id}/run`, body || {}),
  analyze:      (id: ID, force?: boolean): Promise<any>      => http.post(`/apis/${id}/analyze`, null, { params: { force: !!force } }),
  analyzeDoc:   (id: ID, force?: boolean): Promise<any>      => http.post(`/apis/${id}/analyze-doc`, null, { params: { force: !!force } }),
  analyzeAsserts: (id: ID, force?: boolean): Promise<any>    => http.post(`/apis/${id}/analyze-asserts`, null, { params: { force: !!force } }),
  getAsserts:   (id: ID): Promise<any>                       => http.get(`/apis/${id}/asserts`),
  replaceAsserts: (id: ID, rules: any[]): Promise<any>       => http.put(`/apis/${id}/asserts`, rules),
  addAssert:    (id: ID, rule: Body): Promise<any>           => http.post(`/apis/${id}/asserts`, rule),
  importCurl:   (curl: string, projectId: string): Promise<any> => http.post('/apis/import-curl', { curl, project_id: projectId }),
  stats:        (id: ID, limit?: number): Promise<any>       => http.get(`/stats/api/${id}`, { params: { limit } }),
  // P2-4: Mock 生成 + 契约校验
  mock:         (id: ID, statusCode?: number, caseId?: ID): Promise<any>  => http.get(`/apis/${id}/mock`, { params: { ...(statusCode ? { status_code: statusCode } : {}), ...(caseId ? { case_id: caseId } : {}) } }),
  listMockCases: (id: ID): Promise<any>                     => http.get(`/apis/${id}/mock-cases`),
  createMockCase: (id: ID, data: Body): Promise<any>         => http.post(`/apis/${id}/mock-cases`, data),
  updateMockCase: (id: ID, caseId: ID, data: Body): Promise<any> => http.put(`/apis/${id}/mock-cases/${caseId}`, data),
  deleteMockCase: (id: ID, caseId: ID): Promise<any>         => http.delete(`/apis/${id}/mock-cases/${caseId}`),
  contractCheck:(id: ID, responseBody: any): Promise<any>   => http.post(`/apis/${id}/contract-check`, { response_body: responseBody }),
}

// ── Scenarios ────────────────────────────────────────────────
export const scenarioApi = {
  list:     (params?: Params): Promise<any>              => http.get('/scenarios', { params }),
  get:      (id: ID): Promise<any>                       => http.get(`/scenarios/${id}`),
  create:   (data: Body): Promise<any>                   => http.post('/scenarios', data),
  update:   (id: ID, data: Body): Promise<any>           => http.put(`/scenarios/${id}`, data),
  remove:   (id: ID): Promise<any>                       => http.delete(`/scenarios/${id}`),
  versions: (id: ID, limit?: number): Promise<any>       => http.get(`/scenarios/${id}/versions`, { params: { limit } }),
  restoreVersion: (id: ID, versionId: ID): Promise<any>  => http.post(`/scenarios/${id}/versions/${versionId}/restore`),
  run:      (id: ID, body?: Body): Promise<any>          => http.post(`/scenarios/${id}/run`, body || {}),
  validate: (id: ID, data?: Body): Promise<any>          => data ? http.post(`/scenarios/${id}/validate`, data) : http.get(`/scenarios/${id}/validate`),
  previewStep: (id: ID, data: Body): Promise<any>        => http.post(`/scenarios/${id}/steps/preview`, data),
  dryRunScript: (data: Body): Promise<any>               => http.post('/scenarios/steps/script/dry-run', data),
  generate: (apiIds: ID[], projectId: string, scenarioType?: string): Promise<any> => http.post('/scenarios/generate', { api_ids: apiIds, project_id: projectId, scenario_type: scenarioType }),
  stats:    (id: ID, limit?: number): Promise<any>       => http.get(`/stats/scenario/${id}`, { params: { limit } }),
  // P1-9: batchRun 支持 environment_id（作为 query param 传给后端）
  batchRun:     (ids: ID[], body?: Body): Promise<any>      => http.post('/scenarios/batch_run', ids, { params: body || {} }),
  batchDelete:  (ids: ID[]): Promise<any>                => http.post('/scenarios/batch_delete', ids),
  import:       (data: Body): Promise<any>               => http.post('/scenarios/import', data),
  export:       (ids: ID[]): Promise<AxiosResponse>      => http.post('/scenarios/export', ids, { responseType: 'blob' }),
  // P1-4: 场景步骤内联 AI 辅助（推荐断言 + extract 规则）
  aiRecommend:  (apiId: ID): Promise<any>                => http.post('/scenarios/ai-recommend', { api_id: apiId }),
}

// ── DataFactory ──────────────────────────────────────────────
export const factoryApi = {
  listTemplates:  (projectId?: string): Promise<any>          => http.get('/templates', { params: { project_id: projectId || '' } }),
  getTemplate:    (id: ID): Promise<any>                      => http.get(`/templates/${id}`),
  createTemplate: (data: Body): Promise<any>                  => http.post('/templates', data),
  updateTemplate: (id: ID, data: Body): Promise<any>          => http.put(`/templates/${id}`, data),
  deleteTemplate: (id: ID): Promise<any>                      => http.delete(`/templates/${id}`),
  duplicateTemplate: (id: ID, data?: Body): Promise<any>      => http.post(`/templates/${id}/duplicate`, data || {}),
  createScenario: (id: ID, data?: Body): Promise<any>         => http.post(`/templates/${id}/scenario`, data || {}),
  listDatasets:   (projectId?: string, params?: Params): Promise<any> => http.get('/datasets', { params: { ...(params || {}), project_id: projectId || '' } }),
  getDataset:     (id: ID): Promise<any>                      => http.get(`/datasets/${id}`),
  createDataset:  (data: Body): Promise<any>                  => http.post('/datasets', data),
  deleteDataset:  (id: ID): Promise<any>                      => http.delete(`/datasets/${id}`),
  generate:       (body: Body): Promise<any>                  => http.post('/datafactory/generate', body),
  infer:          (apiId: ID, projectId?: string): Promise<any> => http.post('/datafactory/infer', { api_id: apiId, project_id: projectId || '' }),
  // P0-1: faker 方法分组元数据（前端动态渲染分组下拉）
  fakerMethods:   (): Promise<any>                            => http.get('/datafactory/faker-methods'),
  // P1-1: AI 增强数据模板（异步入队，结果走 GenerationVersion 审核闭环）
  aiEnhance:      (id: ID): Promise<any>                      => http.post(`/templates/${id}/ai-enhance`),
}

// ── Monitors ─────────────────────────────────────────────────
export const monitorApi = {
  list:    (params?: Params): Promise<any>            => http.get('/monitors', { params }),
  get:     (id: ID): Promise<any>                     => http.get(`/monitors/${id}`),
  create:  (data: Body): Promise<any>                 => http.post('/monitors', data),
  update:  (id: ID, data: Body): Promise<any>         => http.put(`/monitors/${id}`, data),
  remove:  (id: ID): Promise<any>                     => http.delete(`/monitors/${id}`),
  toggle:  (id: ID, enabled: boolean): Promise<any>   => http.post(`/monitors/${id}/toggle`, { enabled }),
  stats:   (id: ID): Promise<any>                     => http.get(`/monitors/${id}/stats`),
  validate:(body: Body): Promise<any>                  => http.post('/monitors/validate', body),
  validateExisting:(id: ID): Promise<any>              => http.get(`/monitors/${id}/validate`),
  generate:(body: Body): Promise<any>                  => http.post('/monitors/generate', body),
  job:     (jobId: ID): Promise<any>                   => http.get(`/monitors/jobs/${jobId}`),
  // P0-3: 手动立即试跑巡检，绕过 scheduler 调试配置
  runNow:  (id: ID): Promise<any>                     => http.post(`/monitors/${id}/run-now`),
}

// ── Alerts ───────────────────────────────────────────────────
export const alertApi = {
  list: (params?: Params): Promise<any> => http.get('/alerts', { params }),
}

// ── Alert Channels ───────────────────────────────────────────
export const alertChannelApi = {
  list:    (params?: Params): Promise<any>       => http.get('/alert-channels', { params }),
  get:     (id: ID): Promise<any>                => http.get(`/alert-channels/${id}`),
  create:  (data: Body): Promise<any>            => http.post('/alert-channels', data),
  update:  (id: ID, data: Body): Promise<any>    => http.put(`/alert-channels/${id}`, data),
  remove:  (id: ID): Promise<any>                => http.delete(`/alert-channels/${id}`),
}

// ── Capture ──────────────────────────────────────────────────
export const captureApi = {
  status: (): Promise<any> => http.get('/capture/status'),
  // toggle(enabled, { filterHost?, filterUrl? }) — 开关抓包并可选更新过滤条件
  toggle: (enabled: boolean, { filterHost, filterUrl }: { filterHost?: string; filterUrl?: string } = {}): Promise<any> => {
    const body: Record<string, any> = { enabled }
    // 仅在调用方显式传入过滤参数时才附加到请求体，避免发送 undefined 值
    if (filterHost !== undefined) body.filter_host = filterHost || null
    if (filterUrl !== undefined) body.filter_url = filterUrl || null
    return http.post('/capture/toggle', body)
  },
}

// ── Mock Services ────────────────────────────────────────────
export const mockServiceApi = {
  list:        (projectId?: string): Promise<any>                => http.get('/mock-services', { params: { project_id: projectId || '' } }),
  get:         (id: ID): Promise<any>                             => http.get(`/mock-services/${id}`),
  create:      (data: Body): Promise<any>                         => http.post('/mock-services', data),
  update:      (id: ID, data: Body): Promise<any>                 => http.put(`/mock-services/${id}`, data),
  remove:      (id: ID): Promise<any>                             => http.delete(`/mock-services/${id}`),
  rotateKey:   (id: ID): Promise<any>                             => http.post(`/mock-services/${id}/access-key/rotate`),
  stats:       (id: ID): Promise<any>                             => http.get(`/mock-services/${id}/stats`),
  routes:      (id: ID): Promise<any>                             => http.get(`/mock-services/${id}/routes`),
  createRoute: (id: ID, data: Body): Promise<any>                 => http.post(`/mock-services/${id}/routes`, data),
  updateRoute: (id: ID, routeId: ID, data: Body): Promise<any>    => http.put(`/mock-services/${id}/routes/${routeId}`, data),
  removeRoute: (id: ID, routeId: ID): Promise<any>                => http.delete(`/mock-services/${id}/routes/${routeId}`),
  importApi:   (id: ID, data: Body): Promise<any>                 => http.post(`/mock-services/${id}/routes/import-api`, data),
  fromTraffic: (id: ID, data: Body): Promise<any>                 => http.post(`/mock-services/${id}/routes/from-traffic`, data),
  test:        (id: ID, data: Body): Promise<any>                 => http.post(`/mock-services/${id}/test`, data),
  execute:     (id: ID, data: Body): Promise<any>                 => http.post(`/mock-services/${id}/execute`, data),
  logs:        (id: ID, params?: Params): Promise<any>            => http.get(`/mock-services/${id}/logs`, { params }),
}

// ── Database Services / SQL Snippets ─────────────────────────
export const databaseServiceApi = {
  list:   (projectId?: string): Promise<any>             => http.get('/database-services', { params: { project_id: projectId || '' } }),
  create: (data: Body): Promise<any>                     => http.post('/database-services', data),
  update: (id: ID, data: Body): Promise<any>             => http.put(`/database-services/${id}`, data),
  remove: (id: ID): Promise<any>                         => http.delete(`/database-services/${id}`),
  test:   (id: ID): Promise<any>                         => http.post(`/database-services/${id}/test`),
}

export const sqlSnippetApi = {
  list:   (params?: Params): Promise<any>                => http.get('/sql-snippets', { params }),
  create: (data: Body): Promise<any>                     => http.post('/sql-snippets', data),
  update: (id: ID, data: Body): Promise<any>             => http.put(`/sql-snippets/${id}`, data),
  remove: (id: ID): Promise<any>                         => http.delete(`/sql-snippets/${id}`),
  run:    (id: ID, data: Body): Promise<any>             => http.post(`/sql-snippets/${id}/run`, data),
}

export const sqlApi = {
  run: (data: Body): Promise<any> => http.post('/sql/run', data),
  validate: (data: Body): Promise<any> => http.post('/sql/validate', data),
}

// ── Traffic Sources / Rules ──────────────────────────────────
export const trafficApi = {
  records:      (params?: Params): Promise<any>            => http.get('/traffic/records', { params }),
  sources:      (params?: Params): Promise<any>            => http.get('/traffic/sources', { params }),
  createSource: (data: Body): Promise<any>                 => http.post('/traffic/sources', data),
  updateSource: (id: ID, data: Body): Promise<any>         => http.put(`/traffic/sources/${id}`, data),
  rotateSourceKey: (id: ID): Promise<any>                  => http.post(`/traffic/sources/${id}/access-key/rotate`),
  removeSource: (id: ID): Promise<any>                     => http.delete(`/traffic/sources/${id}`),
  rules:        (params?: Params): Promise<any>            => http.get('/traffic/rules', { params }),
  createRule:   (data: Body): Promise<any>                 => http.post('/traffic/rules', data),
  updateRule:   (id: ID, data: Body): Promise<any>         => http.put(`/traffic/rules/${id}`, data),
  removeRule:   (id: ID): Promise<any>                     => http.delete(`/traffic/rules/${id}`),
  testRule:     (data: Body): Promise<any>                 => http.post('/traffic/rules/test', data),
}

// ── Executions ───────────────────────────────────────────────
export const executionApi = {
  list:      (params?: Params): Promise<any> => http.get('/executions', { params }),
  get:       (id: ID): Promise<any>          => http.get(`/executions/${id}`),
  exportCsv: (params?: Params): Promise<AxiosResponse> => http.get('/executions/export/csv', { params, responseType: 'blob' }),
  getReport: (id: ID): Promise<AxiosResponse>         => http.get(`/executions/${id}/report`, { responseType: 'blob' }),
}

// ── Environments ─────────────────────────────────────────────
export const environmentApi = {
  list:    (projectId?: string): Promise<any>         => http.get('/environments', { params: { project_id: projectId || '' } }),
  get:     (id: ID): Promise<any>                     => http.get(`/environments/${id}`),
  create:  (data: Body): Promise<any>                 => http.post('/environments', data),
  update:  (id: ID, data: Body): Promise<any>         => http.put(`/environments/${id}`, data),
  remove:  (id: ID): Promise<any>                     => http.delete(`/environments/${id}`),
}

// ── Settings ─────────────────────────────────────────────────
export const settingsApi = {
  getLlm:         (): Promise<any>               => http.get('/settings/llm'),
  saveLlm:        (data: Body): Promise<any>     => http.put('/settings/llm', data),
  testLlm:        (data: Body): Promise<any>     => http.post('/settings/llm/test', data),
  discoverModels: (data: Body): Promise<any>     => http.post('/settings/llm/models', data),
  getGeneral:     (): Promise<any>               => http.get('/settings/general'),
  saveGeneral:    (data: Body): Promise<any>     => http.put('/settings/general', data),
}

// ── DLQ (死信队列) ──────────────────────────────────────────
export const dlqApi = {
  list:   (queue?: string): Promise<any>           => queue ? http.get(`/ai/dlq/${queue}`) : http.get('/ai/dlq'),
  retry:  (index: number, queue?: string): Promise<any>  => queue ? http.post(`/ai/dlq/${queue}/${index}/retry`) : http.post(`/ai/dlq/${index}/retry`),
  remove: (index: number, queue?: string): Promise<any>  => queue ? http.delete(`/ai/dlq/${queue}/${index}`) : http.delete(`/ai/dlq/${index}`),
  scenarioList:   (): Promise<any>                 => http.get('/ai/scenario-dlq'),
  scenarioRetry:  (index: number): Promise<any>    => http.post(`/ai/scenario-dlq/${index}/retry`),
  scenarioRemove: (index: number): Promise<any>    => http.delete(`/ai/scenario-dlq/${index}`),
}

// ── AI Jobs / Prompt 管理 ───────────────────────────────────
export const aiJobApi = {
  list:  (params?: Params): Promise<any>       => http.get('/ai/jobs', { params }),
  get:   (id: ID, params?: Params): Promise<any> => http.get(`/ai/jobs/${id}`, { params }),
  retry: (id: ID): Promise<any>                => http.post(`/ai/jobs/${id}/retry`),
}

export const promptApi = {
  list:     (params?: Params): Promise<any>       => http.get('/prompts', { params }),
  get:      (id: ID): Promise<any>                => http.get(`/prompts/${id}`),
  create:   (data: Body): Promise<any>            => http.post('/prompts', data),
  update:   (id: ID, data: Body): Promise<any>    => http.put(`/prompts/${id}`, data),
  activate: (id: ID): Promise<any>                => http.post(`/prompts/${id}/activate`),
  remove:   (id: ID): Promise<any>                => http.delete(`/prompts/${id}`),
  reset:    (taskType: string): Promise<any>      => http.post(`/prompts/task-type/${taskType}/reset`),
}

// ── Memory (4-tier 记忆管理) ──────────────────────────────
export const memoryApi = {
  listL1:   (params?: Params): Promise<any>  => http.get('/memory/l1', { params }),
  deleteL1: (key: string): Promise<any>      => http.delete(`/memory/l1/${encodeURIComponent(key)}`),
  listL2:   (params?: Params): Promise<any>  => http.get('/memory/l2', { params }),
  deleteL2: (id: ID): Promise<any>           => http.delete(`/memory/l2/${id}`),
  listL3:   (params?: Params): Promise<any>  => http.get('/memory/l3', { params }),
  search:   (params?: Params): Promise<any>  => http.post('/memory/search', null, { params }),
}

// ── Knowledge (ReMe 记忆系统) ──────────────────────────────
export const knowledgeApi = {
  list:          (params?: Params): Promise<any>    => http.get('/knowledge', { params }),
  get:           (id: ID): Promise<any>             => http.get(`/knowledge/${id}`),
  update:        (id: ID, data: Body): Promise<any> => http.put(`/knowledge/${id}`, data),
  remove:        (id: ID): Promise<any>             => http.delete(`/knowledge/${id}`),
  feedback:      (id: ID, action: string): Promise<any> => http.post(`/knowledge/${id}/feedback`, { action }),
  extract:       (apiId: ID): Promise<any>          => http.post(`/knowledge/extract/${apiId}`),
  batchExtract:  (projectId: string): Promise<any>  => http.post('/knowledge/batch-extract', { project_id: projectId }),
  consolidate:   (projectId: string): Promise<any>  => http.post('/knowledge/consolidate', { project_id: projectId }),
  batchDelete:   (ids: ID[], projectId: string): Promise<any> => http.delete('/knowledge/batch-delete', { data: { ids, project_id: projectId } }),
}

// ── Auth ─────────────────────────────────────────────────────
// JWT token 注入拦截器：请求时自动携带 Authorization 头
http.interceptors.request.use((config: any) => {
  const token = localStorage.getItem('aqp_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 401 全局处理：收到 401 时清除本地 token（可能已过期），
// 但不强制跳转 —— 由路由守卫负责判断是否需要登录
http.interceptors.response.use(
  (r: AxiosResponse) => r.data,
  (e: any) => {
    if (e.response?.status === 401) {
      localStorage.removeItem('aqp_token')
      localStorage.removeItem('aqp_user')
    }
    const msg = e.response?.data?.detail || e.message || 'Network error'
    return Promise.reject(new Error(typeof msg === 'string' ? msg : JSON.stringify(msg)))
  }
)

export const authApi = {
  login:          (username: string, password: string): Promise<any> => http.post('/auth/login', { username, password }),
  register:       (data: Body): Promise<any>              => http.post('/auth/register', data),
  me:             (): Promise<any>                        => http.get('/auth/me'),
  listUsers:      (): Promise<any>                        => http.get('/auth/users'),
  getUser:        (id: ID): Promise<any>                  => http.get(`/auth/users/${id}`),
  updateUser:     (id: ID, data: Body): Promise<any>      => http.put(`/auth/users/${id}`, data),
  deleteUser:     (id: ID): Promise<any>                  => http.delete(`/auth/users/${id}`),
  changePassword: (old_password: string, new_password: string): Promise<any> => http.post('/auth/change-password', { old_password, new_password }),
}

// ── AI Operation Logs ────────────────────────────────────────
export const aiOpLogApi = {
  list: (params?: Params): Promise<any> => http.get('/ai-logs', { params }),
}

// ── Import Diffs ─────────────────────────────────────────────
export const importDiffApi = {
  list:       (params?: Params): Promise<any>        => http.get('/diff-alerts', { params }),
  count:      (params?: Params): Promise<any>        => http.get('/diff-alerts/count', { params }),
  get:        (id: ID): Promise<any>                 => http.get(`/diff-alerts/${id}`),
  comparison: (id: ID): Promise<any>                 => http.get(`/diff-alerts/${id}/comparison`),
  resolve:    (id: ID, action: string): Promise<any> => http.put(`/diff-alerts/${id}/resolve`, { action }),
}

// ── Generations (AI 生成审核) ─────────────────────────────────
export const generationApi = {
  list:         (params?: Params): Promise<any>           => http.get('/generations', { params }),
  get:          (id: ID): Promise<any>                    => http.get(`/generations/${id}`),
  diff:         (id: ID): Promise<any>                    => http.get(`/generations/${id}/diff`),
  accept:       (id: ID): Promise<any>                    => http.post(`/generations/${id}/accept`),
  acceptPartial:(id: ID, fields: string[]): Promise<any>  => http.post(`/generations/${id}/accept-partial`, fields),
  reject:       (id: ID, feedback?: string): Promise<any> => http.post(`/generations/${id}/reject`, feedback || ''),
  edit:         (id: ID, content: Body): Promise<any>     => http.post(`/generations/${id}/edit`, content),
}

// ── Audit ────────────────────────────────────────────────────
export const auditApi = {
  list: (params?: Params): Promise<any> => http.get('/audit-logs', { params }),
}

// ── Stats ────────────────────────────────────────────────────
export const statsApi = {
  overview:     (projectId: string): Promise<any>                        => http.get('/stats/overview', { params: { project_id: projectId } }),
  workbench:    (projectId: string): Promise<any>                        => http.get('/stats/workbench', { params: { project_id: projectId } }),
  topFailing:   (projectId: string, limit?: number, hours?: number): Promise<any> => http.get('/stats/dashboard/top-failing', { params: { project_id: projectId, limit, hours } }),
  healthScores: (projectId: string, limit?: number): Promise<any>         => http.get('/stats/dashboard/health-scores', { params: { project_id: projectId, limit } }),
  trends:       (projectId: string, period?: string, granularity?: string): Promise<any> => http.get('/stats/dashboard/trends', { params: { project_id: projectId, period, granularity } }),
  sla:          (projectId: string, period?: string): Promise<any>         => http.get('/stats/dashboard/sla', { params: { project_id: projectId, period } }),
  aiQuality:    (projectId: string, period?: string): Promise<any>         => http.get('/stats/dashboard/ai-quality', { params: { project_id: projectId, period } }),
  teamActivity: (projectId: string, days?: number): Promise<any>          => http.get('/stats/dashboard/team-activity', { params: { project_id: projectId, days } }),
  coverage:     (projectId: string): Promise<any>                           => http.get('/stats/coverage', { params: { project_id: projectId } }),
}

// ── System ───────────────────────────────────────────────────
export const systemApi = {
  queues: (): Promise<any> => http.get('/system/queues'),
}

// ── WebSocket helper ─────────────────────────────────────────
export function openWs(path: string, onMessage: (data: any) => void): any {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  let retryMs = 1000  // 初始重连间隔 1s，指数退避上限 30s
  let shouldReconnect = true  // 主动 close 时置 false，防止泄漏

  function connect(): any {
    const token = localStorage.getItem('aqp_token') || ''
    const sep = path.includes('?') ? '&' : '?'
    const authPath = token ? `${path}${sep}token=${encodeURIComponent(token)}` : path
    const ws = new WebSocket(`${proto}://${location.host}/ws${authPath}`)
    ws.onmessage = (e: MessageEvent) => {
      // 尝试 JSON 解析，失败时回退为原始字符串（支持纯文本消息）
      try { onMessage(JSON.parse(e.data)) } catch { onMessage(e.data) }
    }
    // 连接断开时自动重连（指数退避：1s/2s/4s/.../30s）
    ws.onclose = (event) => {
      if (event.code === 1008) {
        shouldReconnect = false
        console.warn('[ws] unauthorized, reconnect stopped')
        return
      }
      if (!shouldReconnect) return
      setTimeout(() => {
        retryMs = Math.min(retryMs * 2, 30000)
        connect()
      }, retryMs)
    }
    // 连接异常时记录并依赖 onclose 触发重连
    ws.onerror = () => {
      console.warn('[ws] connection error, will retry via onclose')
    }
    // 连接成功时重置退避间隔
    ws.onopen = () => {
      retryMs = 1000
    }
    // 暴露主动关闭方法，调用后不再重连
    ;(ws as any).terminate = () => {
      shouldReconnect = false
      ws.close()
    }
    return ws
  }

  return connect()
}
