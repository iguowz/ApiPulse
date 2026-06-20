<template>
  <div class="page">
    <!-- 需求6: 嵌入 Dashboard 标签页时隐藏独立页 header -->
    <div v-if="!embedded" class="page-header">
      <div>
        <div class="page-title">{{ $t('quality.title') }}</div>
        <div class="page-subtitle">{{ $t('quality.subtitle') }}</div>
      </div>
      <div class="flex gap-2">
        <!-- 团队活跃度天数切换 -->
        <el-select v-model="activityDays" size="small" style="width:80px" @change="fetchTeamActivity">
          <el-option :value="7" label="7d" />
          <el-option :value="30" label="30d" />
          <el-option :value="90" label="90d" />
        </el-select>
        <el-button size="small" @click="refresh" :loading="loading">{{ $t('common.refresh') }}</el-button>
      </div>
    </div>

    <!-- 首次加载占位 -->
    <div v-if="loadingInit" class="page-body" style="display:flex;align-items:center;justify-content:center;min-height:400px">
      <span class="spinner" style="width:32px;height:32px;border-width:3px"></span>
    </div>

    <div class="page-body" v-else>
      <!-- KPI 卡片：6 项核心质量指标 -->
      <div class="kpi-grid">
        <div class="kpi-card" v-for="k in kpis" :key="k.label">
          <el-tooltip :content="k.desc" placement="top" :show-after="400">
            <div class="kpi-label kpi-label--tip">{{ k.label }}</div>
          </el-tooltip>
          <div class="kpi-value" :style="{ color: k.color }">{{ k.value }}</div>
          <div class="kpi-sub" :class="k.subColor">{{ k.sub }}</div>
        </div>
      </div>

      <!-- Row 1: 团队活跃度图表 -->
      <el-card style="margin-bottom:12px">
        <template #header>
          <el-tooltip :content="$t('quality.desc_team_activity')" placement="top" :show-after="400">
            <span class="card-title card-title--tip">{{ $t('quality.team_activity', { days: activityDays }) }}</span>
          </el-tooltip>
        </template>
        <VChart v-if="activityTrend.length" :option="activityOption" autoresize style="height:300px" />
        <div v-else class="empty-chart">{{ $t('quality.no_data') }}</div>
      </el-card>

      <!-- Row 2: 健康评分 (最佳/最差) -->
      <div class="charts-row">
        <el-card>
          <template #header>
            <el-tooltip :content="$t('quality.desc_health_best')" placement="top" :show-after="400">
              <span class="card-title card-title--tip">{{ $t('quality.health_best') }}</span>
            </el-tooltip>
          </template>
          <el-table :data="healthBest" size="small" :empty-text="$t('quality.no_data')" max-height="260">
            <el-table-column label="API" min-width="160">
              <template #default="{ row }">
                <div style="display:flex;align-items:center;gap:4px">
                  <span style="font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0">{{ row.name || (row.method + ' ' + row.path) }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column :label="$t('quality.health_score')" width="70" align="center">
              <template #default="{ row }">
                <span class="health-score" :class="`grade-${row.grade}`">{{ row.health_score }}</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-card>
          <template #header>
            <el-tooltip :content="$t('quality.desc_health_worst')" placement="top" :show-after="400">
              <span class="card-title card-title--tip">{{ $t('quality.health_worst') }}</span>
            </el-tooltip>
          </template>
          <el-table :data="healthWorst" size="small" :empty-text="$t('quality.no_data')" max-height="260">
            <el-table-column label="API" min-width="160">
              <template #default="{ row }">
                <div style="display:flex;align-items:center;gap:4px">
                  <span style="font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0">{{ row.name || (row.method + ' ' + row.path) }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column :label="$t('quality.health_score')" width="70" align="center">
              <template #default="{ row }">
                <span class="health-score" :class="`grade-${row.grade}`">{{ row.health_score }}</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </div>

      <!-- Row 3: 30天质量趋势 -->
      <el-card style="margin-top:12px">
        <template #header>
          <div class="flex items-center justify-between">
            <el-tooltip :content="$t('quality.desc_quality_trend')" placement="top" :show-after="400">
              <span class="card-title card-title--tip">{{ $t('quality.quality_trend') }}</span>
            </el-tooltip>
            <el-radio-group v-model="trendGran" size="small" @change="fetchTrends">
              <el-radio-button value="hour">时</el-radio-button>
              <el-radio-button value="day">日</el-radio-button>
            </el-radio-group>
          </div>
        </template>
        <VChart v-if="trendData.length" :option="trendOption" autoresize style="height:280px" />
        <div v-else class="empty-chart">{{ $t('quality.no_data') }}</div>
      </el-card>

      <!-- Row 4: SLA 摘要 -->
      <el-card style="margin-top:12px" v-if="slaData">
        <template #header>
          <el-tooltip :content="$t('quality.desc_sla_summary')" placement="top" :show-after="400">
            <span class="card-title card-title--tip">{{ $t('quality.sla_summary') }}</span>
          </el-tooltip>
        </template>
        <div class="sla-row">
          <!-- 全局 SLA 大数字 -->
          <div class="sla-gauge">
            <div class="sla-big-num" :style="{ color: slaData.global_sla_pct >= 99 ? 'var(--green)' : slaData.global_sla_pct >= 95 ? 'var(--amber)' : 'var(--red)' }">
              {{ slaData.global_sla_pct }}%
            </div>
            <div class="sla-label">{{ $t('quality.sla_global') }}</div>
            <div class="sla-sub">{{ $t('quality.sla_met', { met: slaData.sla_met_count, total: slaData.total_api_count }) }}</div>
          </div>
          <!-- 不达标 API 列表 -->
          <div class="sla-list">
            <div v-if="slaBelowTarget.length === 0" class="sla-all-good">所有 API SLA 均达标 (≥99%)</div>
            <el-table v-else :data="slaBelowTarget" size="small" max-height="200">
              <el-table-column label="API" min-width="160">
                <template #default="{ row }">
                  <span style="font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0">{{ row.name || (row.method + ' ' + row.path) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="SLA" width="80" align="center">
                <template #default="{ row }">
                  <span style="color:var(--red);font-weight:600">{{ row.sla_pct }}%</span>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
// 需求6: 支持嵌入 Dashboard 标签页，隐藏独立页面头
defineProps({ embedded: { type: Boolean, default: false } })
import { useI18n } from 'vue-i18n'
import { use } from 'echarts/core'
import { LineChart, BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import VChart from 'vue-echarts'
import { statsApi } from '@/api'
import { useProjectStore, useToastStore } from '@/stores'
const toast = useToastStore()
const { t } = useI18n()

use([LineChart, BarChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const projectStore = useProjectStore()
const loading = ref(false)
const loadingInit = ref(true)

// 团队活跃度
const activityDays = ref(30)
const activityData = ref(null)

// 健康评分
const healthScores = ref([])

// 趋势
const trendData = ref([])
const trendGran = ref('day')

// SLA
const slaData = ref(null)
const slaPeriod = ref('30d')

// ── KPI 卡片计算 ──────────────────────────────────────
const kpis = computed(() => {
  const o = overview.value
  const a = activityData.value
  const passRate = o.executions?.pass_rate_pct ?? 0
  return [
    {
      label: t('quality.kpi_total_apis'),
      desc: t('quality.desc_kpi_total_apis'),
      value: o.apis?.total ?? 0,
      sub: `${o.apis?.statuses?.done ?? 0} ${t('dashboard.pie_done')}`,
      subColor: 'text-2', color: 'var(--text)',
    },
    {
      label: t('quality.kpi_total_scenarios'),
      desc: t('quality.desc_kpi_total_scenarios'),
      value: o.scenarios?.total ?? 0,
      sub: `${o.scenarios?.ai_generated ?? 0} AI / ${(o.scenarios?.total ?? 0) - (o.scenarios?.ai_generated ?? 0)} 手动`,
      subColor: 'text-2', color: 'var(--text)',
    },
    {
      label: t('quality.kpi_active_monitors'),
      desc: t('quality.desc_kpi_active_monitors'),
      value: o.monitors?.active ?? 0,
      sub: `${o.alerts?.total ?? 0} ${t('dashboard.kpi_monitor_sub', { alerts: '' }).split(' ').pop() || '告警'}`,
      subColor: 'text-2', color: 'var(--text)',
    },
    {
      label: t('quality.kpi_pass_rate'),
      desc: t('quality.desc_kpi_pass_rate'),
      value: `${passRate}%`,
      sub: `${t('quality.total_execs')}: ${o.executions?.total ?? 0}`,
      subColor: passRate >= 80 ? 'green' : passRate >= 50 ? 'amber' : 'red',
      color: passRate >= 80 ? 'var(--green)' : passRate >= 50 ? 'var(--amber)' : 'var(--red)',
    },
    {
      label: t('quality.kpi_ai_analyses'),
      desc: t('quality.desc_kpi_ai_analyses'),
      value: a?.total_ai_analyses ?? '—',
      sub: `${activityDays.value}d`,
      subColor: 'text-2', color: '#4f8ef7',
    },
    {
      label: t('quality.kpi_active_days'),
      desc: t('quality.desc_kpi_active_days'),
      value: a?.active_days ?? '—',
      sub: `/${activityDays.value}d`,
      subColor: a?.active_days >= (activityDays.value * 0.5) ? 'green' : 'amber',
      color: a?.active_days >= (activityDays.value * 0.5) ? 'var(--green)' : 'var(--amber)',
    },
  ]
})

// 健康评分排序：最佳/最差各 5
const healthBest = computed(() => {
  return [...healthScores.value].sort((a, b) => b.health_score - a.health_score).slice(0, 5)
})
const healthWorst = computed(() => {
  return [...healthScores.value].sort((a, b) => a.health_score - b.health_score).slice(0, 5)
})

// SLA 不达标列表
const slaBelowTarget = computed(() => {
  if (!slaData.value?.items) return []
  return slaData.value.items.filter(r => !r.sla_met).slice(0, 5)
})

// ── ECharts: 团队活跃度堆叠柱状图 ──────────────────────
const activityTrend = computed(() => activityData.value?.daily_trend || [])

const activityOption = computed(() => {
  const data = activityTrend.value
  const labels = data.map(d => d.date.slice(5)) // MM-DD
  const scenarioData = data.map(d => d.scenario || 0)
  const singleData = data.map(d => d.single || 0)
  const monitorData = data.map(d => d.monitor || 0)
  const aiData = data.map(d => d.ai_analysis || 0)

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: '#1c2030', borderColor: '#2a2f45',
      textStyle: { color: '#e2e6f0', fontSize: 12 },
    },
    legend: {
      data: [t('quality.activity_scenario'), t('quality.activity_single'), t('quality.activity_monitor'), t('quality.activity_ai')],
      bottom: 0, textStyle: { color: '#8b91a8', fontSize: 11 },
    },
    grid: { left: 48, right: 24, top: 12, bottom: 36 },
    xAxis: {
      type: 'category', data: labels,
      axisLabel: { color: '#555d78', fontSize: 10 },
      axisLine: { lineStyle: { color: '#2a2f45' } },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#555d78', fontSize: 10 },
      splitLine: { lineStyle: { color: '#1c2030' } },
    },
    series: [
      { name: t('quality.activity_scenario'), type: 'bar', stack: 'total', data: scenarioData, itemStyle: { color: '#4f8ef7' }, barWidth: '50%' },
      { name: t('quality.activity_single'), type: 'bar', stack: 'total', data: singleData, itemStyle: { color: '#3ecf8e' } },
      { name: t('quality.activity_monitor'), type: 'bar', stack: 'total', data: monitorData, itemStyle: { color: '#f0a040' } },
      { name: t('quality.activity_ai'), type: 'bar', stack: 'total', data: aiData, itemStyle: { color: '#a478e8' } },
    ],
  }
})

// ── ECharts: 30天质量趋势 ─────────────────────────────
const trendOption = computed(() => {
  const data = trendData.value
  const labels = data.map(d => trendGran.value === 'hour' ? d.bucket.slice(11) : d.bucket.slice(5))
  const passRates = data.map(d => d.pass_rate_pct)

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1c2030', borderColor: '#2a2f45',
      textStyle: { color: '#e2e6f0', fontSize: 12 },
      formatter: params => {
        const p = params[0]
        const d = data[p.dataIndex]
        return `${d.bucket}<br/>${t('quality.trend_pass_rate')}: ${d.pass_rate_pct}%<br/>${t('quality.total_execs')}: ${d.total} (${t('common.pass')} ${d.passed} / ${t('common.fail')} ${d.failed})`
      },
    },
    grid: { left: 48, right: 48, top: 12, bottom: 28 },
    xAxis: {
      type: 'category', data: labels,
      axisLabel: { color: '#555d78', fontSize: 10 },
      axisLine: { lineStyle: { color: '#2a2f45' } },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value', min: 0, max: 100,
      axisLabel: { color: '#555d78', fontSize: 10, formatter: '{value}%' },
      splitLine: { lineStyle: { color: '#1c2030' } },
    },
    series: [{
      name: t('quality.trend_pass_rate'),
      type: 'line', smooth: true, symbol: 'circle', symbolSize: 4,
      data: passRates,
      lineStyle: { color: '#3ecf8e', width: 2 },
      itemStyle: { color: '#3ecf8e' },
      areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
        colorStops: [{ offset: 0, color: 'rgba(62,207,142,.25)' }, { offset: 1, color: 'transparent' }] } },
    }],
  }
})

// ── 健康评分数据（用于 overview KPI） ──────────────────
const overview = ref({})

// HTTP method → ElTag type
function methodTagType(m) {
  const map = { GET: 'info', POST: 'primary', PUT: 'warning', DELETE: 'danger', PATCH: 'success' }
  return map[m] || 'info'
}

// ── 数据获取 ──────────────────────────────────────────
async function fetchOverview() {
  try {
    overview.value = await statsApi.overview(projectStore.current)
  } catch { overview.value = {} }
}

async function fetchTeamActivity() {
  try {
    activityData.value = await statsApi.teamActivity(projectStore.current, activityDays.value)
  } catch { activityData.value = null }
}

async function fetchHealthScores() {
  try {
    healthScores.value = (await statsApi.healthScores(projectStore.current, 50)).items || []
  } catch { healthScores.value = [] }
}

async function fetchTrends() {
  try {
    trendData.value = (await statsApi.trends(projectStore.current, '30d', trendGran.value)).trend || []
  } catch { trendData.value = [] }
}

async function fetchSla() {
  try {
    slaData.value = await statsApi.sla(projectStore.current, slaPeriod.value)
  } catch { slaData.value = null }
}

async function refresh() {
  if (loading.value) return
  loading.value = true
  try {
    await Promise.all([
      fetchOverview(),
      fetchTeamActivity(),
      fetchHealthScores(),
      fetchTrends(),
      fetchSla(),
    ])
  } catch (e) {
    toast.error(e.message || t('toast.refresh_failed'))
  } finally {
    loading.value = false
  }
}

watch(() => projectStore.current, () => { refresh() })

onMounted(async () => {
  await refresh()
  loadingInit.value = false
})
</script>

<style scoped>
.kpi-grid {
  display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; margin-bottom: 16px;
}
.kpi-card {
  background: var(--bg-2); border: 1px solid var(--border); border-radius: var(--radius-lg);
  padding: 18px 20px;
}
.kpi-label { font-size: 11px; font-weight: 600; color: var(--text-3); text-transform: uppercase; letter-spacing: .06em; }
.kpi-label--tip { cursor: help; border-bottom: 1px dashed var(--border-2); }
.kpi-value { font-size: 30px; font-weight: 700; font-family: var(--font-mono); margin: 8px 0 4px; line-height: 1; }
.kpi-sub   { font-size: 12px; }

/* 卡片标题 tooltip 样式 */
.card-title--tip { cursor: help; border-bottom: 1px dashed var(--border-2); }

.charts-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }

.empty-chart {
  height: 260px; display: flex; align-items: center; justify-content: center;
  color: var(--text-3); font-size: 13px;
}

/* SLA 布局 */
.sla-row { display: flex; gap: 24px; align-items: flex-start; }
.sla-gauge {
  flex-shrink: 0; width: 200px; text-align: center;
  padding: 16px; background: var(--bg-1); border-radius: var(--radius-lg);
}
.sla-big-num { font-size: 44px; font-weight: 700; font-family: var(--font-mono); }
.sla-label { font-size: 12px; color: var(--text-3); margin-top: 4px; }
.sla-sub { font-size: 11px; color: var(--text-2); margin-top: 2px; }
.sla-list { flex: 1; min-width: 0; }
.sla-all-good {
  padding: 40px 0; text-align: center; color: var(--green);
  font-size: 14px; font-weight: 600;
}

/* 健康评分 */
.health-score {
  font-size: 16px; font-weight: 700; font-family: var(--font-mono);
}
.grade-excellent { color: var(--green); }
.grade-good      { color: #4f8ef7; }
.grade-fair      { color: var(--amber); }
.grade-poor      { color: var(--red); }

@media (max-width: 1100px) {
  .kpi-grid { grid-template-columns: repeat(3, 1fr); }
  .charts-row { grid-template-columns: 1fr; }
  .sla-row { flex-direction: column; }
}
</style>
