<template>
  <!-- 可复用 AI 生成审核面板：左右对比当前内容 vs AI 生成内容，支持接受/拒绝/编辑 -->
  <div class="review-panel" v-loading="loading">
    <!-- 审核操作栏 -->
    <div class="review-toolbar">
      <div class="review-meta">
        <el-tag :type="genTypeTag" size="small">{{ t('generations.type_' + genType) }}</el-tag>
        <el-tag :type="isPending ? 'warning' : 'info'" size="small">{{ generation.status || '—' }}</el-tag>
        <span class="text-3" style="font-size:12px">{{ t('generations.model') }}: {{ genModel }}</span>
        <span class="text-3" style="font-size:12px">来源: {{ sourceLabel(generation.source) }}</span>
        <span class="text-3 mono" style="font-size:11px">Job: {{ generation.job_id || '—' }}</span>
        <span class="text-3" style="font-size:12px">耗时: {{ generation.latency_ms || 0 }}ms · Token: {{ (generation.input_tokens || 0) + (generation.output_tokens || 0) }}</span>
      </div>
      <div class="review-actions">
        <el-button type="primary" size="small" @click="handleAccept" :loading="acting" :disabled="!isPending">
          {{ t('generations.accept') }}
        </el-button>
        <el-button type="warning" size="small" @click="handleEdit" :disabled="acting || !isPending">
          {{ t('generations.edit_accept') }}
        </el-button>
        <el-button
          v-if="partialCandidates.length"
          type="info"
          size="small"
          @click="showPartialDialog = true"
          :disabled="acting || !isPending"
        >
          {{ t('generations.partial_accept') }}
        </el-button>
        <el-button type="danger" size="small" @click="showRejectDialog = true" :disabled="acting || !isPending">
          {{ t('generations.reject') }}
        </el-button>
      </div>
    </div>
    <div v-if="generation.summary" class="review-summary">{{ generation.summary }}</div>

    <div v-if="generation.review_stats" class="review-stats">
      <div class="review-stat">
        <span>{{ t('generations.stat_accepted') }}</span>
        <strong>{{ generation.review_stats.accepted || 0 }}</strong>
      </div>
      <div class="review-stat">
        <span>{{ t('generations.stat_partial') }}</span>
        <strong>{{ generation.review_stats.partially_accepted || 0 }}</strong>
      </div>
      <div class="review-stat">
        <span>{{ t('generations.stat_rejected') }}</span>
        <strong>{{ generation.review_stats.rejected || 0 }}</strong>
      </div>
      <div class="review-stat">
        <span>{{ t('generations.stat_pending') }}</span>
        <strong>{{ generation.review_stats.pending_review || 0 }}</strong>
      </div>
      <el-tag size="small" type="info">
        {{ generation.review_stats.scope === 'job' ? t('generations.stat_scope_job') : t('generations.stat_scope_target') }}
      </el-tag>
    </div>

    <div v-if="fieldDiffs.length" class="field-diff-section">
      <div class="field-diff-header">
        <span>{{ t('generations.field_diff_title') }}</span>
        <span class="text-3">{{ t('generations.field_diff_hint') }}</span>
      </div>
      <div class="field-diff-list">
        <label
          v-for="item in fieldDiffs"
          :key="item.key"
          class="field-diff-row"
          :class="`status-${item.status}`"
        >
          <el-checkbox
            v-model="partialFields"
            :label="item.key"
            :disabled="!item.selectable || !isPending"
          />
          <div class="field-diff-main">
            <div class="field-diff-title">
              <span class="mono">{{ item.label || item.key }}</span>
              <el-tag size="small" :type="diffStatusTag(item.status)">
                {{ t('generations.diff_' + item.status) }}
              </el-tag>
            </div>
            <div class="field-diff-values">
              <div>
                <span>{{ t('generations.current_content') }}</span>
                <pre>{{ compactValue(item.current) }}</pre>
              </div>
              <div>
                <span>{{ t('generations.generated_content') }}</span>
                <pre>{{ compactValue(item.generated) }}</pre>
              </div>
            </div>
          </div>
        </label>
      </div>
    </div>

    <!-- 左右对比视图：当前内容 vs AI 生成内容 -->
    <div class="review-diff-container">
      <!-- 左侧：当前实际内容 -->
      <div class="review-pane">
        <div class="review-pane-header">
          <span class="review-pane-title">{{ t('generations.current_content') }}</span>
          <el-tag v-if="currentEmpty" size="small" type="info">{{ t('generations.empty_current') }}</el-tag>
        </div>
        <div class="review-pane-body">
          <pre class="review-json" v-html="highlightedCurrent"></pre>
        </div>
      </div>

      <!-- 右侧：AI 生成内容 -->
      <div class="review-pane">
        <div class="review-pane-header">
          <span class="review-pane-title">{{ t('generations.generated_content') }}</span>
        </div>
        <div class="review-pane-body">
          <pre class="review-json" v-html="highlightedGenerated"></pre>
        </div>
      </div>
    </div>

    <!-- 拒绝原因弹窗 -->
    <el-dialog
      v-model="showRejectDialog"
      :title="t('generations.reject_title')"
      width="420px"
      :close-on-click-modal="false"
    >
      <el-input
        v-model="rejectFeedback"
        type="textarea"
        :rows="3"
        :placeholder="t('generations.reject_placeholder')"
      />
      <template #footer>
        <el-button @click="showRejectDialog = false">{{ t('common.cancel') }}</el-button>
        <el-button type="danger" @click="handleReject" :loading="acting">
          {{ t('generations.reject_confirm') }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 编辑弹窗：编辑 AI 生成内容后再接受 -->
    <el-dialog
      v-model="showEditDialog"
      :title="t('generations.edit_title')"
      width="640px"
      :close-on-click-modal="false"
    >
      <el-input
        v-model="editContentText"
        type="textarea"
        :rows="16"
        :placeholder="t('generations.edit_placeholder')"
        :class="{ 'input-error': editJsonError }"
      />
      <span v-if="editJsonError" class="field-error">{{ t('generations.edit_json_error') }}</span>
      <template #footer>
        <el-button @click="showEditDialog = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="handleEditConfirm" :loading="acting">
          {{ t('generations.edit_confirm') }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="showPartialDialog"
      :title="t('generations.partial_accept')"
      width="460px"
      :close-on-click-modal="false"
    >
      <el-checkbox-group v-model="partialFields" class="partial-list">
        <el-checkbox
          v-for="item in partialCandidateItems"
          :key="item.key"
          :label="item.key"
          :disabled="!item.selectable"
        >
          {{ item.label || item.key }}
        </el-checkbox>
      </el-checkbox-group>
      <template #footer>
        <el-button @click="showPartialDialog = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="handlePartialAccept" :loading="acting" :disabled="!partialFields.length">
          {{ t('generations.accept') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { generationApi } from '@/api'
import { useToastStore } from '@/stores'

const { t } = useI18n()
const toast = useToastStore()

const props = defineProps({
  generationId: { type: String, required: true },  // GenerationVersion ID
})

const emit = defineEmits(['accepted', 'rejected', 'edited'])

const loading = ref(true)
const acting = ref(false)
const diffData = ref({ current: {}, generated: {}, type: '', api_id: '', field_diffs: [] })
const generation = ref({})

// 弹窗状态
const showRejectDialog = ref(false)
const rejectFeedback = ref('')
const showEditDialog = ref(false)
const editContentText = ref('')
const editJsonError = ref(false)
const showPartialDialog = ref(false)
const partialFields = ref([])

// 便捷计算属性
const currentEmpty = computed(() => {
  const c = diffData.value.current
  return !c || Object.keys(c).length === 0
})
const genType = computed(() => diffData.value.type || '')
const genModel = computed(() => generation.value.model || '')
const isPending = computed(() => !generation.value.status || generation.value.status === 'pending_review')

// 类型标签颜色映射：doc→success, asserts→warning, scenario→primary
const genTypeTag = computed(() => {
  const map = { doc: 'success', asserts: 'warning', scenario: 'primary', data_template: 'info', monitor: 'danger' }
  return map[genType.value] || 'info'
})

const fieldDiffs = computed(() => diffData.value.field_diffs || [])

const fallbackPartialCandidates = computed(() => {
  const generated = diffData.value.generated || {}
  if (genType.value === 'doc') {
    return ['summary', 'description', 'params', 'response_fields', 'tags'].filter(k => generated[k] !== undefined)
  }
  if (genType.value === 'asserts') {
    return (generated.asserts || []).map(a => a.field).filter(Boolean)
  }
  if (genType.value === 'data_template') {
    return (generated.fields || []).map(f => f.name).filter(Boolean)
  }
  if (genType.value === 'monitor') {
    return (generated.monitors || []).map((m, idx) => m.id || m.target_id || m.name || String(idx)).filter(Boolean)
  }
  return []
})

const partialCandidateItems = computed(() => {
  if (fieldDiffs.value.length) {
    return fieldDiffs.value.map(item => ({
      key: item.key,
      label: item.label || item.key,
      selectable: item.selectable !== false,
    }))
  }
  return fallbackPartialCandidates.value.map(key => ({ key, label: key, selectable: true }))
})

const partialCandidates = computed(() => partialCandidateItems.value.map(item => item.key))

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

function diffStatusTag(status) {
  const map = { added: 'success', modified: 'warning', unchanged: 'info', removed: 'danger' }
  return map[status] || 'info'
}

function compactValue(value) {
  if (value === undefined || value === null) return '—'
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2)
  return text.length > 1200 ? text.slice(0, 1200) + '\n…' : text
}

// JSON 格式化并高亮差异字段（简单实现：格式化 + 行号 + 差异标记）
function formatAndHighlight(obj, isCurrent) {
  if (!obj || (typeof obj === 'object' && Object.keys(obj).length === 0)) {
    return '<span class="text-3">' + t('generations.empty_content') + '</span>'
  }
  const json = JSON.stringify(obj, null, 2)
  return escapeHtml(json)
}

// 简单 HTML 转义，防止 JSON 中的 < > 被浏览器解析
function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/("(?:[^"\\]|\\.)*")\s*:/g, '<span class="json-key">$1</span>:')
    .replace(/: ("(?:[^"\\]|\\.)*")/g, ': <span class="json-string">$1</span>')
    .replace(/: (\d+\.?\d*)/g, ': <span class="json-number">$1</span>')
    .replace(/: (true|false|null)/g, ': <span class="json-bool">$1</span>')
}

const highlightedCurrent = computed(() => formatAndHighlight(diffData.value.current, true))
const highlightedGenerated = computed(() => formatAndHighlight(diffData.value.generated, false))

// 加载 diff 数据
async function loadDiff() {
  loading.value = true
  try {
    const [detail, diff] = await Promise.all([
      generationApi.get(props.generationId),
      generationApi.diff(props.generationId),
    ])
    generation.value = detail || {}
    diffData.value = diff
    // 字段级 diff 默认勾选有变化且可采纳的条目，让用户可以直接点“部分采纳”。
    if (Array.isArray(diff?.field_diffs) && diff.field_diffs.length) {
      partialFields.value = diff.field_diffs.filter(item => item.selectable !== false && item.status !== 'unchanged').map(item => item.key)
    } else {
      partialFields.value = []
    }
  } catch (e) {
    toast.error(e.message || t('generations.load_diff_failed'))
  } finally {
    loading.value = false
  }
}

// 接受：调用 API，发送 accepted 事件
async function handleAccept() {
  if (!isPending.value) return
  acting.value = true
  try {
    await generationApi.accept(props.generationId)
    toast.success(t('generations.accept_success'))
    emit('accepted')
  } catch (e) {
    toast.error(e.message || t('generations.accept_failed'))
  } finally {
    acting.value = false
  }
}

// 拒绝：先打开拒绝原因弹窗，确认后调用 API
async function handleReject() {
  if (!isPending.value) return
  acting.value = true
  try {
    await generationApi.reject(props.generationId, rejectFeedback.value)
    toast.success(t('generations.reject_success'))
    showRejectDialog.value = false
    rejectFeedback.value = ''
    emit('rejected')
  } catch (e) {
    toast.error(e.message || t('generations.reject_failed'))
  } finally {
    acting.value = false
  }
}

// 编辑后接受：打开编辑弹窗，预填 AI 生成内容
function handleEdit() {
  if (!isPending.value) return
  editContentText.value = JSON.stringify(diffData.value.generated, null, 2)
  editJsonError.value = false
  showEditDialog.value = true
}

// 确认编辑并接受
async function handleEditConfirm() {
  let content
  try {
    content = JSON.parse(editContentText.value)
    editJsonError.value = false
  } catch {
    editJsonError.value = true
    return
  }
  acting.value = true
  try {
    await generationApi.edit(props.generationId, content)
    toast.success(t('generations.edit_success'))
    showEditDialog.value = false
    emit('edited')
    emit('accepted')
  } catch (e) {
    toast.error(e.message || t('generations.edit_failed'))
  } finally {
    acting.value = false
  }
}

async function handlePartialAccept() {
  if (!isPending.value || !partialFields.value.length) return
  acting.value = true
  try {
    await generationApi.acceptPartial(props.generationId, partialFields.value)
    toast.success(t('generations.accept_success'))
    showPartialDialog.value = false
    emit('accepted')
  } catch (e) {
    toast.error(e.message || t('generations.accept_failed'))
  } finally {
    acting.value = false
  }
}

// generationId 变化时重新加载 diff（immediate 处理初始加载，避免 watch+onMounted 重复触发）
watch(() => props.generationId, (id) => {
  if (id) loadDiff()
}, { immediate: true })
</script>

<style scoped>
.review-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.review-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-2);
  flex-wrap: wrap;
  gap: 8px;
}
.review-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.review-summary {
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-1);
  color: var(--text-2);
  font-size: 12px;
  line-height: 1.5;
}

.review-stats {
  display: flex;
  align-items: stretch;
  gap: 8px;
  flex-wrap: wrap;
}
.review-stat {
  min-width: 92px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-1);
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.review-stat span {
  font-size: 11px;
  color: var(--text-3);
}
.review-stat strong {
  font-size: 16px;
  color: var(--text);
}

.review-actions {
  display: flex;
  gap: 6px;
}

.field-diff-section {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  background: var(--bg-1);
}

.field-diff-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  background: var(--bg-2);
  border-bottom: 1px solid var(--border);
  font-size: 12px;
  font-weight: 600;
  color: var(--text-2);
}

.field-diff-list {
  display: grid;
}

.field-diff-row {
  display: grid;
  grid-template-columns: 28px 1fr;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
}
.field-diff-row:last-child {
  border-bottom: 0;
}
.field-diff-row.status-added {
  background: rgba(62, 207, 142, .05);
}
.field-diff-row.status-modified {
  background: rgba(240, 180, 76, .05);
}
.field-diff-row.status-unchanged {
  opacity: .72;
}

.field-diff-main {
  min-width: 0;
}

.field-diff-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 25px;
  font-size: 12px;
  color: var(--text);
}

.field-diff-values {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}
/* 防止 grid 子元素溢出导致内容重叠 */
.field-diff-values > div {
  min-width: 0;
  overflow: hidden;
}
.field-diff-values span {
  display: block;
  margin-bottom: 4px;
  font-size: 11px;
  color: var(--text-3);
}
.field-diff-values pre {
  margin: 0;
  min-height: 42px;
  max-height: 140px;
  overflow: auto;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg);
  color: var(--text-2);
  font-family: 'JetBrains Mono', 'SF Mono', 'Menlo', monospace;
  font-size: 11px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 800px) {
  .field-diff-values {
    grid-template-columns: 1fr;
  }
}

.review-diff-container {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

@media (max-width: 800px) {
  .review-diff-container {
    grid-template-columns: 1fr;
  }
}

.review-pane {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}

.review-pane-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: var(--bg-2);
  border-bottom: 1px solid var(--border);
}

.review-pane-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-2);
}

.review-pane-body {
  max-height: 480px;
  overflow: auto;
  padding: 12px;
  background: var(--bg-1);
}

.review-json {
  font-family: 'JetBrains Mono', 'SF Mono', 'Menlo', monospace;
  font-size: 11px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
  color: var(--text-2);
}

/* JSON 语法高亮 */
.review-json :deep(.json-key) { color: var(--accent); }
.review-json :deep(.json-string) { color: var(--green); }
.review-json :deep(.json-number) { color: #f0b44c; }
.review-json :deep(.json-bool) { color: #f06f4f; }

.input-error :deep(.el-textarea__inner) {
  border-color: var(--red) !important;
  background: rgba(240,96,96,.04);
}
.field-error {
  font-size: 11px;
  color: var(--red);
  display: block;
  margin-top: 4px;
}
.partial-list {
  display: grid;
  gap: 8px;
  max-height: 320px;
  overflow: auto;
}
</style>
