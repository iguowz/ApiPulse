<template>
  <!-- AI 生成审核中心：按类型/状态筛选，查看待审核版本，执行审核操作 -->
  <div class="page">
    <div class="page-header">
      <div>
        <div class="page-title">{{ $t('generations.title') }}</div>
        <div class="page-subtitle">{{ $t('generations.subtitle') }}</div>
      </div>
    </div>

    <div class="page-body">
      <!-- 筛选栏 -->
      <div class="filter-bar">
        <div class="filter-row">
          <div class="filter-item">
            <span class="filter-label">{{ $t('generations.filter_type') }}</span>
            <el-select v-model="filterType" :placeholder="$t('generations.filter_type_all')" size="small" clearable @change="onFilterChange">
              <el-option value="doc" :label="$t('generations.type_doc')" />
              <el-option value="asserts" :label="$t('generations.type_asserts')" />
              <el-option value="scenario" :label="$t('generations.type_scenario')" />
              <el-option value="data_template" :label="$t('generations.type_data_template')" />
              <el-option value="monitor" :label="$t('generations.type_monitor')" />
            </el-select>
          </div>
          <div class="filter-item">
            <span class="filter-label">{{ $t('generations.filter_status') }}</span>
            <el-select v-model="filterStatus" :placeholder="$t('generations.filter_status_all')" size="small" clearable @change="onFilterChange">
              <el-option value="pending_review" :label="$t('generations.status_pending_review')" />
              <el-option value="accepted" :label="$t('generations.status_accepted')" />
              <el-option value="partially_accepted" :label="$t('generations.status_partially_accepted')" />
              <el-option value="rejected" :label="$t('generations.status_rejected')" />
            </el-select>
          </div>
        </div>
      </div>

      <!-- 列表表格 -->
      <el-card v-loading="loading">
        <el-table :data="items" stripe size="small" style="width:100%">
          <el-table-column :label="$t('generations.col_api_id')" width="200">
            <template #default="{ row }">
              <router-link v-if="row.api_id" :to="'/apis/' + row.api_id" class="mono link">
                {{ row.api_name || row.api_path || row.api_id }}
              </router-link>
              <span v-else class="text-3">—</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('generations.col_type')" width="90">
            <template #default="{ row }">
              <el-tag :type="typeTag(row.type)" size="small">{{ $t('generations.type_' + row.type) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="$t('generations.col_status')" width="120">
            <template #default="{ row }">
              <el-tag :type="statusTag(row.status)" size="small">{{ $t('generations.status_' + row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="来源" width="120">
            <template #default="{ row }">
              <el-tag size="small" type="info">{{ sourceLabel(row.source) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="Job" width="130">
            <template #default="{ row }">
              <span class="text-3 mono" style="font-size:11px">{{ row.job_id || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('generations.col_summary')" min-width="220">
            <template #default="{ row }">
              <span class="text-2" style="font-size:13px">{{ row.summary || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('generations.col_model')" width="140">
            <template #default="{ row }">
              <span class="text-3 mono" style="font-size:11px">{{ row.model || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('generations.col_created')" width="170">
            <template #default="{ row }">
              <span class="text-3" style="font-size:11px">{{ fmt.time(row.created_at) || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('generations.col_actions')" width="220" fixed="right">
            <template #default="{ row }">
              <div class="action-btns">
                <!-- 预览：弹窗查看 diff 对比 -->
                <el-button size="small" text type="primary" @click="openPreview(row)">
                  {{ $t('generations.preview') }}
                </el-button>
                <!-- 待审核状态显示审核操作 -->
                <template v-if="row.status === 'pending_review'">
                  <el-button size="small" text type="success" @click="doAccept(row)">
                    {{ $t('generations.accept') }}
                  </el-button>
                  <el-button size="small" text type="danger" @click="openReject(row)">
                    {{ $t('generations.reject') }}
                  </el-button>
                </template>
                <el-tag v-else size="small" type="info">已处理</el-tag>
              </div>
            </template>
          </el-table-column>
        </el-table>

        <!-- 分页 -->
        <div v-if="total > pageSize" class="pagination-wrap">
          <el-pagination
            v-model:current-page="page"
            :page-size="pageSize"
            :total="total"
            layout="prev, pager, next"
            @current-change="load"
          />
        </div>
      </el-card>
    </div>

    <!-- 预览弹窗：使用 ReviewPanel 组件 -->
    <el-dialog
      v-model="showPreview"
      :title="$t('generations.preview_title')"
      width="900px"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <ReviewPanel
        v-if="showPreview && previewId"
        :generation-id="previewId"
        @accepted="onPreviewAccepted"
        @rejected="onPreviewRejected"
      />
    </el-dialog>

    <!-- 拒绝原因弹窗（列表页快捷拒绝） -->
    <el-dialog
      v-model="showRejectDlg"
      :title="$t('generations.reject_title')"
      width="420px"
      :close-on-click-modal="false"
    >
      <el-input
        v-model="rejectFeedback"
        type="textarea"
        :rows="3"
        :placeholder="$t('generations.reject_placeholder')"
      />
      <template #footer>
        <el-button @click="showRejectDlg = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="danger" @click="doReject">{{ $t('generations.reject_confirm') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { generationApi } from '@/api'
import { useProjectStore, useToastStore } from '@/stores'
import { fmt } from '@/utils'
import ReviewPanel from '@/components/ReviewPanel.vue'

const { t } = useI18n()
const toast = useToastStore()
const projectStore = useProjectStore()
const route = useRoute()

// 列表数据
const items = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const pageSize = 30

// 筛选
const filterType = ref('')
// 默认筛选"待审核"状态，方便审核人员快速定位待处理项
const filterStatus = ref('pending_review')

// 预览弹窗
const showPreview = ref(false)
const previewId = ref('')

// 拒绝弹窗
const showRejectDlg = ref(false)
const rejectId = ref('')
const rejectFeedback = ref('')

// 类型标签颜色
function typeTag(type) {
  const map = { doc: 'success', asserts: 'warning', scenario: 'primary', data_template: 'info', monitor: 'danger' }
  return map[type] || 'info'
}

// 状态标签颜色
function statusTag(status) {
  const map = {
    pending_review: 'warning',
    accepted: 'success',
    partially_accepted: 'info',
    rejected: 'danger',
  }
  return map[status] || 'info'
}

function sourceLabel(source) {
  const map = {
    analyzer: '分析器',
    ai_chat: 'AI 助手',
    diff_evaluator: '差异评估',
    data_factory: '数据工厂',
    manual_edit: '手动编辑',
  }
  return map[source] || source || '—'
}

// 加载列表
async function load() {
  loading.value = true
  try {
    const params = {
      project_id: projectStore.current,
      skip: (page.value - 1) * pageSize,
      limit: pageSize,
    }
    if (filterType.value) params.type = filterType.value
    if (filterStatus.value) params.status = filterStatus.value

    const res = await generationApi.list(params)
    items.value = res.items || []
    total.value = res.total || 0
  } catch (e) {
    toast.error(e.message || t('generations.load_failed'))
  } finally {
    loading.value = false
  }
}

// 筛选变更时重置分页并重新加载
function onFilterChange() {
  page.value = 1
  load()
}

// 打开预览弹窗
function openPreview(row) {
  previewId.value = row.id
  showPreview.value = true
}

// 预览弹窗内审核操作完成后回调
// 采纳/拒绝后自动加载列表并打开下一个待审核项
function onPreviewAccepted() {
  load().then(() => {
    const next = items.value.find(item => item.status === 'pending_review' && item.id !== previewId.value)
    if (next) {
      previewId.value = next.id
    } else {
      showPreview.value = false
    }
  })
}
function onPreviewRejected() {
  load().then(() => {
    const next = items.value.find(item => item.status === 'pending_review' && item.id !== previewId.value)
    if (next) {
      previewId.value = next.id
    } else {
      showPreview.value = false
    }
  })
}

// 列表页快捷接受
async function doAccept(row) {
  try {
    await generationApi.accept(row.id)
    toast.success(t('generations.accept_success'))
    await load()
  } catch (e) {
    toast.error(e.message || t('generations.accept_failed'))
  }
}

// 列表页快捷拒绝：打开拒绝原因弹窗
function openReject(row) {
  rejectId.value = row.id
  rejectFeedback.value = ''
  showRejectDlg.value = true
}

// 确认拒绝
async function doReject() {
  try {
    await generationApi.reject(rejectId.value, rejectFeedback.value)
    toast.success(t('generations.reject_success'))
    showRejectDlg.value = false
    await load()
  } catch (e) {
    toast.error(e.message || t('generations.reject_failed'))
  }
}

onMounted(() => {
  // 从 URL 查询参数恢复筛选条件（场景页面生成完成后的跳转）
  if (route.query.type) filterType.value = route.query.type
  if (route.query.status) filterStatus.value = route.query.status
  load()
})

watch(() => projectStore.current, () => {
  page.value = 1
  load()
})
</script>

<style scoped>
.filter-bar {
  margin-bottom: 12px;
}
.filter-row {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}
.filter-item {
  display: flex;
  align-items: center;
  gap: 6px;
}
/* 确保筛选下拉框有足够宽度展示选项文字 */
.filter-item :deep(.el-select) {
  min-width: 120px;
}
.filter-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-3);
  text-transform: uppercase;
  letter-spacing: .05em;
  white-space: nowrap;
}
.action-btns {
  display: flex;
  gap: 2px;
  flex-wrap: nowrap;
}
.link {
  color: var(--accent);
  text-decoration: none;
}
.link:hover {
  text-decoration: underline;
}
.pagination-wrap {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
