<template>
  <div class="page">
    <div class="page-header">
      <div>
        <div class="page-title">{{ $t('scenarios.title') }}</div>
        <div class="page-subtitle">{{ $t('scenarios.total', { total }) }}</div>
      </div>
      <div class="flex items-center gap-8">
        <el-button @click="importScenarios" :disabled="importing" :loading="importing">{{ $t('scenarios.import') }}</el-button>
        <el-button @click="exportScenarios" :disabled="!selectedIds.length">{{ $t('scenarios.export') }} ({{ selectedIds.length }})</el-button>
        <el-button @click="showGenModal = true">{{ $t('scenarios.ai_gen_btn') }}</el-button>
        <el-button type="primary" @click="showCreateModal = true">{{ $t('scenarios.new') }}</el-button>
      </div>
    </div>

    <div class="page-body">
      <!-- 工具栏：状态筛选 + 搜索 + 批量操作 -->
      <div class="flex items-center justify-between" style="margin-bottom:12px;flex-wrap:wrap;gap:8px">
        <div class="flex items-center gap-8">
          <el-radio-group v-model="statusFilter" size="small" @change="onFilterChange">
            <el-radio-button value="">{{ $t('scenarios.filter_all') }}</el-radio-button>
            <el-radio-button value="draft">{{ $t('scenarios.status_draft') }}</el-radio-button>
            <el-radio-button value="ready">{{ $t('scenarios.status_ready') }}</el-radio-button>
            <!-- P0-1: 补 running 筛选项（状态流转修复后会出现 running 状态） -->
            <el-radio-button value="running">{{ $t('scenarios.status_running') }}</el-radio-button>
            <el-radio-button value="done">{{ $t('scenarios.status_done') }}</el-radio-button>
            <el-radio-button value="failed">{{ $t('scenarios.status_failed') }}</el-radio-button>
          </el-radio-group>
          <!-- P1-7: 类型筛选补 complex（与 AI 生成三策略对齐） -->
          <el-select v-model="typeFilter" size="small" style="width:130px" @change="onFilterChange" clearable :placeholder="$t('scenarios.filter_type')">
            <el-option value="single" :label="$t('scenarios.type_single')" />
            <el-option value="multi" :label="$t('scenarios.type_multi')" />
            <el-option value="complex" :label="$t('scenarios.type_complex', '复杂场景')" />
          </el-select>
          <el-input v-model="searchFilter" :placeholder="$t('scenarios.search_placeholder')" size="small" clearable style="width:220px" @input="onSearchInput" @clear="onSearchInput('')" />
        </div>
        <div class="flex items-center gap-8">
          <el-button v-if="selectedIds.length" type="warning" size="small" @click="batchRun" :disabled="batchRunning">
            {{ batchRunning ? '…' : $t('scenarios.batch_run', { n: selectedIds.length }) }}
          </el-button>
          <el-button v-if="selectedIds.length" type="danger" size="small" @click="batchDelete" :disabled="batchDeleting">
            {{ batchDeleting ? '…' : $t('scenarios.batch_delete', { n: selectedIds.length }) }}
          </el-button>
        </div>
      </div>

      <el-card style="padding:0">
      <el-table
        :data="items"
        v-loading="loading"
        row-key="id"
        :empty-text="$t('scenarios.no_scenarios')"
        @selection-change="onSelectionChange"
      >
        <el-table-column type="selection" width="40" />
        <el-table-column :label="$t('scenarios.col_name')" min-width="180">
          <template #default="{ row }">
            <!-- 仅点击名称列跳转详情，避免与 checkbox 选择冲突 -->
            <div style="font-weight:500;cursor:pointer" @click.stop="$router.push(`/scenarios/${row.id}`)">{{ row.name }}</div>
            <div class="text-3" style="font-size:11px">{{ row.description }}</div>
          </template>
        </el-table-column>
        <el-table-column :label="$t('scenarios.col_steps')" width="80">
          <template #default="{ row }">
            <span class="mono text-2">{{ row.steps?.length || 0 }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="$t('scenarios.col_type')" width="90">
          <template #default="{ row }">
            <!-- P1-7: 优先用 scenario_type 字段（AI 生成时写入），回退到 steps.length 推断 -->
            <el-tag :type="scenarioTypeTag(row)" size="small">
              {{ scenarioTypeLabel(row) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="$t('scenarios.col_ai_generated')" width="80">
          <template #default="{ row }">
            <el-tag v-if="row.ai_generated" type="info" size="small">AI</el-tag>
            <el-tag v-else type="info" size="small">{{ $t('scenarios.manual') }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="$t('scenarios.col_status')" width="80">
          <template #default="{ row }">
            <el-tag :type="scenarioStatusTagType(row.status)" size="small">{{ $t('scenarios.status_' + row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="下一步" min-width="150">
          <template #default="{ row }">
            <div class="scenario-next-actions">
              <el-button v-if="row.status === 'draft' || !(row.steps?.length)" link type="primary" size="small" @click.stop="$router.push(`/scenarios/${row.id}`)">编辑步骤</el-button>
              <el-button v-else-if="row.status === 'failed'" link type="danger" size="small" @click.stop="$router.push(`/scenarios/${row.id}`)">查看失败</el-button>
              <el-button v-else link type="primary" size="small" @click.stop="runScenario(row)">运行</el-button>
              <el-tag v-if="batchJob.scenarioStatus[row.id]" size="small" :type="batchJob.scenarioStatus[row.id] === 'failed' ? 'danger' : 'info'">
                {{ batchJob.scenarioStatus[row.id] }}
              </el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column :label="$t('scenarios.col_update_time')" width="130">
          <template #default="{ row }">
            <span class="text-2">{{ fmt.fromNow(row.updated_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="$t('scenarios.col_actions')" width="200">
          <template #default="{ row }">
            <div class="flex items-center gap-8" @click.stop>
              <el-button type="success" size="small" @click="runScenario(row)" :disabled="runningId === row.id">
                {{ runningId === row.id ? '…' : $t('scenarios.run_btn') }}
              </el-button>
              <el-dropdown trigger="click" @command="(cmd) => handleAddTo(row, cmd)">
                <el-button size="small" :icon="ArrowDown">{{ $t('scenarios.add_to') }}</el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="factory">{{ $t('scenarios.add_to_factory') }}</el-dropdown-item>
                    <el-dropdown-item command="monitor">{{ $t('scenarios.add_to_monitor') }}</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
              <el-button type="danger" size="small" :icon="Close" @click="removeScenario(row)" />
            </div>
          </template>
        </el-table-column>
      </el-table>

      <AppPagination v-model:page="page" :page-size="pageSize" :total="total" @page-change="load" />

      </el-card>
    </div>

    <!-- AI Generate dialog -->
    <el-dialog v-model="showGenModal" :title="$t('scenarios.ai_gen_title')" width="560px">
      <!-- 场景类型选择 -->
      <div style="margin-bottom:16px">
        <div style="margin-bottom:8px;font-size:13px;font-weight:500">{{ $t('scenarios.gen_type_label') }}</div>
        <el-radio-group v-model="genType" size="small">
          <el-radio-button value="single">{{ $t('scenarios.gen_type_single_short') }}</el-radio-button>
          <el-radio-button value="multi">{{ $t('scenarios.gen_type_multi_short') }}</el-radio-button>
          <el-radio-button value="complex">{{ $t('scenarios.gen_type_complex_short') }}</el-radio-button>
        </el-radio-group>
        <div style="margin-top:4px;font-size:11px;color:var(--text-3)">{{ $t('scenarios.gen_type_' + genType) }}</div>
      </div>
      <el-form label-position="top">
        <el-form-item :label="$t('scenarios.select_apis_label')">
          <el-input v-model="genSearch" :placeholder="$t('scenarios.search_apis_placeholder')" @input="searchApis" />
        </el-form-item>
      </el-form>
      <div class="api-pick-list">
        <label v-for="a in apiPickList" :key="a.id" class="api-pick-item">
          <input type="checkbox" :value="a.id" v-model="selectedApiIds" />
          <span :class="methodClass(a.request?.method)">{{ a.request?.method }}</span>
          <span class="mono" style="font-size:12px">{{ a.request?.path }}</span>
          <span class="text-3" style="font-size:11px">{{ a.doc?.summary }}</span>
          <el-tag v-if="a.analysis_status" size="small" effect="plain">{{ a.analysis_status }}</el-tag>
        </label>
      </div>
      <div style="margin-top:8px;font-size:12px;color:var(--text-3)">
        {{ $t('scenarios.selected_count', { count: selectedApiIds.length }) }}
        <span v-if="genType === 'single' && selectedApiIds.length > 1" style="color:var(--el-color-warning);margin-left:8px">{{ $t('scenarios.gen_type_hint_single') }}</span>
        <span v-else-if="genType === 'multi' && selectedApiIds.length < 2" style="color:var(--text-3);margin-left:8px">{{ $t('scenarios.gen_type_hint_multi') }}</span>
        <span v-else-if="genType === 'complex'" style="color:var(--text-3);margin-left:8px">{{ $t('scenarios.gen_type_hint_complex') }}</span>
      </div>
      <template #footer>
        <el-button @click="showGenModal = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" @click="generateScenarios" :loading="generating" :disabled="generating || !selectedApiIds.length || (genType === 'multi' && selectedApiIds.length < 2)">
          {{ generating ? $t('scenarios.generating') : $t('scenarios.generate') }}
        </el-button>
      </template>
    </el-dialog>

    <!-- Hidden file input for import -->
    <input ref="importFileInput" type="file" accept=".json" style="display:none" @change="onImportFile" />

    <!-- Create dialog -->
    <el-dialog v-model="showCreateModal" :title="$t('scenarios.new')" width="420px">
      <el-form label-position="top">
        <el-form-item :label="$t('scenarios.scenario_name')">
          <el-input v-model="newScenario.name" :placeholder="$t('scenarios.name_placeholder')" />
        </el-form-item>
        <el-form-item :label="$t('scenarios.col_description')">
          <el-input type="textarea" v-model="newScenario.description" :placeholder="$t('scenarios.description_placeholder')" />
        </el-form-item>
        <el-form-item :label="$t('common.owner')">
          <el-input v-model="newScenario.owner" :placeholder="$t('common.owner_placeholder')" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateModal = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" @click="createScenario" :disabled="creating || !newScenario.name">
          {{ creating ? $t('scenarios.creating') : $t('common.create') }}
        </el-button>
      </template>
    </el-dialog>

    <!-- P1-8/9: 运行环境选择弹窗（单场景/批量运行共用） -->
    <el-dialog v-model="runEnvModal" :title="runEnvScenarioId ? $t('scenarios.run_with_env') : $t('scenarios.batch_run_with_env')" width="440px">
      <el-form label-position="top">
        <el-form-item :label="$t('scenarios.select_env')">
          <el-select v-model="runEnvId" clearable style="width:100%" :placeholder="$t('scenarios.select_env_placeholder')">
            <el-option v-for="env in environments" :key="env.id" :label="env.name" :value="env.id">
              <span>{{ env.name }}</span>
              <span v-if="env.base_url" class="mono text-3" style="font-size:10px;margin-left:8px">{{ env.base_url }}</span>
            </el-option>
          </el-select>
          <div class="text-3" style="font-size:11px;margin-top:4px">{{ $t('scenarios.env_optional_hint') }}</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="runEnvModal = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" @click="confirmRunWithEnv" :disabled="runEnvScenarioId === null && !selectedIds.length">
          {{ $t('scenarios.confirm_run') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'
import { Close, ArrowDown } from '@element-plus/icons-vue'
import { scenarioApi, apiApi, environmentApi, openWs } from '@/api'
import { useProjectStore, useToastStore } from '@/stores'
import { fmt, methodClass, scenarioStatusTagType } from '@/utils'
import { useDebounceFn } from '@vueuse/core'
import AppPagination from '@/components/AppPagination.vue'

const router = useRouter()
const { t } = useI18n()
const projectStore = useProjectStore()
const toast = useToastStore()

const items    = ref([])
const total    = ref(0)
const loading  = ref(false)
const page     = ref(1)
const pageSize = 50
const runningId = ref(null)
const statusFilter = ref('')
const typeFilter = ref('')  // 场景类型筛选：single/multi
const searchFilter = ref('')
const selectedIds  = ref([])
const batchRunning = ref(false)
const batchDeleting = ref(false)
const importing    = ref(false)
const exporting    = ref(false)
const importFileInput = ref(null)
// P1-8/9: 运行环境选择（列表页运行/批量运行时弹出选择）
const runEnvModal = ref(false)
const runEnvScenarioId = ref(null)  // 待执行的场景 ID（null 表示批量）
const runEnvId = ref(localStorage.getItem('apipulse-env') || '')  // 选中的环境 ID，初始值从全局环境选择器读取
const environments = ref([])        // 可用环境列表
let scenarioWs = null
const batchJob = ref({ job_id: '', status: '', total: 0, scenarioStatus: {} })

// P1-7: 场景类型展示辅助 —— 优先用 scenario_type 字段，回退到 steps.length 推断
function scenarioTypeLabel(row) {
  const st = row.scenario_type
  if (st === 'single' || st === 'multi' || st === 'complex') return t(`scenarios.type_${st}`)
  // 无 scenario_type 字段（手动创建）→ 不计 start/end 只统计 api 步骤数
  const n = (row.steps || []).filter(s => s.type !== 'start' && s.type !== 'end').length
  return n <= 1 ? t('scenarios.type_single') : t('scenarios.type_multi')
}
function scenarioTypeTag(row) {
  const st = row.scenario_type
  if (st === 'complex') return 'danger'
  if (st === 'multi') return 'warning'
  if (st === 'single') return 'info'
  const n = (row.steps || []).filter(s => s.type !== 'start' && s.type !== 'end').length
  return n <= 1 ? 'info' : 'warning'
}

const showGenModal    = ref(false)
const showCreateModal = ref(false)
const genSearch       = ref('')
const apiPickList     = ref([])
const selectedApiIds  = ref([])
const genType         = ref('multi')  // 场景生成类型：single/multi/complex
const generating      = ref(false)
const newScenario     = ref({ name: '', description: '', owner: '' })
const creating        = ref(false)

async function load() {
  loading.value = true
  try {
    selectedIds.value = []
    const params = { project_id: projectStore.current, skip: (page.value-1)*pageSize, limit: pageSize }
    // 状态筛选：按 status 字段过滤场景列表
    if (statusFilter.value) params.status = statusFilter.value
    // 搜索关键词：按名称/描述模糊匹配
    if (searchFilter.value) params.search = searchFilter.value
    // 场景类型筛选：单接口(single)或多步骤(multi)
    if (typeFilter.value) params.scenario_type = typeFilter.value
    const res = await scenarioApi.list(params)
    items.value = res.items || []
    total.value = res.total || 0
  } catch (e) {
    toast.error(e.message || t('scenarios.load_failed'))
  } finally { loading.value = false }
}

const searchApis = useDebounceFn(async () => {
  try {
    const params = { project_id: projectStore.current, search: genSearch.value, limit: 60 }
    const res = await apiApi.list(params)
    const all = res.items || []
    // 多接口/复杂场景优先展示已审核应用的 API，同时兼容旧 done 状态；不足时保留其他 API 供用户手动选择。
    apiPickList.value = genType.value === 'single'
      ? all.slice(0, 30)
      : [
          ...all.filter(a => ['applied', 'done'].includes(a.analysis_status)),
          ...all.filter(a => !['applied', 'done'].includes(a.analysis_status)),
        ].slice(0, 30)
  } catch (e) {
    toast.error(e.message || t('scenarios.search_failed'))
  }
}, 300)

// 场景类型筛选变更：重置页码并重新加载
function onFilterChange() { page.value = 1; load() }

// 从场景添加至数据工厂/巡检：通过 query param 跳转并预填表单
function handleAddTo(row, target) {
  const path = target === 'factory' ? '/factory' : '/monitor'
  router.push({ path, query: { scenario_id: row.id } })
}
// 搜索关键词变更：防抖后重置页码并加载
const onSearchInput = useDebounceFn(() => { page.value = 1; load() }, 400)
// 多选变更：同步选中的 ID 列表
function onSelectionChange(rows) { selectedIds.value = rows.map(r => r.id) }

// 批量执行：依次触发选中场景的执行，不阻塞列表
// P1-9: 批量运行 —— 弹环境选择（与单场景运行一致），确认后调 confirmRunWithEnv
async function batchRun() {
  runEnvScenarioId.value = null  // null 表示批量
  // 从 localStorage 读取全局选中的环境 ID（不重置，保持与全局环境选择器同步）
  runEnvId.value = localStorage.getItem('apipulse-env') || ''
  try {
    const res = await environmentApi.list(projectStore.current)
    environments.value = res.items || []
  } catch { environments.value = [] }
  runEnvModal.value = true
}

// 批量删除：确认后调用后端批量删除接口
async function batchDelete() {
  batchDeleting.value = true
  try {
    // ElMessageBox.confirm：确定→resolve，取消→reject('cancel')，关闭→reject('close')
    await ElMessageBox.confirm(t('scenarios.confirm_batch_delete', { n: selectedIds.value.length }), t('scenarios.confirm_delete_title'), { type: 'warning' })
    // 将响应式数组转为纯数组传递，避免 Vue Proxy 序列化问题
    const r = await scenarioApi.batchDelete([...selectedIds.value])
    // 检查后端实际删除数量，避免 deleted_count=0 时仍提示成功
    toast.success(t('scenarios.batch_delete_done', { count: r.deleted_count }))
    selectedIds.value = []
    load()
  } catch (e) {
    // 取消/关闭弹窗不提示；其他错误统一提示
    if (e !== 'cancel' && e !== 'close') {
      toast.error(e?.message || t('scenarios.delete_failed'))
    }
  }
  finally { batchDeleting.value = false }
}

// 导出：将选中的场景导出为 JSON 文件下载
async function exportScenarios() {
  exporting.value = true
  try {
    const blob = await scenarioApi.export(selectedIds.value)
    // 创建下载链接：blob 数据转为 URL 后触发点击下载
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `scenarios_${new Date().toISOString().slice(0, 10)}.json`
    a.click()
    URL.revokeObjectURL(url)
    toast.success(t('scenarios.export_done'))
  } catch (e) { toast.error(e.message) }
  finally { exporting.value = false }
}

// 导入：触发隐藏的文件选择器
function importScenarios() {
  importFileInput.value?.click()
}

// 处理导入文件：读取 JSON 文件并调用后端导入接口
async function onImportFile(e) {
  const file = e.target.files?.[0]
  if (!file) return
  importing.value = true
  try {
    const text = await file.text()
    const data = JSON.parse(text)
    // 后端期望 { scenarios: [...] } 或直接的场景数组
    const res = await scenarioApi.import({ scenarios: Array.isArray(data) ? data : (data.scenarios || [data]), project_id: projectStore.current })
    toast.success(`导入完成：新增 ${res.imported || 0}，失败 ${res.failed || 0}`)
    load()
  } catch (e) {
    toast.error(e.message || t('scenarios.import_failed'))
  } finally {
    importing.value = false
    // 重置文件输入，允许重复导入同一文件
    if (importFileInput.value) importFileInput.value.value = ''
  }
}

async function generateScenarios() {
  generating.value = true
  try {
    const res = await scenarioApi.generate(selectedApiIds.value, projectStore.current, genType.value)
    // 后端已改为异步：返回 {queued: true}，Worker 完成后通过 WS 广播 scenario_done
    if (res.queued) {
      toast.info(`${t('scenarios.gen_queued')} ${res.job_id ? `Job: ${res.job_id}` : ''}`)
    } else {
      toast.success(t('scenarios.gen_done', { count: res.generation_ids?.length ?? 0 }))
    }
    showGenModal.value = false
    selectedApiIds.value = []
  } catch (e) { toast.error(e.message) }
  finally { generating.value = false }
}

// ── WebSocket 订阅：场景异步生成完成后自动刷新 ──
function onScenarioWsMessage(msg) {
  if (!msg) return
  if (msg.project_id && msg.project_id !== projectStore.current) return
  if (msg.type === 'scenario_generation') {
    toast.info(msg.status === 'retry' ? `场景生成重试中：${msg.error || ''}` : '场景生成运行中')
  }
  if (msg.type === 'scenario_batch') {
    batchJob.value = { ...batchJob.value, job_id: msg.job_id || '', status: msg.status || '', total: msg.total || 0 }
    if (msg.status === 'done') {
      toast.success(`批量运行完成：${msg.total || selectedIds.value.length} 个场景`)
      batchRunning.value = false
      load()
    }
    if (msg.status === 'failed') {
      toast.error(msg.error || '批量运行失败')
      batchRunning.value = false
    }
  }
  if (msg.type === 'scenario_batch_progress' && msg.scenario_id) {
    batchJob.value.scenarioStatus = { ...batchJob.value.scenarioStatus, [msg.scenario_id]: msg.status }
  }
  // 场景生成完成：刷新列表并提示成功
  if (msg.type === 'scenario_done') {
    toast.successAction(
      t('scenarios.gen_ws_done', { count: msg.generation_ids?.length ?? 0 }),
      t('scenarios.go_review'),
      () => router.push('/generations?type=scenario&status=pending_review')
    )
    load()
  }
  // 场景生成失败：提示错误原因
  if (msg.type === 'scenario_failed') {
    toast.error(t('scenarios.gen_ws_failed', { error: msg.error || '' }))
  }
}

async function createScenario() {
  creating.value = true
  try {
    await scenarioApi.create({ ...newScenario.value, project_id: projectStore.current, steps: [] })
    toast.success(t('scenarios.created'))
    showCreateModal.value = false
    newScenario.value = { name: '', description: '', owner: '' }
    load()
  } catch (e) { toast.error(e.message) }
  finally { creating.value = false }
}

// P1-8: 列表运行 —— 弹环境选择（可选），执行后刷新列表（状态流转显示 running→done/failed）
async function runScenario(s) {
  runEnvScenarioId.value = s.id
  // 从 localStorage 读取全局选中的环境 ID（不重置，保持与全局环境选择器同步）
  runEnvId.value = localStorage.getItem('apipulse-env') || ''
  // 加载环境列表供选择
  try {
    const res = await environmentApi.list(projectStore.current)
    environments.value = res.items || []
  } catch { environments.value = [] }
  runEnvModal.value = true
}

// P1-8/9: 确认执行（单场景或批量），带环境选择
async function confirmRunWithEnv() {
  const envId = runEnvId.value || undefined
  const body = envId ? { environment_id: envId } : {}
  if (runEnvScenarioId.value) {
    // 单场景执行
    runningId.value = runEnvScenarioId.value
    runEnvModal.value = false
    try {
      // P0-2: run 端点现在异步返回 exec_id
      const res = await scenarioApi.run(runEnvScenarioId.value, body)
      if (res.exec_id) {
        // 异步执行：跳转到场景详情页查看 DAG 实时进度
        toast.success(t('scenarios.run_started', '场景已开始执行'))
        router.push(`/scenarios/${runEnvScenarioId.value}`)
      }
    } catch (e) { toast.error(e.message) }
    finally { runningId.value = null; runEnvScenarioId.value = null }
  } else if (selectedIds.value.length) {
    // 批量执行
    batchRunning.value = true
    runEnvModal.value = false
    try {
      const res = await scenarioApi.batchRun(selectedIds.value, { ...body, async: true })
      batchJob.value = { job_id: res.job_id || '', status: 'queued', total: res.total || selectedIds.value.length, scenarioStatus: Object.fromEntries(selectedIds.value.map(id => [id, 'queued'])) }
      toast.info(`批量运行已排队：${res.job_id || ''}`)
    } catch (e) { toast.error(e.message) }
  }
}

async function removeScenario(s) {
  try {
    await ElMessageBox.confirm(t('scenarios.confirm_delete', { name: s.name }), t('scenarios.confirm_delete_title'), { type: 'warning' })
    await scenarioApi.remove(s.id)
    toast.success(t('scenarios.deleted'))
    load()
  } catch (e) {
    // ElMessageBox reject 可能返回 'cancel'(按钮) 或 'close'(X关闭)，均忽略
    if (e !== 'cancel' && e !== 'close' && e.message) toast.error(e.message)
  }
}

// 打开弹窗时保留用户上次选择的 genType，不再强制重置为 multi
function subscribeScenarioWs() {
  if (scenarioWs?.terminate) scenarioWs.terminate()
  scenarioWs = openWs(`/ai-analysis?project_id=${encodeURIComponent(projectStore.current || 'default')}`, onScenarioWsMessage)
}

watch(showGenModal, v => { if (v) { genSearch.value = ''; selectedApiIds.value = []; searchApis() } })
watch(genType, () => { selectedApiIds.value = []; searchApis() })
watch(() => projectStore.current, () => {
  page.value = 1
  selectedIds.value = []
  batchRunning.value = false
  batchJob.value = { job_id: '', status: '', total: 0, scenarioStatus: {} }
  subscribeScenarioWs()
  load()
})
onMounted(() => {
  load()
  subscribeScenarioWs()
})
onUnmounted(() => {
  if (scenarioWs?.terminate) scenarioWs.terminate()
})
</script>

<style scoped>
.api-pick-list { max-height: 280px; overflow-y: auto; border: 1px solid var(--border); border-radius: var(--radius); }
.api-pick-item {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; cursor: pointer; border-bottom: 1px solid var(--border);
  font-size: 12px;
}
.api-pick-item:last-child { border-bottom: none; }
.api-pick-item:hover { background: var(--bg-hover); }
.api-pick-item input { flex-shrink: 0; }
.scenario-next-actions { display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
</style>
