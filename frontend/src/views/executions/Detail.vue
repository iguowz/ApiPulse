<template>
  <div class="page">
    <div class="page-header">
      <div class="flex items-center gap-12">
        <el-button @click="$router.back()">{{ $t('execution_detail.back') }}</el-button>
        <div>
          <div class="flex items-center gap-8">
            <ResultTag :passed="record.passed" />
            <el-tag type="info" size="small">{{ $t(fmt.typeLabel(record.type)) }}</el-tag>
            <el-tag type="info" size="small">{{ $t('executions.trigger_' + (record.trigger || '')) || record.trigger }}</el-tag>
          </div>
          <div class="page-subtitle mono" style="font-size:11px">{{ record.id }}</div>
        </div>
      </div>
      <div class="flex items-center gap-16">
        <el-button @click="downloadReport" :disabled="downloading" :loading="downloading" size="small">{{ $t('execution_detail.download_report') }}</el-button>
        <div class="meta-item">
          <span class="meta-key">{{ $t('execution_detail.steps') }}</span>
          <span class="mono">{{ record.steps?.length || 0 }}</span>
        </div>
        <div class="meta-item">
          <span class="meta-key">{{ $t('common.duration') }}</span>
          <span class="mono">{{ fmt.duration(record.duration_ms) }}</span>
        </div>
        <div class="meta-item">
          <span class="meta-key">{{ $t('execution_detail.started') }}</span>
          <span>{{ fmt.time(record.started_at) }}</span>
        </div>
        <div class="meta-item">
          <span class="meta-key">{{ $t('execution_detail.executor') }}</span>
          <span class="mono text-2">{{ record.executor || '—' }}</span>
        </div>
        <div class="meta-item">
          <span class="meta-key">{{ $t('execution_detail.ip') }}</span>
          <span class="mono text-2" style="font-size:11px">{{ record.execution_ip || '—' }}</span>
        </div>
        <div class="meta-item">
          <span class="meta-key">{{ $t('execution_detail.finished') }}</span>
          <span>{{ fmt.time(record.finished_at) }}</span>
        </div>
      </div>
    </div>

    <div class="page-body" v-loading="loading">
      <!-- 失败原因 -->
      <div v-if="record.failure_reason" class="failure-banner">
        <span style="color:var(--red);font-weight:600">✕</span>
        {{ record.failure_reason }}
      </div>

      <!-- AI 失败诊断 -->
      <div v-if="record.diagnosis_status" class="diagnosis-card" :class="'diagnosis-' + record.diagnosis_status">
        <div class="diagnosis-header">
          <span class="diagnosis-icon">{{ diagnosisIcon }}</span>
          <span class="diagnosis-title">{{ $t('execution_detail.diagnosis_title') }}</span>
          <el-tag
            v-if="record.diagnosis_status === 'queued'"
            type="info"
            size="small"
          >{{ $t('execution_detail.diagnosis_queued') }}</el-tag>
          <el-tag
            v-else-if="record.diagnosis_status === 'running'"
            type="warning"
            size="small"
          >{{ $t('execution_detail.diagnosis_running') }}</el-tag>
          <el-tag
            v-else-if="record.diagnosis_status === 'failed'"
            type="danger"
            size="small"
          >{{ $t('execution_detail.diagnosis_failed') }}</el-tag>
          <el-tag
            v-else-if="record.diagnosis_status === 'done'"
            type="success"
            size="small"
          >{{ $t('execution_detail.diagnosis_done') }}</el-tag>
        </div>

        <!-- 诊断进行中 -->
        <div v-if="['queued', 'running'].includes(record.diagnosis_status)" class="diagnosis-loading">
          <el-progress :percentage="100" :indeterminate="true" :show-text="false" />
          <p class="text-3" style="font-size:12px;margin-top:8px">
            {{ record.diagnosis_status === 'queued' ? $t('execution_detail.diagnosis_queued_hint') : $t('execution_detail.diagnosis_running_hint') }}
          </p>
        </div>

        <!-- 诊断失败 -->
        <div v-else-if="record.diagnosis_status === 'failed'" class="diagnosis-error">
          <p class="text-3" style="font-size:12px">{{ record.diagnosis?.error || $t('execution_detail.diagnosis_failed_hint') }}</p>
        </div>

        <!-- Phase 4: 诊断完成 → 展示根因、解释、建议、置信度 -->
        <div v-else-if="record.diagnosis_status === 'done'" class="diagnosis-result">
          <div class="diagnosis-row">
            <span class="diagnosis-label">{{ $t('execution_detail.diagnosis_root_cause') }}</span>
            <el-tag
              :type="rootCauseTagType(record.diagnosis?.root_cause)"
              size="default"
            >{{ $t('diagnosis.' + (record.diagnosis?.root_cause || 'unknown')) }}</el-tag>
          </div>
          <div class="diagnosis-row">
            <span class="diagnosis-label">{{ $t('execution_detail.diagnosis_confidence') }}</span>
            <span class="mono" style="font-size:14px;font-weight:600;color:var(--text)">
              {{ Math.round((record.diagnosis?.confidence || 0) * 100) }}%
            </span>
          </div>
          <div class="diagnosis-row">
            <span class="diagnosis-label">{{ $t('execution_detail.diagnosis_explanation') }}</span>
            <p class="diagnosis-text">{{ record.diagnosis?.explanation }}</p>
          </div>
          <div class="diagnosis-row">
            <span class="diagnosis-label">{{ $t('execution_detail.diagnosis_suggested_fix') }}</span>
            <p class="diagnosis-text" style="border-left:3px solid var(--accent);padding-left:12px;background:var(--bg-2);border-radius:0 var(--radius) var(--radius) 0">
              {{ record.diagnosis?.suggested_fix }}
            </p>
          </div>
          <div v-if="record.diagnosis?.diagnosed_at" class="diagnosis-row">
            <span class="diagnosis-label">{{ $t('execution_detail.diagnosis_at') }}</span>
            <span class="text-2" style="font-size:11px">{{ fmt.time(record.diagnosis?.diagnosed_at) }}</span>
          </div>
          <div v-if="record.diagnosis_links?.length" class="diagnosis-row">
            <span class="diagnosis-label">关联任务</span>
            <div class="diagnosis-actions">
              <el-button
                v-for="link in record.diagnosis_links"
                :key="link.execution_id + link.api_id + link.created_at"
                size="small"
                @click="$router.push(`/apis/${link.api_id}`)"
              >
                查看 API
              </el-button>
              <el-button size="small" @click="$router.push('/generations?status=pending_review')">进入审核中心</el-button>
              <el-button size="small" @click="$router.push('/import-diffs')">查看 Diff</el-button>
            </div>
          </div>
        </div>
      </div>

      <!-- 步骤卡片（保持自定义，业务特效强） -->
      <div v-for="(step, idx) in record.steps" :key="step.step_id" class="step-card">
        <div
          class="step-header"
          :class="stepHeaderClass(step)"
          @click="toggleStep(idx)"
        >
          <span class="step-num mono">{{ String(idx+1).padStart(2,'0') }}</span>
          <span :class="step.skipped ? 'dot dot-gray' : step.passed ? 'dot dot-green' : 'dot dot-red'"></span>
          <span class="step-id-label mono">{{ step.step_id }}</span>
          <span class="text-2" style="font-size:12px">{{ step.name || step.api_id }}</span>
          <div class="step-header-right">
            <el-tag v-if="step.attempt > 1" type="warning" size="small">{{ $t('execution_detail.retry_count', { count: step.attempt }) }}</el-tag>
            <el-tag v-if="step.loop_index != null" size="small">{{ $t('execution_detail.loop_index', { index: step.loop_index }) }}</el-tag>
            <el-tag v-if="step.skipped" type="info" size="small">{{ $t('execution_detail.skip') }}</el-tag>
            <ResultTag v-else :passed="step.passed" />
            <span class="mono text-2" style="font-size:11px">{{ step.latency_ms }}ms</span>
            <span class="expand-icon text-3">{{ expanded.has(idx) ? '▾' : '▸' }}</span>
          </div>
        </div>

        <div v-if="expanded.has(idx)" class="step-body">
          <div v-if="step.error" class="error-msg">{{ step.error }}</div>

          <!-- 断言结果 -->
          <div v-if="step.assert_results?.length" class="section">
            <div class="section-title">{{ $t('execution_detail.assert_results') }}</div>
            <div class="assert-grid">
              <div v-for="ar in step.assert_results" :key="ar.field" class="assert-item" :class="ar.passed ? 'assert-ok' : 'assert-fail'">
                <div class="assert-top">
                  <span :class="ar.passed ? 'dot dot-green' : 'dot dot-red'"></span>
                  <span class="mono" style="font-size:12px;font-weight:500">{{ ar.field }}</span>
                  <el-tag size="small" effect="plain">{{ assertSourceLabel(ar) }}</el-tag>
                  <el-tag size="small" type="info">{{ ar.operator }}</el-tag>
                  <span :class="[ar.passed ? 'green' : 'red', 'risk-tag']">{{ ar.risk_level }}</span>
                </div>
                <div class="assert-detail-grid">
                  <div>
                    <span class="assert-label">Expected</span>
                    <code class="assert-value">{{ compactJson(ar.expected) }}</code>
                  </div>
                  <div>
                    <span class="assert-label">Actual</span>
                    <code class="assert-value" :class="ar.passed ? 'green' : 'red'">{{ compactJson(ar.actual) }}</code>
                  </div>
                </div>
                <div v-if="ar.error || ar.sql_error" class="assert-error mono">{{ ar.error || ar.sql_error }}</div>
                <div v-if="ar.description" class="text-3" style="font-size:11px;margin-top:2px">{{ ar.description }}</div>
              </div>
            </div>
          </div>

          <!-- 提取变量 -->
          <div v-if="Object.keys(step.extracted_vars||{}).length" class="section">
            <div class="section-title">{{ $t('execution_detail.extracted_vars') }}</div>
            <div class="kv-list">
              <div v-for="(v, k) in step.extracted_vars" :key="k" class="kv-row">
                <span class="mono kv-key">{{ k }}</span>
                <span class="mono kv-val">{{ JSON.stringify(v) }}</span>
              </div>
            </div>
          </div>

          <div class="req-resp-grid">
            <div class="section">
              <div class="section-title">{{ $t('api_detail.request') }}</div>
              <div class="kv-list" style="margin-bottom:8px">
                <div class="kv-row">
                  <span class="kv-key">METHOD</span>
                  <span :class="methodClass(step.request_sent?.method)">{{ step.request_sent?.method }}</span>
                </div>
                <div class="kv-row">
                  <span class="kv-key">URL</span>
                  <span class="mono text-2" style="font-size:11px;word-break:break-all">{{ step.request_sent?.url }}</span>
                </div>
              </div>
              <div v-if="step.request_sent?.body">
                <div class="section-title" style="margin-bottom:4px">{{ $t('api_detail.body') }}</div>
                <pre class="code-block" style="max-height:180px;overflow:auto">{{ jsonPretty(step.request_sent.body) }}</pre>
              </div>
            </div>

            <div class="section">
              <div class="section-title">{{ $t('api_detail.response') }}</div>
              <div class="kv-list" style="margin-bottom:8px">
                <div class="kv-row">
                  <span class="kv-key">STATUS</span>
                  <el-tag :type="(step.response_received?.status_code||0) < 300 ? 'success' : 'danger'" size="small">
                    {{ step.response_received?.status_code }}
                  </el-tag>
                </div>
                <div class="kv-row">
                  <span class="kv-key">LATENCY</span>
                  <span class="mono text-2">{{ step.latency_ms }}ms</span>
                </div>
              </div>
              <div v-if="step.response_received?.body">
                <div class="section-title" style="margin-bottom:4px">{{ $t('api_detail.body') }}</div>
                <pre class="code-block" style="max-height:240px;overflow:auto">{{ jsonPretty(step.response_received.body) }}</pre>
              </div>
            </div>
          </div>
        </div>
      </div>

      <el-empty v-if="!loading && !record.steps?.length" :description="$t('execution_detail.no_steps')" />
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, reactive, watch, computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { executionApi } from '@/api'
import { useToastStore } from '@/stores'
import { fmt, jsonPretty, methodClass } from '@/utils'
import ResultTag from '@/components/ResultTag.vue'
import { useWebSocket } from '@/composables/useWebSocket'

const { t } = useI18n()
const toast = useToastStore()
const route   = useRoute()
const record  = ref({})
const loading = ref(false)
const expanded = reactive(new Set([0]))

function toggleStep(i) {
  if (expanded.has(i)) expanded.delete(i)
  else expanded.add(i)
}

function stepHeaderClass(step) {
  if (step.skipped) return 'step-skip'
  return step.passed ? 'step-ok' : 'step-fail'
}

async function load() {
  loading.value = true
  try {
    record.value = await executionApi.get(route.params.id)
    if (record.value.steps) {
      expanded.clear()
      const failIdx = record.value.steps.findIndex(s => !s.passed && !s.skipped)
      expanded.add(failIdx >= 0 ? failIdx : 0)
    }
  } catch (e) {
    toast.error(e.message || t('toast.load_failed'))
  } finally { loading.value = false }
}

// ── Markdown 测试报告下载 ──
const downloading = ref(false)
async function downloadReport() {
  downloading.value = true
  try {
    const blob = await executionApi.getReport(route.params.id)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `execution_report_${route.params.id.slice(0, 8)}.md`
    a.click()
    URL.revokeObjectURL(url)
    toast.success(t('execution_detail.report_downloaded'))
  } catch (e) {
    toast.error(e.message || t('execution_detail.report_failed'))
  } finally { downloading.value = false }
}

// ── AI 诊断图标 ──
const diagnosisIcon = computed(() => {
  const map = { queued: '⏳', running: '🔄', done: '✅', failed: '❌' }
  return map[record.value.diagnosis_status] || '🔍'
})

// 根因 → el-tag type 映射
function rootCauseTagType(rootCause) {
  const map = {
    env_mismatch: 'warning',
    timeout: 'danger',
    assertion_error: 'danger',
    api_change: '',
    data_issue: 'warning',
    unknown: 'info',
  }
  return map[rootCause] || 'info'
}

function compactJson(value, max = 160) {
  const text = value === undefined ? 'undefined' : JSON.stringify(value)
  const normalized = text ?? 'null'
  return normalized.length > max ? normalized.slice(0, max) + '...' : normalized
}

function assertSourceLabel(ar) {
  const source = ar.origin_source || ar.source
  if (source === 'sql') return ar.sql_name ? `SQL:${ar.sql_name}` : 'SQL'
  const map = {
    response: 'Response',
    status: 'Status',
    header: 'Header',
    performance: 'Performance',
    step: 'Step',
  }
  return map[ar.source] || 'Response'
}

// ── WebSocket 实时接收诊断完成事件 ──
function onDiagnosisWs(data) {
  // diagnosis_done: 诊断完成，刷新当前记录获取最新诊断结果
  if (data?.type === 'diagnosis_done' && data?.execution_id === record.value.id) {
    // P0-5: 此前这里本地拼凑 diagnosis 并把 suggested_fix 硬编码为 ''，
    // 导致用户永远看不到 AI 给出的修复建议（诊断价值被丢弃）。
    // 修复：不本地拼凑不完整数据，直接触发 load() 从后端拉取完整 diagnosis
    // （后端 diagnose 方法已回写完整字段含 suggested_fix/confidence/explanation）。
    record.value.diagnosis_status = 'done'
    // 延迟 1s 再完整刷新，确保后端已写入
    setTimeout(() => load(), 1000)
  }
  // P0-5: 诊断触发重新分析事件 → 提示用户接口将被自动重新分析
  if (data?.type === 'diagnosis_triggered_reanalyze' && data?.api_id) {
    // 诊断触发重新分析，用户可在 API 详情页查看结果
  }
}

onMounted(async () => {
  await load()
  // 建立 WebSocket 连接接收 AI 诊断结果推送（必须在 load() 完成后，确保 project_id 已加载）
  useWebSocket(`/ai-analysis?project_id=${encodeURIComponent(record.value?.project_id || 'default')}`, onDiagnosisWs)
})
// 路由参数变化时重新加载（导航到另一个执行记录详情时）
watch(() => route.params.id, () => { if (route.params.id) load() })
</script>

<style scoped>
.meta-item   { display: flex; flex-direction: column; align-items: flex-end; gap: 2px; }
.meta-key    { font-size: 10px; color: var(--text-3); text-transform: uppercase; letter-spacing: .06em; }

.failure-banner {
  padding: 10px 16px; margin-bottom: 12px;
  background: rgba(240,96,96,.08); border: 1px solid rgba(240,96,96,.25);
  border-radius: var(--radius); font-size: 13px; color: var(--text-2);
  display: flex; align-items: center; gap: 8px;
}

.step-card { border: 1px solid var(--border); border-radius: var(--radius-lg); margin-bottom: 8px; overflow: hidden; }

.step-header {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 14px; cursor: pointer; transition: background .12s;
  border-left: 3px solid transparent;
}
.step-header:hover { background: var(--bg-hover); }
.step-ok   { border-left-color: var(--green);  background: rgba(62,207,142,.04); }
.step-fail { border-left-color: var(--red);    background: rgba(240,96,96,.04); }
.step-skip { border-left-color: var(--text-3); background: transparent; }

.step-num       { font-size: 11px; color: var(--text-3); min-width: 20px; }
.step-id-label  { font-size: 12px; font-weight: 600; color: var(--text); min-width: 80px; }
.step-header-right { margin-left: auto; display: flex; align-items: center; gap: 8px; }
.expand-icon    { font-size: 10px; }

.step-body { padding: 16px; border-top: 1px solid var(--border); }

.error-msg {
  padding: 8px 12px; margin-bottom: 12px;
  background: rgba(240,96,96,.08); border-left: 3px solid var(--red);
  border-radius: var(--radius); font-family: var(--font-mono); font-size: 12px; color: var(--red);
}

.section { margin-bottom: 16px; }
.section-title { font-size: 10px; font-weight: 600; color: var(--text-3); text-transform: uppercase; letter-spacing: .07em; margin-bottom: 8px; }

.assert-grid { display: flex; flex-direction: column; gap: 6px; }
.assert-item {
  padding: 8px 12px; border-radius: var(--radius);
  border: 1px solid var(--border);
}
.assert-ok   { border-color: rgba(62,207,142,.2);  background: rgba(62,207,142,.04); }
.assert-fail { border-color: rgba(240,96,96,.2);   background: rgba(240,96,96,.04); }
.assert-top  { display: flex; align-items: center; gap: 8px; }
.assert-detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: 8px; padding-left: 18px; }
.assert-label { display: block; font-size: 10px; font-weight: 600; color: var(--text-3); text-transform: uppercase; letter-spacing: .06em; margin-bottom: 2px; }
.assert-value { display: block; font-size: 11px; color: var(--text-2); white-space: pre-wrap; word-break: break-all; background: var(--bg-2); border: 1px solid var(--border); border-radius: 4px; padding: 4px 6px; }
.assert-error { margin-top: 6px; margin-left: 18px; color: var(--red); font-size: 11px; word-break: break-word; }
.risk-tag    { font-size: 10px; font-weight: 600; font-family: var(--font-mono); margin-left: auto; }

.kv-list { display: flex; flex-direction: column; gap: 4px; }
.kv-row  { display: flex; align-items: flex-start; gap: 12px; padding: 3px 0; }
.kv-key  { font-size: 10px; font-weight: 700; color: var(--text-3); text-transform: uppercase; letter-spacing: .06em; min-width: 60px; padding-top: 1px; }
.kv-val  { font-size: 12px; color: var(--text-2); word-break: break-all; }

.req-resp-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 860px) { .req-resp-grid { grid-template-columns: 1fr; } }
@media (max-width: 860px) { .assert-detail-grid { grid-template-columns: 1fr; } }

/* ── AI 诊断卡片 ── */
.diagnosis-card {
  padding: 16px; margin-bottom: 12px;
  border-radius: var(--radius-lg); border: 1px solid var(--border);
}
.diagnosis-queued { border-color: var(--border);  background: var(--bg-2); }
.diagnosis-running { border-color: rgba(224,175,54,.3); background: rgba(224,175,54,.04); }
.diagnosis-done { border-color: rgba(62,207,142,.3); background: rgba(62,207,142,.04); }
.diagnosis-failed { border-color: rgba(240,96,96,.3); background: rgba(240,96,96,.04); }

.diagnosis-header { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.diagnosis-icon { font-size: 16px; }
.diagnosis-title { font-size: 14px; font-weight: 600; color: var(--text-1); }

.diagnosis-loading { padding: 12px 0; }

.diagnosis-error { padding: 8px 12px; }

.diagnosis-result { display: flex; flex-direction: column; gap: 12px; }
.diagnosis-row { display: flex; flex-direction: column; gap: 4px; }
.diagnosis-label { font-size: 10px; font-weight: 600; color: var(--text-3); text-transform: uppercase; letter-spacing: .07em; }
.diagnosis-text { font-size: 13px; color: var(--text-2); line-height: 1.55; padding: 8px 12px; background: var(--bg-2); border-radius: var(--radius); }
.diagnosis-actions { display: flex; flex-wrap: wrap; gap: 8px; }
</style>
