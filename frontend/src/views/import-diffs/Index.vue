<template>
  <div class="page">
    <div class="page-header">
      <div>
        <div class="page-title">{{ $t('importDiffs.title') }}</div>
        <div class="page-subtitle">{{ $t('importDiffs.subtitle', { total }) }}</div>
      </div>
    </div>

    <!-- 筛选栏：按状态、严重程度过滤 -->
    <div class="filter-bar">
      <el-select v-model="filter.status" :placeholder="$t('importDiffs.filterStatus')" size="small" style="width:140px" clearable @change="load" @clear="load">
        <el-option :label="$t('importDiffs.allStatus')" value="" />
        <el-option :label="$t('importDiffs.statusPending')" value="pending" />
        <el-option :label="$t('importDiffs.statusConfirmed')" value="confirmed" />
        <el-option :label="$t('importDiffs.statusAutoFixed')" value="auto_fixed" />
        <el-option :label="$t('importDiffs.statusDismissed')" value="dismissed" />
      </el-select>
      <el-select v-model="filter.severity" :placeholder="$t('importDiffs.filterSeverity')" size="small" style="width:140px" clearable @change="load" @clear="load">
        <el-option :label="$t('importDiffs.allSeverity')" value="" />
        <el-option :label="$t('importDiffs.severityLow')" value="low" />
        <el-option :label="$t('importDiffs.severityMedium')" value="medium" />
        <el-option :label="$t('importDiffs.severityHigh')" value="high" />
        <el-option :label="$t('importDiffs.severityCritical')" value="critical" />
      </el-select>
      <el-button v-if="anyFilter" size="small" @click="clearFilters">{{ $t('apis.clear') }}</el-button>
    </div>

    <div class="page-body" style="padding-top:0">

      <el-card style="padding:0">
      <el-table
        :data="items"
        v-loading="loading"
        row-key="id"
        @row-click="openDetail"
        style="cursor:pointer"
        :empty-text="$t('importDiffs.empty')"
      >
        <!-- API 路径 -->
        <el-table-column :label="$t('importDiffs.colPath')" min-width="200">
          <template #default="{ row }">
            <span class="mono" style="font-size:13px">{{ row.api_path }}</span>
          </template>
        </el-table-column>
        <!-- HTTP 方法 -->
        <el-table-column :label="$t('importDiffs.colMethod')" width="80">
          <template #default="{ row }">
            <el-tag :type="methodTagType(row.method)" size="small">{{ row.method }}</el-tag>
          </template>
        </el-table-column>
        <!-- 差异字段数 -->
        <el-table-column :label="$t('importDiffs.colFieldCount')" width="100">
          <template #default="{ row }">
            <span class="mono text-2">{{ row.fields_diff?.length || 0 }}</span>
          </template>
        </el-table-column>
        <!-- 状态 -->
        <el-table-column :label="$t('importDiffs.colStatus')" width="110">
          <template #default="{ row }">
            <status-tag :status="row.status" />
          </template>
        </el-table-column>
        <!-- 根因 -->
        <el-table-column :label="$t('importDiffs.colRootCause')" width="140">
          <template #default="{ row }">
            <span class="text-2" style="font-size:12px">{{ row.root_cause ? $t(`importDiffs.rootCause_${row.root_cause}`) : '-' }}</span>
          </template>
        </el-table-column>
        <!-- 严重程度 -->
        <el-table-column :label="$t('importDiffs.colSeverity')" width="90">
          <template #default="{ row }">
            <severity-tag v-if="row.severity" :severity="row.severity" />
            <span v-else class="text-3">—</span>
          </template>
        </el-table-column>
        <!-- 创建时间 -->
        <el-table-column :label="$t('importDiffs.colCreated')" width="150">
          <template #default="{ row }">
            <span class="text-2">{{ fmt.fromNow(row.created_at) }}</span>
          </template>
        </el-table-column>
        <!-- 操作：确认/忽略未处理的差异 -->
        <el-table-column :label="$t('importDiffs.colActions')" width="140" fixed="right">
          <template #default="{ row }">
            <template v-if="row.status === 'pending' || row.status === 'confirmed'">
              <el-button size="small" type="primary" plain @click.stop="confirmDiff(row)">{{ $t('importDiffs.confirm_action') }}</el-button>
              <el-button size="small" type="danger" plain @click.stop="dismissDiff(row)">{{ $t('importDiffs.dismiss') }}</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>

      <AppPagination v-model:page="page" :page-size="pageSize" :total="total" @page-change="load" />
      </el-card>
    </div>

    <!-- 详情弹窗：含字段差异与文档对比两 Tab 视图 -->
    <el-dialog
      v-model="showDetail"
      :title="$t('importDiffs.detailTitle')"
      width="860px"
      :close-on-click-modal="true"
      @closed="activeTab = 'fields'; comparisonData = null"
    >
      <template v-if="current">
        <!-- Header：基础信息 + 关联 API 链接 -->
        <div class="detail-header">
          <div class="detail-row">
            <span class="detail-label">{{ $t('importDiffs.colPath') }}</span>
            <span class="mono">{{ current.api_path }}</span>
            <el-tag :type="methodTagType(current.method)" size="small" style="margin-left:8px">{{ current.method }}</el-tag>
          </div>
          <div class="detail-row">
            <span class="detail-label">{{ $t('importDiffs.colStatus') }}</span>
            <status-tag :status="current.status" />
            <span class="detail-label" style="margin-left:16px">{{ $t('importDiffs.colSeverity') }}</span>
            <severity-tag v-if="current.severity" :severity="current.severity" />
            <span v-else class="text-3">—</span>
          </div>
          <div v-if="current.root_cause" class="detail-row">
            <span class="detail-label">{{ $t('importDiffs.colRootCause') }}</span>
            <span class="text-2">{{ $t(`importDiffs.rootCause_${current.root_cause}`) }}</span>
          </div>
          <!-- 关联 API：可点击跳转到对应 API 详情页 -->
          <div class="detail-row">
            <span class="detail-label">关联 API</span>
            <el-button size="small" text type="primary" @click="openApi(current.existing_api_id)">{{ $t('importDiffs.viewExistingApi') }}</el-button>
            <el-button size="small" text type="success" @click="openApi(current.new_api_id)">{{ $t('importDiffs.viewNewApi') }}</el-button>
          </div>
          <!-- AI 评估 reasoning -->
          <div v-if="current.ai_evaluation?.reasoning" class="detail-reasoning">
            <div class="detail-label">{{ $t('importDiffs.aiReasoning') }}</div>
            <p class="text-2" style="margin:4px 0 0;font-size:12px;line-height:1.6">{{ current.ai_evaluation.reasoning }}</p>
          </div>
        </div>
        <el-divider />

        <!-- Tab 切换：字段差异 | 文档对比 -->
        <el-tabs v-model="activeTab" @tab-change="onTabChange">
          <!-- Tab 1：字段差异列表 -->
          <el-tab-pane :label="`${$t('importDiffs.tabFieldDiffs')} (${current.fields_diff?.length || 0})`" name="fields">
            <el-table :data="current.fields_diff || []" size="small" max-height="360">
              <el-table-column :label="$t('importDiffs.fieldPath')" prop="field_path" min-width="160">
                <template #default="{ row }">
                  <span class="mono" style="font-size:11px">{{ row.field_path }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('importDiffs.fieldLocation')" prop="location" width="90">
                <template #default="{ row }">
                  <el-tag :type="row.location === 'request' ? 'warning' : 'success'" size="small">{{ row.location }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column :label="$t('importDiffs.fieldDiffType')" width="120">
                <template #default="{ row }">
                  <span class="text-2" style="font-size:12px">{{ $t(`importDiffs.diffType_${row.difference_type}`) }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('importDiffs.fieldOldValue')" width="100">
                <template #default="{ row }">
                  <span class="mono text-2" style="font-size:11px">{{ formatValue(row.existing_value) }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('importDiffs.fieldNewValue')" width="100">
                <template #default="{ row }">
                  <span class="mono" style="font-size:11px;color:var(--accent)">{{ formatValue(row.new_value) }}</span>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>

          <!-- Tab 2：新旧 API 文档对比视图 -->
          <el-tab-pane :label="$t('importDiffs.tabDocComparison')" name="comparison" :disabled="!comparisonData">
            <div v-if="loadingComparison" class="comparison-loading">
              <span class="text-2">{{ $t('importDiffs.loadingComparison') }}</span>
            </div>
            <div v-else-if="comparisonData" class="comparison-grid">
              <!-- 旧 API（左） -->
              <div class="comparison-panel">
                <div class="comparison-panel-header">
                  <el-tag size="small" type="warning">{{ $t('importDiffs.existingApi') }}</el-tag>
                  <span class="text-2" style="font-size:12px;margin-left:6px">{{ comparisonData.old_api?.request?.path || '-' }}</span>
                </div>
                <div class="comparison-panel-body">
                  <pre class="mono" style="font-size:11px;white-space:pre-wrap;word-break:break-all;margin:0">{{ JSON.stringify(comparisonData.old_api?.doc || comparisonData.old_api, null, 2) }}</pre>
                </div>
              </div>
              <!-- 新 API（右） -->
              <div class="comparison-panel">
                <div class="comparison-panel-header">
                  <el-tag size="small" type="success">{{ $t('importDiffs.newApi') }}</el-tag>
                  <span class="text-2" style="font-size:12px;margin-left:6px">{{ comparisonData.new_api?.request?.path || '-' }}</span>
                </div>
                <div class="comparison-panel-body">
                  <pre class="mono" style="font-size:11px;white-space:pre-wrap;word-break:break-all;margin:0">{{ JSON.stringify(comparisonData.new_api?.doc || comparisonData.new_api, null, 2) }}</pre>
                </div>
              </div>
            </div>
          </el-tab-pane>
        </el-tabs>

        <!-- 底部操作：审核修复 + 确认/忽略 -->
        <div class="detail-actions">
          <!-- ai_doc_error 且已生成修复版本 → 可跳转审核 -->
          <el-button
            v-if="current.ai_evaluation?.generation_id"
            type="primary"
            size="small"
            @click="openGeneration(current.ai_evaluation.generation_id)"
          >{{ $t('importDiffs.goReviewFix') }}</el-button>
          <!-- 未处理的差异记录支持手动确认或忽略 -->
          <template v-if="canResolve">
            <el-button type="primary" plain @click="confirmCurrent">{{ $t('importDiffs.confirm_action') }}</el-button>
            <el-button type="danger" plain @click="dismissCurrent">{{ $t('importDiffs.dismiss') }}</el-button>
          </template>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch, h } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { importDiffApi } from '@/api'
import { useProjectStore, useToastStore } from '@/stores'
import { fmt } from '@/utils'
import AppPagination from '@/components/AppPagination.vue'
import type { ImportDiffRecord } from '@/types'
import { ElTag } from 'element-plus'

const { t } = useI18n()
const router = useRouter()
const toast = useToastStore()
const projectStore = useProjectStore()
const items    = ref<ImportDiffRecord[]>([])
const total    = ref(0)
const loading  = ref(false)
const page     = ref(1)
const pageSize = 20
const filter   = ref({ status: '', severity: '' })

const anyFilter = computed(() => filter.value.status !== '' || filter.value.severity !== '')

async function load() {
  loading.value = true
  try {
    const params: Record<string, any> = {
      project_id: projectStore.current,
      skip: (page.value - 1) * pageSize,
      limit: pageSize,
    }
    if (filter.value.status) params.status = filter.value.status
    if (filter.value.severity) params.severity = filter.value.severity
    const res = await importDiffApi.list(params)
    items.value = (res.items || []) as ImportDiffRecord[]
    total.value = res.total || 0
  } catch (e: any) {
    toast.error(e.message || t('toast.load_failed'))
  } finally { loading.value = false }
}

function clearFilters() {
  filter.value = { status: '', severity: '' }
  page.value = 1
  load()
}

// ── HTTP 方法 tag 颜色 ──
function methodTagType(method: string): string {
  const m = (method || '').toUpperCase()
  if (m === 'GET') return 'success'
  if (m === 'POST') return 'primary'
  if (m === 'PUT' || m === 'PATCH') return 'warning'
  if (m === 'DELETE') return 'danger'
  return 'info'
}

// ── 格式化字段值 ──
function formatValue(v: any): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  if (typeof v === 'object') return JSON.stringify(v).slice(0, 60)
  return String(v).slice(0, 60)
}

// ── 状态 tag 组件 ──
// 内联渲染函数，避免独立组件的样板代码
const StatusTag = {
  name: 'StatusTag',
  props: { status: String },
  setup(props: { status: string }) {
    const map: Record<string, { text: string; type: string }> = {
      pending:    { text: t('importDiffs.statusPending'),    type: 'info' },
      confirmed:  { text: t('importDiffs.statusConfirmed'),  type: 'warning' },
      auto_fixed: { text: t('importDiffs.statusAutoFixed'),  type: 'success' },
      dismissed:  { text: t('importDiffs.statusDismissed'),  type: '' },
    }
    const info = map[props.status] || { text: props.status || '-', type: 'info' }
    return () => h(ElTag, { type: info.type, size: 'small' }, info.text)
  },
}

const SeverityTag = {
  name: 'SeverityTag',
  props: { severity: String },
  setup(props: { severity: string }) {
    const map: Record<string, { text: string; type: string }> = {
      low:      { text: t('importDiffs.severityLow'),      type: 'info' },
      medium:   { text: t('importDiffs.severityMedium'),   type: 'warning' },
      high:     { text: t('importDiffs.severityHigh'),     type: 'danger' },
      critical: { text: t('importDiffs.severityCritical'), type: 'danger' },
    }
    const info = map[props.severity] || { text: props.severity || '-', type: 'info' }
    return () => h(ElTag, { type: info.type, size: 'small', effect: 'dark' }, info.text)
  },
}

// ── 详情弹窗 ──
const showDetail = ref(false)
const current = ref<ImportDiffRecord | null>(null)
const activeTab = ref('fields')         // 'fields' | 'comparison'
const comparisonData = ref<Record<string, any> | null>(null)
const loadingComparison = ref(false)

// 当前记录是否可手动处理（pending/confirmed 状态）
const canResolve = computed(() => {
  const s = current.value?.status
  return s === 'pending' || s === 'confirmed'
})

function openDetail(row: ImportDiffRecord) {
  current.value = row
  activeTab.value = 'fields'
  comparisonData.value = null
  showDetail.value = true
}

// 切换 Tab 时懒加载对比数据
async function onTabChange(name: string) {
  if (name === 'comparison' && !comparisonData.value && current.value) {
    await loadComparison(current.value.id)
  }
}

// 加载新旧 API 文档对比数据（调用已有的 /comparison 端点）
async function loadComparison(diffId: string) {
  loadingComparison.value = true
  try {
    const res = await importDiffApi.comparison(diffId)
    comparisonData.value = res
  } catch (e: any) {
    toast.error(e.message || t('toast.load_failed'))
  } finally {
    loadingComparison.value = false
  }
}

// 跳转到 API 详情页（apis 模块）
function openApi(apiId: string) {
  if (!apiId) return
  router.push({ path: '/apis', query: { id: apiId } })
}

// 跳转到生成审核页
function openGeneration(genId: string) {
  if (!genId) return
  router.push({ path: '/generations', query: { id: genId } })
}

// ── 处理操作：确认 / 忽略 ──
async function resolveDiff(row: ImportDiffRecord, action: 'confirm' | 'dismiss') {
  loading.value = true
  try {
    await importDiffApi.resolve(row.id, action)
    const msgKey = action === 'confirm' ? 'importDiffs.confirmed' : 'importDiffs.dismissed'
    toast.success(t(msgKey))
    await load()
  } catch (e: any) {
    toast.error(e.message || t('toast.load_failed'))
  } finally { loading.value = false }
}

async function confirmDiff(row: ImportDiffRecord) {
  await resolveDiff(row, 'confirm')
}

async function dismissDiff(row: ImportDiffRecord) {
  await resolveDiff(row, 'dismiss')
}

async function confirmCurrent() {
  if (!current.value) return
  await confirmDiff(current.value)
  showDetail.value = false
}

async function dismissCurrent() {
  if (!current.value) return
  await dismissDiff(current.value)
  showDetail.value = false
}

watch(() => projectStore.current, () => { page.value = 1; load() })
onMounted(load)
</script>

<style scoped>
.filter-bar {
  display: flex; gap: 10px; padding: 12px 24px; border-bottom: 0px solid var(--border);
  align-items: center;
}

.detail-header {
  display: flex; flex-direction: column; gap: 10px;
}
.detail-row {
  display: flex; align-items: center; gap: 8px;
}
.detail-label {
  font-size: 12px; color: var(--text-3); font-weight: 600; min-width: 60px;
}
.detail-reasoning {
  margin-top: 4px; padding: 10px; background: var(--bg-3); border-radius: var(--radius); border: 1px solid var(--border-2);
}
.detail-actions {
  display: flex; gap: 8px; margin-top: 16px; justify-content: flex-end; align-items: center;
}
/* 文档对比：左右双栏网格布局 */
.comparison-loading {
  display: flex; align-items: center; justify-content: center; padding: 32px;
}
.comparison-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 12px; max-height: 400px;
}
.comparison-panel {
  border: 1px solid var(--border-2); border-radius: var(--radius); overflow: hidden;
  display: flex; flex-direction: column;
}
.comparison-panel-header {
  padding: 8px 10px; background: var(--bg-3); border-bottom: 1px solid var(--border-2);
  display: flex; align-items: center;
}
.comparison-panel-body {
  padding: 10px; overflow-y: auto; flex: 1;
  background: var(--bg-1);
}
</style>
