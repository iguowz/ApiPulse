<template>
  <div class="page">
    <div class="page-header">
      <div>
        <div class="page-title">{{ $t('apis.title') }}</div>
        <div class="page-subtitle">{{ $t('apis.total', { total }) }}</div>
      </div>
      <div class="flex items-center gap-8">
        <!-- HAR 导入过滤：域名 + URL 关键字 -->
        <el-input
          v-model="harFilterHost"
          :placeholder="$t('apis.filter_host')"
          class="filter-input"
          :title="$t('apis.filter_host')"
        />
        <el-input
          v-model="harFilterUrl"
          :placeholder="$t('apis.filter_url')"
          class="filter-input"
          :title="$t('apis.filter_url')"
        />
        <label class="btn btn-primary btn-sm" style="cursor:pointer">
          <input type="file" accept=".har" @change="onHarUpload" hidden :disabled="uploading" />
          {{ uploading ? $t('apis.parsing') : $t('apis.upload_har') }}
        </label>
        <el-button @click="showCaptureModal = true">◉ {{ $t('apis.capture') }}</el-button>
        <el-button @click="showCurlModal = true">{{ $t('apis.import_curl') }}</el-button>
        <label class="btn btn-sm" style="cursor:pointer">
          <input type="file" accept=".json,application/json" @change="onOpenApiUpload" hidden :disabled="openApiImporting" />
          {{ openApiImporting ? $t('common.loading') : $t('apis.import_openapi') }}
        </label>
        <!-- P1-5b: 手动新建接口（此前只能导入，无法从零定义） -->
        <el-button type="primary" @click="openCreateModal">{{ $t('apis.new_api') }}</el-button>
        <el-button v-if="selected.size" type="warning" @click="batchAnalyze" :disabled="batchAnalyzing">
          {{ batchAnalyzing ? '…' : $t('apis.batch_analyze', { count: selected.size }) }}
        </el-button>
        <el-button v-if="selected.size" @click="exportSelectedOpenApi" :disabled="openApiExporting">
          {{ openApiExporting ? '…' : $t('apis.export_openapi', { count: selected.size }) }}
        </el-button>
        <el-button v-if="selected.size" type="danger" @click="batchDelete">
          {{ $t('apis.delete_selected', { count: selected.size }) }}
        </el-button>
      </div>
    </div>

    <!-- Filters -->
    <div class="filters">
      <el-input
        v-model="search"
        :placeholder="$t('apis.search_path')"
        :prefix-icon="Search"
        @input="onSearch"
        class="search-input"
        clearable
      />
      <el-select v-model="filterAnalysisStatus" @change="load" :placeholder="$t('apis.all_status')" class="filter-select" clearable>
        <el-option :label="$t('apis.status_idle')" value="idle" />
        <el-option :label="$t('apis.status_queued')" value="queued" />
        <el-option :label="$t('apis.status_running')" value="running" />
        <el-option :label="$t('apis.status_pending_review')" value="pending_review" />
        <el-option :label="$t('apis.status_applied')" value="applied" />
        <el-option :label="$t('apis.status_failed')" value="failed" />
      </el-select>
      <el-select v-model="filterMethod" @change="load" :placeholder="$t('apis.all_method')" class="filter-select" clearable>
        <el-option v-for="m in ['GET','POST','PUT','PATCH','DELETE']" :key="m" :label="m" :value="m" />
      </el-select>
      <!-- 来源过滤 -->
      <el-select v-model="filterSource" @change="load" :placeholder="$t('apis.filter_source')" class="filter-select" clearable>
        <!-- source_har 字段在 HAR 导入时存储文件名（如 api.har），mitmproxy 抓包时存储 mitmproxy://{timestamp} -->
        <el-option :label="$t('apis.source_har')" value=".har" />
        <el-option :label="$t('apis.source_mitmproxy')" value="mitmproxy://" />
      </el-select>
      <!-- 状态码过滤 -->
      <el-select v-model="filterStatusCode" @change="load" :placeholder="$t('apis.filter_status_code')" class="filter-select" clearable>
        <el-option v-for="c in ['2xx','3xx','4xx','5xx']" :key="c" :label="c" :value="c" />
      </el-select>
      <!-- 排序 -->
      <el-select v-model="filterSortBy" @change="load" class="filter-select">
        <el-option :label="$t('apis.sort_newest')" value="created_at_desc" />
        <el-option :label="$t('apis.sort_oldest')" value="created_at_asc" />
        <el-option :label="$t('apis.sort_name_asc')" value="name_asc" />
      </el-select>
      <el-button v-if="anyFilter" @click="clearFilters">✕ {{ $t('apis.clear') }}</el-button>
    </div>

    <!-- div class="page-body" style="padding-top:0" -->
    <div class="page-body" style="padding-top:0">
      <!-- Table -->
      <el-card style="padding:0">
        <el-table
          :data="items"
          @row-click="(row) => router.push(`/apis/${row.id}`)"
          :row-style="() => ({ cursor: 'pointer' })"
          v-loading="loading"
          :empty-text="$t('apis.no_apis')"
          @selection-change="onSelectionChange"
          ref="tableRef"
          size="small"
        >
          <el-table-column type="selection" width="36" />
          <el-table-column :label="$t('apis.col_method')" width="80">
            <template #default="{ row: api }">
              <span :class="methodClass(api.request?.method)">{{ api.request?.method }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('apis.col_path')" min-width="200">
            <template #default="{ row: api }">
              <span class="mono truncate" style="font-size:12px" :title="api.request?.path">{{ api.request?.path }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('common.name')" min-width="140">
            <template #default="{ row: api }">
              <span class="text-2 truncate">{{ api.doc?.summary || api.name }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('api_detail.status_code')" width="80">
            <template #default="{ row: api }">
              <el-tag :type="statusTagType(api.response?.status_code)" size="small">{{ api.response?.status_code }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="$t('apis.col_ai')" width="80">
            <template #default="{ row: api }">
              <AnalysisStatusTag :status="api.analysis_status" :error="api.analysis_error" prefix="apis" />
            </template>
          </el-table-column>
          <el-table-column :label="$t('apis.col_quality')" width="96">
            <template #default="{ row: api }">
              <el-tooltip
                :content="$t('apis.quality_tooltip', { risk: $t('quality.risk_' + (api.quality?.risk_level || 'critical')) })"
                placement="top"
              >
                <div class="quality-cell">
                  <el-progress
                    type="circle"
                    :percentage="api.quality?.score || 0"
                    :width="34"
                    :stroke-width="4"
                    :color="qualityColor(api.quality?.risk_level)"
                    :show-text="false"
                  />
                  <span class="quality-score">{{ api.quality?.score || 0 }}</span>
                </div>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column :label="$t('api_detail.asserts')" width="60">
            <template #default="{ row: api }">
              <span class="text-2 mono">{{ api.asserts?.length || 0 }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('apis.source_col')" width="90">
            <template #default="{ row: api }">
              <span class="text-3 mono" style="font-size:11px">{{ api.source_har || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('apis.col_created_at')" width="150">
            <template #default="{ row: api }">
              <span class="text-2" style="font-size:11px">{{ fmt.time(api.created_at) }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('apis.col_updated_at')" width="150">
            <template #default="{ row: api }">
              <span class="text-2" style="font-size:11px">{{ fmt.time(api.updated_at) }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('common.actions')" width="100">
            <template #default="{ row: api }">
              <div class="flex items-center gap-8" @click.stop>
                <el-button size="small" @click="runApi(api)" :disabled="runningId === api.id">
                  {{ runningId === api.id ? '…' : '▶' }}
                </el-button>
                <el-button size="small" @click="analyzeApi(api)" :disabled="analyzingId === api.id" :title="$t('apis.analyze')">
                  {{ analyzingId === api.id ? '…' : '✦' }}
                </el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>
        <AppPagination v-model:page="page" :page-size="pageSize" :total="total" @page-change="load" />
      </el-card>

      <!-- Run result inline：按 api.id 存储，显示最近一次执行结果 -->
      <div v-if="activeResult" class="run-result" :class="activeResult.passed ? 'run-ok' : 'run-fail'">
        <div class="run-result-header">
          <ResultTag :passed="activeResult.passed" />
          <span class="mono text-2" style="font-size:11px">{{ activeResultApiPath }}</span>
          <span class="mono text-2" style="font-size:12px">{{ activeResult.steps?.[0]?.response_received?.status_code }} · {{ fmt.duration(activeResult.duration_ms) }}</span>
          <el-button size="small" @click="activeResultApiId = null">✕</el-button>
        </div>
        <pre class="code-block" style="max-height:200px;overflow:auto">{{ jsonPretty(activeResult.steps?.[0]?.response_received?.body) }}</pre>
      </div>
    </div>

    <!-- 抓包 modal -->
    <el-dialog v-model="showCaptureModal" :title="$t('apis.capture_title')" width="680px" @open="onCaptureModalOpen" @close="onCaptureModalClose">
      <!-- 抓包状态 -->
      <div class="capture-status-bar">
        <div class="capture-stat">
          <span class="capture-stat-label">{{ $t('apis.capture_status') }}</span>
          <el-tag :type="captureState.enabled ? 'success' : 'info'">
            {{ captureState.enabled ? $t('apis.capture_running') : $t('apis.capture_paused') }}
          </el-tag>
        </div>
        <div class="capture-stat">
          <span class="capture-stat-label">{{ $t('apis.capture_ingested') }}</span>
          <span class="mono" style="font-weight:600">{{ $t('apis.capture_count', { count: captureState.ingested_count || 0 }) }}</span>
        </div>
        <div class="capture-stat">
          <span class="capture-stat-label">{{ $t('apis.capture_last') }}</span>
          <span class="text-2 mono" style="font-size:11px">{{ captureState.last_ingest_at ? fmt.fromNow(captureState.last_ingest_at) : '—' }}</span>
        </div>
        <!-- 抓包过滤：域名 + URL 关键字 -->
        <el-input
          v-model="captureFilterHost"
          :placeholder="$t('apis.filter_host_placeholder')"
          :title="$t('apis.filter_host_title')"
          @change="updateCaptureFilter"
          style="width:130px"
          size="small"
        />
        <el-input
          v-model="captureFilterUrl"
          :placeholder="$t('apis.filter_url_placeholder')"
          :title="$t('apis.filter_url_title')"
          @change="updateCaptureFilter"
          style="width:130px"
          size="small"
        />
        <!-- 抓包开关按钮 -->
        <el-button
          :type="captureState.enabled ? 'danger' : 'primary'"
          size="small"
          @click="toggleCapture"
          :disabled="captureLoading"
        >
          {{ captureState.enabled ? $t('apis.capture_pause') : $t('apis.capture_start') }}
        </el-button>
        <el-button size="small" @click="refreshCaptureStatus" :disabled="captureLoading">
          {{ captureLoading ? '…' : $t('apis.capture_refresh') }}
        </el-button>
      </div>

      <!-- 使用说明 -->
      <div class="capture-instructions">
        <div class="form-label" style="margin-bottom:8px">{{ $t('apis.capture_usage') }}</div>
        <div class="capture-step">
          <span class="capture-step-num">1</span>
          <span>{{ $t('apis.capture_step1') }}<code class="inline-code">{{ $t('apis.capture_step1_cmd') }}</code></span>
        </div>
        <div class="capture-step">
          <span class="capture-step-num">2</span>
          <span>{{ $t('apis.capture_step2') }}</span>
        </div>
        <pre class="code-block capture-cmd"><code>mitmproxy -s mitmproxy_capture/capture_addon.py \
  --set api_pulse_url=http://localhost:8000 \
  --set project_id={{ projectStore.current }}</code></pre>
        <div class="capture-step">
          <span class="capture-step-num">3</span>
          <span>{{ $t('apis.capture_step3') }} <code class="inline-code">localhost:8080</code></span>
        </div>
        <div class="capture-step">
          <span class="capture-step-num">4</span>
          <span>{{ $t('apis.capture_step4') }}</span>
        </div>
        <div class="capture-note">
          <strong>{{ $t('apis.capture_note') }}</strong> {{ $t('apis.capture_note_cmd') }}
        </div>
      </div>
      <template #footer>
        <el-button @click="showCaptureModal = false">{{ $t('apis.close') }}</el-button>
      </template>
    </el-dialog>

    <!-- cURL 导入 modal -->
    <el-dialog v-model="showCurlModal" :title="$t('apis.import_curl_title')" width="620px">
      <el-input
        v-model="curlCommand"
        type="textarea"
        :rows="8"
        :placeholder="$t('apis.curl_placeholder')"
        class="curl-textarea"
      />
      <div style="margin-top:16px;display:flex;gap:10px;justify-content:flex-end">
        <el-button @click="showCurlModal = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" @click="onCurlImport" :disabled="curlImporting || !curlCommand.trim()">
          {{ curlImporting ? $t('common.loading') : $t('apis.import_curl') }}
        </el-button>
      </div>
    </el-dialog>

    <!-- P1-5b: 手动新建接口弹窗 -->
    <el-dialog v-model="showCreateModal" :title="$t('apis.new_api_title')" width="620px" @close="resetCreateForm">
      <el-form label-position="top">
        <el-form-item :label="$t('apis.new_api_name')">
          <el-input v-model="createForm.name" :placeholder="$t('apis.new_api_name_placeholder')" />
        </el-form-item>
        <div style="display:flex;gap:12px">
          <el-form-item :label="$t('apis.new_api_method')" style="width:140px">
            <el-select v-model="createForm.method">
              <el-option v-for="m in ['GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS']" :key="m" :label="m" :value="m" />
            </el-select>
          </el-form-item>
          <el-form-item :label="$t('apis.new_api_url')" style="flex:1">
            <el-input v-model="createForm.url" :placeholder="$t('apis.new_api_url_placeholder')" />
          </el-form-item>
        </div>
        <el-form-item :label="$t('apis.new_api_path')">
          <el-input v-model="createForm.path" :placeholder="$t('apis.new_api_path_placeholder')" />
        </el-form-item>
        <div style="display:flex;gap:12px">
          <el-form-item :label="$t('apis.new_api_body_type')" style="width:160px">
            <el-select v-model="createForm.body_type">
              <el-option label="none" value="none" />
              <el-option label="json" value="json" />
              <el-option label="form" value="form" />
              <el-option label="text" value="text" />
              <el-option label="multipart" value="multipart" />
              <el-option label="xml" value="xml" />
            </el-select>
          </el-form-item>
          <el-form-item :label="$t('apis.new_api_tags')" style="flex:1">
            <el-input v-model="createForm.tagsStr" :placeholder="$t('apis.new_api_tags_placeholder')" />
          </el-form-item>
        </div>
        <el-form-item v-if="createForm.body_type !== 'none'" :label="$t('apis.new_api_body')">
          <el-input v-model="createForm.body" type="textarea" :rows="4" :placeholder='$t("apis.new_api_body_placeholder")' />
        </el-form-item>
        <div class="text-3" style="font-size:11px;margin-top:4px">{{ $t('apis.new_api_hint') }}</div>
      </el-form>
      <template #footer>
        <el-button @click="showCreateModal = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" @click="onCreateApi" :disabled="creating || !createForm.url">
          {{ creating ? $t('common.loading') : $t('common.create') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { apiApi, harApi, captureApi } from '@/api'
import { useProjectStore, useToastStore } from '@/stores'
import { fmt, methodClass, jsonPretty, statusTagType } from '@/utils'
import { useDebounceFn } from '@vueuse/core'
import AppPagination from '@/components/AppPagination.vue'
import ResultTag from '@/components/ResultTag.vue'
import AnalysisStatusTag from '@/components/AnalysisStatusTag.vue'
import { useWebSocket } from '@/composables/useWebSocket'

const { t } = useI18n()

const router = useRouter()
const projectStore = useProjectStore()
const toast = useToastStore()

const items       = ref([])
const total       = ref(0)
const loading     = ref(false)
const uploading   = ref(false)
const page        = ref(1)
const pageSize    = 50
const search      = ref('')
const filterAnalysisStatus = ref('')  // 5 状态分析过滤，替代旧 ai_analyzed bool
const filterMethod   = ref('')
// 新增过滤维度：来源、状态码、排序
const filterSource     = ref('')
const filterStatusCode = ref('')
const filterSortBy     = ref('created_at_desc')
const selected    = ref(new Set())
const runningId   = ref(null)
const analyzingId = ref(null)
const batchAnalyzing = ref(false)  // P1-3: 批量分析状态
const openApiImporting = ref(false)  // P1-5: OpenAPI/Swagger JSON 导入状态
const openApiExporting = ref(false)  // P1-5: 选中 API 导出 OpenAPI 状态
// 按 api.id 存储最近执行结果，避免多 API 运行时互相覆盖
const lastResults = ref({})
// 当前展示哪个 API 的执行结果；null 表示不展示
const activeResultApiId = ref(null)

// 当前活跃的执行结果（从 lastResults 中取出）
const activeResult = computed(() => {
  if (!activeResultApiId.value) return null
  return lastResults.value[activeResultApiId.value] || null
})
// 活跃结果的 API 路径，用于结果卡片标识
const activeResultApiPath = computed(() => {
  if (!activeResultApiId.value) return ''
  const api = items.value.find(a => a.id === activeResultApiId.value)
  return api ? `${api.request?.method} ${api.request?.path}` : activeResultApiId.value
})
// HAR 导入过滤：按域名/URL 关键字过滤，仅导入匹配的请求
const harFilterHost = ref('')
const harFilterUrl  = ref('')
let _captureTimer = null  // 抓包弹窗自动刷新 interval，modal 关闭时清理

// ── 抓包模式 ──
const showCaptureModal = ref(false)
const captureLoading   = ref(false)

// cURL 导入
const showCurlModal = ref(false)
const curlCommand = ref('')
const curlImporting = ref(false)

// P1-5b: 手动新建接口状态
const showCreateModal = ref(false)
const creating = ref(false)
const defaultCreateForm = () => ({
  name: '', method: 'GET', url: '', path: '',
  body_type: 'none', body: '', tagsStr: '',
})
const createForm = ref(defaultCreateForm())

// P1-5b: 打开新建弹窗
function openCreateModal() {
  resetCreateForm()
  showCreateModal.value = true
}

// P1-5b: 重置新建表单
function resetCreateForm() {
  createForm.value = defaultCreateForm()
}

// P1-5b: 提交新建接口
async function onCreateApi() {
  creating.value = true
  try {
    // body 按 body_type 处理：json 解析为对象，text 原样字符串，none 不传
    let bodyVal = null
    if (createForm.value.body_type === 'json' && createForm.value.body.trim()) {
      try { bodyVal = JSON.parse(createForm.value.body) }
      catch { toast.error(t('apis.new_api_body_json_error')); creating.value = false; return }
    } else if (createForm.value.body_type !== 'none' && createForm.value.body.trim()) {
      bodyVal = createForm.value.body
    }
    // tags 字符串转数组
    const tags = createForm.value.tagsStr.split(',').map(s => s.trim()).filter(Boolean)
    const payload = {
      name: createForm.value.name,
      method: createForm.value.method,
      url: createForm.value.url,
      path: createForm.value.path,
      body_type: createForm.value.body_type,
      body: bodyVal,
      tags,
      project_id: projectStore.current,
    }
    const created = await apiApi.create(payload)
    toast.success(t('apis.new_api_created'))
    showCreateModal.value = false
    // 跳转到新接口详情页，便于继续编辑断言/触发分析
    router.push(`/apis/${created.id}`)
  } catch (e) {
    toast.error(e.message || t('apis.new_api_error'))
  } finally {
    creating.value = false
  }
}
const captureState = ref({ enabled: false, ingested_count: 0, last_ingest_at: null, filter_host: null, filter_url: null })
// 抓包模态框内的过滤输入（双向绑定到 captureState 的回显值）
const captureFilterHost = ref('')
const captureFilterUrl  = ref('')

// ElTable selection-change → Set 同步
const tableRef = ref(null)
function onSelectionChange(rows) {
  selected.value = new Set(rows.map(r => r.id))
}

async function refreshCaptureStatus() {
  captureLoading.value = true
  try {
    const res = await captureApi.status()
    captureState.value = res
    // 同步回显过滤字段到输入框
    captureFilterHost.value = res.filter_host || ''
    captureFilterUrl.value = res.filter_url || ''
  } catch (e) {
    toast.error(e.message || t('apis.capture_status_failed'))
  } finally {
    captureLoading.value = false
  }
}

// 切换抓包开关：调用后端 toggle 端点并刷新状态
async function toggleCapture() {
  captureLoading.value = true
  try {
    const res = await captureApi.toggle(!captureState.value.enabled, {
      filterHost: captureFilterHost.value,
      filterUrl: captureFilterUrl.value,
    })
    captureState.value = res
    captureFilterHost.value = res.filter_host || ''
    captureFilterUrl.value = res.filter_url || ''
  } catch (e) {
    toast.error(e.message || t('apis.capture_toggle_failed'))
  } finally {
    captureLoading.value = false
  }
}

// 用户修改过滤输入框时，自动同步到后端
async function updateCaptureFilter() {
  captureLoading.value = true
  try {
    // 发送当前 enabled 状态 + 过滤条件 到后端
    const res = await captureApi.toggle(captureState.value.enabled, {
      filterHost: captureFilterHost.value,
      filterUrl: captureFilterUrl.value,
    })
    captureState.value = res
    captureFilterHost.value = res.filter_host || ''
    captureFilterUrl.value = res.filter_url || ''
  } catch (e) {
    toast.error(e.message || t('apis.capture_filter_failed'))
  } finally {
    captureLoading.value = false
  }
}

// 打开抓包弹窗时拉取状态并启动 5s 自动刷新
function onCaptureModalOpen() {
  refreshCaptureStatus()
  // 每 5s 自动刷新抓包状态，保持数据实时
  if (_captureTimer) clearInterval(_captureTimer)
  _captureTimer = setInterval(refreshCaptureStatus, 5000)
}

// 关闭抓包弹窗时清除 interval
function onCaptureModalClose() {
  if (_captureTimer) {
    clearInterval(_captureTimer)
    _captureTimer = null
  }
}

// 判断是否有任意过滤条件激活，用于显示"清除"按钮
const anyFilter = computed(() => search.value || filterAnalysisStatus.value || filterMethod.value || filterSource.value || filterStatusCode.value || filterSortBy.value !== 'created_at_desc')

function qualityColor(level) {
  const map = {
    low: '#3ecf8e',
    medium: '#f0b44c',
    high: '#f06f4f',
    critical: '#f06060',
  }
  return map[level] || map.critical
}

async function load() {
  loading.value = true
  try {
    const params = {
      project_id: projectStore.current,
      skip: (page.value - 1) * pageSize,
      limit: pageSize,
    }
    if (search.value) params.search = search.value
    if (filterAnalysisStatus.value !== '') params.analysis_status = filterAnalysisStatus.value
    if (filterMethod.value) params.method = filterMethod.value
    // 新增过滤参数
    if (filterSource.value) params.source = filterSource.value
    // 状态码过滤：如 "4xx" → min=400, max=499
    if (filterStatusCode.value) {
      const prefix = parseInt(filterStatusCode.value[0], 10)
      params.status_code_min = prefix * 100
      params.status_code_max = prefix * 100 + 99
    }
    // 排序：格式如 "created_at_desc"
    if (filterSortBy.value) {
      const lastUnderscore = filterSortBy.value.lastIndexOf('_')
      params.sort_by = filterSortBy.value.substring(0, lastUnderscore)
      params.sort_order = filterSortBy.value.substring(lastUnderscore + 1)
    }
    const res = await apiApi.list(params)
    items.value = res.items || []
    total.value = res.total || 0
  } catch (e) {
    toast.error(e.message || t('apis.load_failed'))
  } finally {
    loading.value = false
  }
}

const onSearch = useDebounceFn(() => { page.value = 1; load() }, 300)

function clearFilters() {
  search.value = ''
  filterAnalysisStatus.value = ''
  filterMethod.value = ''
  // 重置新增过滤条件到默认值
  filterSource.value = ''
  filterStatusCode.value = ''
  filterSortBy.value = 'created_at_desc'
  page.value = 1
  load()
}

async function batchDelete() {
  try {
    await ElMessageBox.confirm(t('apis.confirm_batch_delete', { count: selected.value.size }), t('apis.confirm_batch_delete_title'), { type: 'warning' })
    const r = await apiApi.batchDelete([...selected.value])
    toast.success(t('apis.delete_done', { deleted: r.deleted }))
    selected.value.clear()
    load()
  } catch (e) {
    // ElMessageBox reject 可能返回 'cancel'(按钮) 或 'close'(X关闭)，均忽略
    if (e !== 'cancel' && e !== 'close' && e.message) toast.error(e.message)
  }
}

async function onCurlImport() {
  if (!curlCommand.value.trim() || curlImporting.value) return
  curlImporting.value = true
  try {
    const res = await apiApi.importCurl(curlCommand.value, projectStore.current)
    toast.success(t('apis.import_curl_success'))
    showCurlModal.value = false
    curlCommand.value = ''
    // 跳转到新创建的 API 详情页
    if (res && res.id) {
      router.push(`/apis/${res.id}`)
    } else {
      await load()
    }
  } catch (e) {
    const msg = e?.response?.data?.detail || e?.message || t('apis.import_curl_failed')
    toast.error(msg)
  } finally {
    curlImporting.value = false
  }
}

async function onHarUpload(e) {
  const file = e.target.files?.[0]
  if (!file) return
  uploading.value = true
  try {
    const res = await harApi.upload(file, projectStore.current, {
      filterHost: harFilterHost.value,
      filterUrl: harFilterUrl.value,
    })
    if (res.status === 'success') {
      toast.success(t('apis.parse_done', { new_apis: res.new_apis, skipped_static: res.skipped_static }))
      load()
    } else {
      toast.error(t('apis.parse_failed') + ': ' + res.reason)
    }
  } catch (e) { toast.error(e.message) }
  finally { uploading.value = false; e.target.value = '' }
}

// P1-5: OpenAPI/Swagger JSON 导入 —— 复用已有 API 资产列表入口。
async function onOpenApiUpload(e) {
  const file = e.target.files?.[0]
  if (!file) return
  openApiImporting.value = true
  try {
    const text = await file.text()
    let spec
    try { spec = JSON.parse(text) }
    catch { toast.error(t('apis.import_openapi_json_error')); return }
    const res = await apiApi.importOpenApi(spec, projectStore.current, file.name)
    toast.success(t('apis.import_openapi_done', {
      imported: res.new_apis || 0,
      skipped: res.skipped_duplicates || 0,
    }))
    await load()
  } catch (err) {
    toast.error(err.message || t('apis.import_openapi_failed'))
  } finally {
    openApiImporting.value = false
    e.target.value = ''
  }
}

// P1-5: 批量导出 OpenAPI JSON，便于接口资产回流到文档/网关/研发工具链。
async function exportSelectedOpenApi() {
  const ids = [...selected.value]
  if (!ids.length) return
  openApiExporting.value = true
  try {
    const blob = await apiApi.exportOpenApi(ids)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'apis-openapi.json'
    a.click()
    URL.revokeObjectURL(url)
    toast.success(t('apis.export_openapi_done', { count: ids.length }))
  } catch (err) {
    toast.error(err.message || t('apis.export_openapi_failed'))
  } finally {
    openApiExporting.value = false
  }
}

async function runApi(api) {
  runningId.value = api.id
  try {
    const res = await apiApi.run(api.id, {})
    // 按 api.id 存储结果，不同 API 的执行结果互不覆盖
    lastResults.value[api.id] = res
    activeResultApiId.value = api.id
    toast[res.passed ? 'success' : 'error'](res.passed ? t('common.pass') : t('common.fail') + ': ' + (res.failure_reason || '…'))
  } catch (e) { toast.error(e.message) }
  finally { runningId.value = null }
}

async function analyzeApi(api) {
  analyzingId.value = api.id
  try {
    // done/failed 状态强制重新分析（force=true），idle 状态首次分析无需 force
    const forceReanalyze = ['applied', 'done', 'pending_review', 'failed'].includes(api.analysis_status)
    await apiApi.analyze(api.id, forceReanalyze)
    toast.info(t('apis.analyze_queued'))
    // 立即更新本地行的分析状态为 queued，无需等待轮询
    const row = items.value.find(it => it.id === api.id)
    if (row) { row.analysis_status = 'queued'; row.analysis_error = '' }
  } catch (e) { toast.error(e.message) }
  finally { analyzingId.value = null }
}

// P1-3: 批量分析 —— 对选中接口逐个触发 AI 分析（并发 5 防限流）
async function batchAnalyze() {
  const ids = [...selected.value]
  if (!ids.length) return
  batchAnalyzing.value = true
  let queued = 0, failed = 0
  // 分批并发（每批 5 个），避免瞬间打满 AI 队列
  const BATCH = 5
  for (let i = 0; i < ids.length; i += BATCH) {
    const batch = ids.slice(i, i + BATCH)
    await Promise.allSettled(batch.map(async (id) => {
      const api = items.value.find(it => it.id === id)
      const force = api && ['applied', 'done', 'pending_review', 'failed'].includes(api.analysis_status)
      await apiApi.analyze(id, force)
      if (api) api.analysis_status = 'queued'
      queued++
    })).then(results => {
      failed += results.filter(r => r.status === 'rejected').length
    })
  }
  toast.success(t('apis.batch_analyze_done', { queued, failed, total: ids.length }))
  batchAnalyzing.value = false
}

// ── WebSocket + 轮询双通道：监听分析状态变更，更新列表行 ──
let _pollTimer = null
const POLL_INTERVAL = 3000    // 轮询间隔 3s
const POLL_MAX_DURATION = 60000  // 最长轮询 60s

// WebSocket 消息处理：匹配 items 中的 api_id 后更新 analysis_status
function onAiWsMessage(msg) {
  if (!msg || !msg.api_id) return
  const row = items.value.find(it => it.id === msg.api_id)
  if (!row) return
  if (msg.type === 'status') {
    row.analysis_status = msg.status
    if (msg.error) row.analysis_error = msg.error
    // 到达终态后触发一次完整重载，刷新关联的 doc/asserts
    if (msg.status === 'done' || msg.status === 'failed') {
      load()
    }
  }
}

// 轮询检测：每 3s 检查列表中是否有 queued/running 的行，重新加载
function startStatusPolling() {
  stopStatusPolling()
  const startedAt = Date.now()
  _pollTimer = setInterval(async () => {
    // 检查是否有需要跟踪的行
    const active = items.value.some(it =>
      it.analysis_status === 'queued' || it.analysis_status === 'running'
    )
    const elapsed = Date.now() - startedAt
    if (!active || elapsed > POLL_MAX_DURATION) {
      stopStatusPolling()
      return
    }
    // 重新加载当前页（保持分页/过滤条件）
    await load()
  }, POLL_INTERVAL)
}

function stopStatusPolling() {
  if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null }
}

watch(() => projectStore.current, () => { page.value = 1; load() })
onMounted(() => {
  load()
  // 连接 AI 分析 WebSocket，实时更新列表行状态（自动在 onUnmounted 清理）
  useWebSocket(`/ai-analysis?project_id=${encodeURIComponent(projectStore.current || 'default')}`, onAiWsMessage)
  // 启动轮询作为 WS 回退，确保列表中 queued/running 行能刷新
  startStatusPolling()
})
onUnmounted(() => {
  stopStatusPolling()
  // 清理抓包自动刷新 interval
  if (_captureTimer) {
    clearInterval(_captureTimer)
    _captureTimer = null
  }
})
</script>

<style scoped>
.filters {
  display: flex; align-items: center; gap: 10px;
  padding: 12px 24px; border-bottom: 0px solid var(--border);
  flex-wrap: wrap; /* 过滤项超出宽度时自然折行 */
}
.search-input { width: 220px; }
.filter-select { width: 140px; }
.filter-input { width: 130px; }

.quality-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 58px;
}
.quality-score {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 700;
  color: var(--text);
}

.run-result {
  margin-top: 12px; border-radius: var(--radius-lg); border: 1px solid;
  overflow: hidden;
}
.run-ok   { border-color: rgba(62,207,142,.3);  background: rgba(62,207,142,.05); }
.run-fail { border-color: rgba(240,96,96,.3);    background: rgba(240,96,96,.05); }
.run-result-header {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 16px; border-bottom: 1px solid var(--border);
}
.run-result-header .el-button { margin-left: auto; }

/* ── 抓包模态 ── */
.capture-status-bar {
  display: flex; align-items: center; gap: 16px;
  padding: 12px 16px; border-radius: var(--radius);
  background: var(--bg-2); margin-bottom: 20px;
  flex-wrap: wrap;
}
.capture-stat { display: flex; align-items: center; gap: 6px; }
.capture-stat-label { font-size: 11px; color: var(--text-3); text-transform: uppercase; }
.capture-instructions { padding: 0 4px; }
.capture-step {
  display: flex; align-items: flex-start; gap: 10px;
  margin-bottom: 10px; font-size: 13px; line-height: 1.6;
}
.capture-step-num {
  width: 22px; height: 22px; flex-shrink: 0;
  border-radius: 50%; background: var(--accent);
  color: #fff; font-size: 11px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
  margin-top: 1px;
}
.capture-cmd { margin: 8px 0 12px 32px; font-size: 12px; padding: 10px 14px; white-space: pre-wrap; }
.inline-code {
  font-family: var(--font-mono); font-size: 11px;
  background: var(--bg-hover); padding: 1px 5px;
  border-radius: 3px; border: 1px solid var(--border);
}
.capture-note {
  margin-top: 16px; padding: 10px 14px; border-radius: var(--radius);
  background: rgba(79,142,247,.08); font-size: 12px; line-height: 1.8;
  color: var(--text-2);
}
</style>
