<template>
  <div class="page">
    <div class="page-header">
      <div>
        <div class="page-title">{{ $t('dashboard.title') }}</div>
        <div class="page-subtitle">{{ $t('dashboard.subtitle') }}</div>
      </div>
      <div class="flex gap-2">
        <!-- 趋势时间窗口切换 -->
        <el-select v-model="trendPeriod" size="small" style="width:90px" @change="fetchTrends">
          <el-option label="24h" value="24h" />
          <el-option label="7d" value="7d" />
          <el-option label="30d" value="30d" />
        </el-select>
        <el-button size="small" @click="refresh" :loading="loading">{{ $t('common.refresh') }}</el-button>
      </div>
    </div>

    <!-- 首次加载时的全页 loading 占位 -->
    <div v-if="loadingInit" class="page-body" style="display:flex;align-items:center;justify-content:center;min-height:400px">
      <span class="spinner" style="width:32px;height:32px;border-width:3px"></span>
    </div>

    <div class="page-body" v-else>
      <!-- 需求5/6: Dashboard 标签页整合概览、质量报告、知识库 -->
      <el-tabs v-model="dashboardTab">
        <el-tab-pane :label="$t('dashboard.tab_overview')" name="overview">
      <el-card v-if="queueSummary.totalPending || queueSummary.totalDlq" class="queue-strip">
        <div class="queue-strip-inner">
          <div>
            <span class="card-title">{{ $t('dashboard.queue_status') }}</span>
            <span class="text-2 queue-strip-sub">{{ $t('dashboard.queue_status_sub', { pending: queueSummary.totalPending, dlq: queueSummary.totalDlq }) }}</span>
          </div>
          <div class="queue-strip-actions">
            <el-tag v-for="q in queueHighlights" :key="q.key" size="small" :type="q.dlq ? 'danger' : q.pending > 0 ? 'warning' : 'info'">
              {{ q.name }} {{ q.pending }}<span v-if="q.dlq"> / {{ $t('dashboard.queue_dlq_short') }} {{ q.dlq }}</span>
            </el-tag>
            <el-button size="small" @click="dashboardTab = 'workbench'">{{ $t('dashboard.view_workbench') }}</el-button>
          </div>
        </div>
      </el-card>
      <!-- KPI cards -->
      <div class="kpi-grid">
        <div class="kpi-card" v-for="k in kpis" :key="k.label">
          <el-tooltip :content="k.desc" placement="top" :show-after="400">
            <div class="kpi-label kpi-label--tip">{{ k.label }}</div>
          </el-tooltip>
          <div class="kpi-value" :style="{ color: k.color }">{{ k.value }}</div>
          <div class="kpi-sub" :class="k.subColor">{{ k.sub }}</div>
        </div>
      </div>

      <!-- Row 1: 趋势图 + 健康评分分布 -->
      <div class="charts-row">
        <el-card class="chart-card">
          <template #header>
            <div class="flex items-center justify-between">
              <el-tooltip :content="$t('dashboard.desc_chart_trend')" placement="top" :show-after="400">
              <span class="card-title">{{ trendTitle }}</span>
            </el-tooltip>
              <el-radio-group v-model="trendGran" size="small" @change="fetchTrends">
                <el-radio-button value="hour">{{ $t('dashboard.gran_hour') }}</el-radio-button>
                <el-radio-button value="day">{{ $t('dashboard.gran_day') }}</el-radio-button>
              </el-radio-group>
            </div>
          </template>
          <VChart v-if="trendData.length" :option="trendOption" autoresize style="height:260px" />
          <div v-else class="empty-chart">{{ $t('common.no_data') }}</div>
        </el-card>

        <el-card class="chart-card chart-card-sm">
          <template #header>
            <el-tooltip :content="$t('dashboard.desc_pie_analysis')" placement="top" :show-after="400">
              <span class="card-title">{{ $t('dashboard.pie_title') }}</span>
            </el-tooltip>
          </template>
          <VChart :option="pieOption" autoresize style="height:260px" />
        </el-card>
      </div>

      <!-- Row 2: Top 失败 API + SLA 摘要 -->
      <div class="charts-row" style="margin-top:12px">
        <!-- Top failing APIs -->
        <el-card>
          <template #header>
            <div class="flex items-center justify-between">
              <el-tooltip :content="$t('dashboard.desc_top_failing')" placement="top" :show-after="400">
                <span class="card-title">{{ $t('dashboard.top_failing_title', { h: topFailingHours }) }}</span>
              </el-tooltip>
              <el-select v-model="topFailingHours" size="small" style="width:80px" @change="fetchTopFailing">
                <el-option :value="1" label="1h" />
                <el-option :value="6" label="6h" />
                <el-option :value="24" label="24h" />
                <el-option :value="168" label="7d" />
              </el-select>
            </div>
          </template>
          <el-table :data="topFailing" size="small" :empty-text="$t('common.no_data')" max-height="300">
            <el-table-column label="API" min-width="180">
              <template #default="{ row }">
                <span style="font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0">{{ row.name || (row.method + ' ' + row.path) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="失败次数" width="90" align="center">
              <template #default="{ row }">
                <span style="color:var(--red);font-weight:600">{{ row.fail_count }}</span>
              </template>
            </el-table-column>
            <el-table-column label="平均耗时" width="100" align="center">
              <template #default="{ row }">
                <span class="mono text-2" style="font-size:11px">{{ row.avg_duration_ms }}ms</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <!-- SLA 报告 -->
        <el-card v-if="slaData">
          <template #header>
            <div class="flex items-center justify-between">
              <el-tooltip :content="$t('dashboard.desc_sla_report')" placement="top" :show-after="400">
                <span class="card-title">{{ $t('dashboard.sla_title', { period: slaPeriod }) }}</span>
              </el-tooltip>
              <el-select v-model="slaPeriod" size="small" style="width:80px" @change="fetchSla">
                <el-option value="7d" label="7d" />
                <el-option value="30d" label="30d" />
                <el-option value="90d" label="90d" />
              </el-select>
            </div>
          </template>
          <!-- 全局 SLA 大数字 -->
          <div style="text-align:center;padding:8px 0 12px">
            <div style="font-size:40px;font-weight:700;font-family:var(--font-mono)" :style="{ color: slaData.global_sla_pct >= 99 ? 'var(--green)' : slaData.global_sla_pct >= 95 ? 'var(--amber)' : 'var(--red)' }">
              {{ slaData.global_sla_pct }}%
            </div>
            <div style="font-size:11px;color:var(--text-3);margin-top:2px">
              {{ $t('dashboard.sla_met_summary', { met: slaData.sla_met_count, total: slaData.total_api_count }) }}
            </div>
          </div>
          <!-- 不达标 API 列表 -->
          <el-table :data="slaData.items.filter(r => !r.sla_met).slice(0, 5)" size="small" :empty-text="$t('dashboard.sla_all_met')" max-height="220">
            <el-table-column label="API" min-width="160">
              <template #default="{ row }">
                <span style="font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0">{{ row.name || (row.method + ' ' + row.path) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="SLA" width="65" align="center">
              <template #default="{ row }">
                <span style="color:var(--red);font-weight:600">{{ row.sla_pct }}%</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </div>

      <!-- Row 3: 健康评分 -->
      <el-card style="margin-top:12px">
        <template #header>
          <el-tooltip :content="$t('dashboard.desc_health_score')" placement="top" :show-after="400">
            <span class="card-title">{{ $t('dashboard.health_score_title') }}</span>
          </el-tooltip>
        </template>
        <div class="health-grid" v-if="healthScores.length">
          <div v-for="h in healthScores.slice(0, 8)" :key="h.api_id" class="health-chip" @click="router.push(`/apis/${h.api_id}`)">
            <el-tooltip :content="`${h.name || (h.method + ' ' + h.path)}\n成功率:${h.pass_rate_pct}% 延迟:${h.avg_latency_ms}ms`" placement="top">
              <div style="display:flex;align-items:center;gap:8px;cursor:pointer;overflow:hidden">
                <el-tag size="small" :type="methodTagType(h.method)">{{ h.method }}</el-tag>
                <span style="font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0;font-weight:500">{{ h.name || h.path }}</span>
              </div>
            </el-tooltip>
            <div class="health-score" :class="`grade-${h.grade}`">{{ h.health_score }}</div>
          </div>
        </div>
        <div v-else style="padding:20px;text-align:center;color:var(--text-3);font-size:13px">{{ $t('dashboard.no_health_data') }}</div>
      </el-card>

      <!-- Row 4: Audit timeline -->
      <el-card style="margin-top:12px" v-if="recentAuditLogs.length">
        <template #header>
          <div class="flex items-center justify-between">
            <el-tooltip :content="$t('dashboard.desc_recent_audit')" placement="top" :show-after="400">
              <span class="card-title">{{ $t('dashboard.recent_audit_title') }}</span>
            </el-tooltip>
          </div>
        </template>
        <el-table :data="recentAuditLogs" size="small" max-height="250">
          <el-table-column :label="$t('common.actions')" width="100">
            <template #default="{ row }">
              <el-tag :type="actionTagType(row.action)" size="small">{{ actionLabel(row.action) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="$t('dashboard.audit_resource')" min-width="180">
            <template #default="{ row }">
              <span class="text-2">{{ row.resource_name || row.resource_id }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('dashboard.audit_user')" width="100">
            <template #default="{ row }">
              <span class="text-2">{{ row.username || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="IP" width="130">
            <template #default="{ row }">
              <span class="mono text-2" style="font-size:11px">{{ row.ip || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('common.time')" width="150">
            <template #default="{ row }">
              <span class="text-2">{{ fmt.fromNow(row.created_at) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- Row 5: Recent executions -->
      <el-card style="margin-top:12px">
        <template #header>
          <div class="flex items-center justify-between">
            <span class="card-title">{{ $t('dashboard.recent_executions') }}</span>
            <RouterLink to="/executions">
              <el-button size="small">{{ $t('dashboard.view_all') }}</el-button>
            </RouterLink>
          </div>
        </template>
        <el-table :data="recentExecs" :empty-text="$t('executions.no_executions')" size="small" @row-click="(row) => router.push(`/executions/${row.id}`)" style="cursor:pointer">
          <el-table-column :label="$t('common.type')" width="90">
            <template #default="{ row }">
              <el-tag type="info" size="small">{{ $t(fmt.typeLabel(row.type)) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="$t('executions.col_related')" width="120">
            <template #default="{ row }">
              <span class="mono text-2" style="font-size:11px">{{ String(row.api_id || row.scenario_id || '—').slice(0,8) }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('common.result')" width="80">
            <template #default="{ row }">
              <ResultTag :passed="row.passed" />
            </template>
          </el-table-column>
          <el-table-column :label="$t('common.duration')" width="100">
            <template #default="{ row }">
              <span class="mono text-2">{{ fmt.duration(row.duration_ms) }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('common.trigger')" width="80">
            <template #default="{ row }">
              <span class="text-2">{{ $t(fmt.triggerLabel(row.trigger)) }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('executions.col_executor')" width="110">
            <template #default="{ row }">
              <span class="text-2">{{ row.executor || '—' }}</span>
            </template>
          </el-table-column>
          <!-- 需求7: 面板执行表增加 IP 列，在执行人与时间列之间 -->
          <el-table-column :label="$t('executions.col_ip')" width="130">
            <template #default="{ row }">
              <span class="mono text-2" style="font-size:11px">{{ row.execution_ip || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('common.time')" min-width="130">
            <template #default="{ row }">
              <span class="text-2">{{ fmt.fromNow(row.started_at) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- Row 6: Recent alerts -->
      <el-card style="margin-top:12px" v-if="recentAlerts.length">
        <template #header>
          <div class="flex items-center justify-between">
            <span class="card-title">{{ $t('dashboard.recent_alerts') }}</span>
            <RouterLink to="/monitor">
              <el-button size="small">{{ $t('dashboard.view_all') }}</el-button>
            </RouterLink>
          </div>
        </template>
        <el-table :data="recentAlerts" size="small">
          <el-table-column :label="$t('dashboard.level')" width="100">
            <template #default="{ row: a }">
              <el-tag :type="riskTagType(a.risk_level)" size="small">{{ a.risk_level }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="$t('dashboard.alert_title')" min-width="200">
            <template #default="{ row: a }">{{ a.title }}</template>
          </el-table-column>
          <el-table-column :label="$t('common.time')" width="150">
            <template #default="{ row: a }">
              <span class="text-2">{{ fmt.fromNow(a.sent_at) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
        </el-tab-pane>
        <el-tab-pane :label="$t('dashboard.tab_ai_outputs')" name="ai">
          <div class="ai-panel-head">
            <div>
              <div class="card-title">{{ $t('dashboard.ai_outputs_title') }}</div>
              <div class="text-2" style="font-size:12px;margin-top:4px">{{ $t('dashboard.ai_outputs_desc') }}</div>
            </div>
            <el-select v-model="aiQualityPeriod" size="small" style="width:90px" @change="fetchAiQuality">
              <el-option value="7d" label="7d" />
              <el-option value="30d" label="30d" />
              <el-option value="90d" label="90d" />
            </el-select>
          </div>

          <!-- AI产出 KPI 卡片行：v-for 驱动，与概览 tab 风格一致，含 tooltip -->
          <div class="kpi-grid">
            <div class="kpi-card" v-for="k in aiKpis" :key="k.label">
              <el-tooltip :content="k.desc" placement="top" :show-after="400">
                <div class="kpi-label kpi-label--tip">{{ k.label }}</div>
              </el-tooltip>
              <div class="kpi-value" :style="{ color: k.color }">{{ k.value }}</div>
              <div class="kpi-sub" :class="k.subColor">{{ k.sub }}</div>
            </div>
          </div>

          <!-- 按类型统计 KPI 卡片行：展示各 AI 产出类型的计数与采纳率 -->
          <div v-if="aiGenerationNodes.length" class="kpi-grid ai-kpi-grid" style="margin-top:12px">
            <div v-for="node in aiGenerationNodes" :key="node.type" class="kpi-card">
              <div class="kpi-label">{{ generationNodeLabel(node) }}</div>
              <div class="kpi-value" style="font-size:24px">{{ node.count }}</div>
              <div class="kpi-sub text-2">
                {{ node.accepted }}/{{ node.pending_review }}/{{ node.rejected }}
                | {{ node.acceptance_rate_pct ?? 0 }}%
              </div>
            </div>
          </div>

          <el-card>
            <template #header>
              <span class="card-title">{{ $t('dashboard.ai_nodes_title') }}</span>
            </template>
            <div v-if="aiGenerationNodes.length" class="ai-node-grid">
              <div v-for="node in aiGenerationNodes" :key="node.type" class="ai-node-card" @click="router.push(node.action)">
                <div class="ai-node-head">
                  <span>{{ generationNodeLabel(node) }}</span>
                  <el-tag size="small" type="info">{{ node.count }}</el-tag>
                </div>
                <div class="ai-node-stats">
                  <div><span class="green">{{ node.accepted }}</span><small>{{ $t('dashboard.ai_status_accepted') }}</small></div>
                  <div><span class="blue">{{ node.partially_accepted }}</span><small>{{ $t('dashboard.ai_status_partial') }}</small></div>
                  <div><span class="red">{{ node.rejected }}</span><small>{{ $t('dashboard.ai_status_rejected') }}</small></div>
                  <div><span class="amber">{{ node.pending_review }}</span><small>{{ $t('dashboard.ai_status_pending') }}</small></div>
                </div>
                <el-progress :percentage="Math.min(100, Math.max(0, node.acceptance_rate_pct || 0))" :stroke-width="6" :show-text="false" />
              </div>
            </div>
            <div v-else class="empty-chart" style="height:180px">{{ $t('common.no_data') }}</div>
          </el-card>

          <div class="charts-row" style="margin-top:12px">
            <el-card>
              <template #header><span class="card-title">{{ $t('dashboard.ai_recent_errors') }}</span></template>
              <el-table :data="aiQuality.recent_errors || []" size="small" :empty-text="$t('common.no_data')" max-height="260">
                <el-table-column :label="$t('common.type')" width="110">
                  <template #default="{ row }">{{ generationNodeLabel(row) }}</template>
                </el-table-column>
                <el-table-column :label="$t('common.status')" width="90">
                  <template #default="{ row }"><el-tag size="small" type="danger">{{ row.status }}</el-tag></template>
                </el-table-column>
                <el-table-column :label="$t('common.error')" min-width="220">
                  <template #default="{ row }"><span class="text-2">{{ row.error }}</span></template>
                </el-table-column>
              </el-table>
            </el-card>
            <el-card>
              <template #header><span class="card-title">{{ $t('dashboard.ai_quality_summary') }}</span></template>
              <div class="ai-summary-list">
                <div><span>{{ $t('dashboard.ai_metric_success_rate') }}</span><b>{{ aiQuality.jobs?.success_rate_pct ?? 0 }}%</b></div>
                <div><span>{{ $t('dashboard.ai_metric_avg_duration') }}</span><b>{{ fmt.duration(aiQuality.jobs?.avg_duration_ms || 0) }}</b></div>
                <div><span>{{ $t('dashboard.ai_metric_avg_latency') }}</span><b>{{ fmt.duration(aiQuality.generations?.avg_latency_ms || 0) }}</b></div>
                <div><span>{{ $t('dashboard.ai_metric_tokens_each') }}</span><b>{{ fmtNumber(aiQuality.generations?.tokens_per_generation || 0) }}</b></div>
                <div><span>{{ $t('dashboard.ai_metric_total_tokens') }}</span><b>{{ fmtNumber(aiQuality.generations?.total_tokens || 0) }}</b></div>
              </div>
            </el-card>
          </div>
        </el-tab-pane>
        <el-tab-pane :label="$t('dashboard.tab_workbench')" name="workbench">
          <div class="kpi-grid workbench-kpi-grid">
            <div
              v-for="item in workbenchCards"
              :key="item.type"
              class="kpi-card workbench-kpi-card"
              :class="{ 'is-disabled': !item.count }"
              @click="openWorkbenchItem(item)"
            >
              <el-tooltip :content="item.desc" placement="top" :show-after="400">
                <div class="kpi-label kpi-label--tip">{{ item.title }}</div>
              </el-tooltip>
              <div class="kpi-value" :style="{ color: item.color }">{{ item.count }}</div>
              <div class="kpi-sub" :class="item.subColor">{{ item.button }}</div>
            </div>
          </div>

          <div class="charts-row" style="margin-top:12px">
            <el-card>
              <template #header>
                <span class="card-title">{{ $t('dashboard.workbench_recent_failed') }}</span>
              </template>
              <el-table :data="taskItems('recent_failed')" size="small" :empty-text="$t('common.no_data')" max-height="280" @row-click="(row) => row.id && router.push(`/executions/${row.id}`)" style="cursor:pointer">
                <el-table-column :label="$t('dashboard.workbench_object')" min-width="160">
                  <template #default="{ row }">
                    <span class="mono text-2" style="font-size:11px">{{ row.api_id || row.scenario_id || row.id }}</span>
                  </template>
                </el-table-column>
                <el-table-column :label="$t('common.duration')" width="90">
                  <template #default="{ row }">{{ fmt.duration(row.duration_ms) }}</template>
                </el-table-column>
                <el-table-column :label="$t('common.time')" width="140">
                  <template #default="{ row }">{{ fmt.fromNow(row.started_at) }}</template>
                </el-table-column>
              </el-table>
            </el-card>

            <el-card>
              <template #header>
                <span class="card-title">{{ $t('dashboard.workbench_long_queues') }}</span>
              </template>
              <el-table :data="queueRows" size="small" :empty-text="$t('common.no_data')" max-height="280">
                <el-table-column :label="$t('dashboard.workbench_queue')" min-width="160">
                  <template #default="{ row }">{{ row.name }}</template>
                </el-table-column>
                <el-table-column :label="$t('dashboard.workbench_pending')" width="70" align="center">
                  <template #default="{ row }">
                    <el-tag :type="row.pending > 0 ? 'warning' : 'info'" size="small">{{ row.pending }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="DLQ" width="70" align="center">
                  <template #default="{ row }">
                    <el-tag :type="row.dlq > 0 ? 'danger' : 'info'" size="small">{{ row.dlq }}</el-tag>
                  </template>
                </el-table-column>
              </el-table>
            </el-card>
          </div>
        </el-tab-pane>
        <!-- 需求6: 质量报告标签页 -->
        <!-- v-if 确保仅在 tab 激活时挂载，避免 hidden 容器内 ECharts 零尺寸初始化警告 -->
        <el-tab-pane :label="$t('dashboard.tab_quality')" name="quality">
          <QualityReport v-if="dashboardTab === 'quality'" :embedded="true" />
        </el-tab-pane>
        <!-- 覆盖度标签页：嵌入 Dashboard 中展示各模块维度的测试资产覆盖矩阵 -->
        <el-tab-pane :label="$t('dashboard.tab_coverage')" name="coverage">
          <CoverageIndex :embedded="true" />
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { use } from 'echarts/core'
import { LineChart, PieChart, BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import VChart from 'vue-echarts'
import { statsApi, executionApi, alertApi, auditApi, systemApi } from '@/api'
import { useProjectStore, useToastStore } from '@/stores'
import { fmt, riskTagType } from '@/utils'
import ResultTag from '@/components/ResultTag.vue'
// 需求6: 质量报告作为 Dashboard 标签页子组件
import QualityReport from '@/views/QualityReport.vue'
// 覆盖度作为 Dashboard 标签页子组件，展示各模块测试资产覆盖矩阵
import CoverageIndex from '@/views/coverage/Index.vue'

const router = useRouter()
const toast = useToastStore()
const { t } = useI18n()

use([LineChart, PieChart, BarChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])

// 需求5/6: Dashboard 标签页状态，默认"概览"
const dashboardTab = ref('overview')

const projectStore = useProjectStore()
const loading = ref(false)
const loadingInit = ref(true)
const overview = ref({})
const recentExecs = ref([])
const recentAlerts = ref([])
const recentAuditLogs = ref([])
const workbench = ref({ counts: {}, tasks: [] })
const queueStatus = ref({ queues: {}, dlq: {} })

// 新增 Dashboard 数据
const topFailing = ref([])
const topFailingHours = ref(24)
const healthScores = ref([])
const trendData = ref([])
const trendPeriod = ref('24h')
const trendGran = ref('hour')
const slaData = ref(null)
const slaPeriod = ref('30d')
const aiQuality = ref({ jobs: {}, generations: {}, quality: {}, nodes: [], recent_errors: [] })
const aiQualityPeriod = ref('30d')

const workbenchMeta = {
  unanalysed_apis: {
    titleKey: 'dashboard.workbench_unanalysed_apis',
    descKey: 'dashboard.workbench_unanalysed_apis_desc',
    buttonKey: 'dashboard.workbench_go_analyze',
    tag: 'warning',
    action: '/apis?analysis_status=idle',
  },
  pending_generations: {
    titleKey: 'dashboard.workbench_pending_generations',
    descKey: 'dashboard.workbench_pending_generations_desc',
    buttonKey: 'dashboard.workbench_go_review',
    tag: 'danger',
    action: '/generations?status=pending_review',
  },
  low_quality: {
    titleKey: 'dashboard.workbench_low_quality',
    descKey: 'dashboard.workbench_low_quality_desc',
    buttonKey: 'dashboard.workbench_view_apis',
    tag: 'warning',
    action: '/apis',
  },
  no_scenario: {
    titleKey: 'dashboard.workbench_no_scenario',
    descKey: 'dashboard.workbench_no_scenario_desc',
    buttonKey: 'dashboard.workbench_generate_scenario',
    tag: 'warning',
    action: '/scenarios',
  },
  no_monitor: {
    titleKey: 'dashboard.workbench_no_monitor',
    descKey: 'dashboard.workbench_no_monitor_desc',
    buttonKey: 'dashboard.workbench_create_monitor',
    tag: 'warning',
    action: '/monitor',
  },
  recent_failed: {
    titleKey: 'dashboard.workbench_recent_failed_card',
    descKey: 'dashboard.workbench_recent_failed_desc',
    buttonKey: 'dashboard.workbench_view_executions',
    tag: 'danger',
    action: '/executions',
  },
  recent_alerts: {
    titleKey: 'dashboard.workbench_recent_alerts',
    descKey: 'dashboard.workbench_recent_alerts_desc',
    buttonKey: 'dashboard.workbench_view_alerts',
    tag: 'danger',
    action: '/monitor?tab=alerts',
  },
  failed_ai_tasks: {
    titleKey: 'dashboard.workbench_failed_ai_tasks',
    descKey: 'dashboard.workbench_failed_ai_tasks_desc',
    buttonKey: 'dashboard.workbench_retry',
    tag: 'danger',
    action: '/apis?analysis_status=failed',
  },
  failed_diagnoses: {
    titleKey: 'dashboard.workbench_failed_diagnoses',
    descKey: 'dashboard.workbench_failed_diagnoses_desc',
    buttonKey: 'dashboard.workbench_view_executions',
    tag: 'danger',
    action: '/executions?passed=false',
  },
}

const queueNameMap = {
  'queue:ai_analyze': 'dashboard.queue_ai_analyze',
  'queue:ai_analyze_doc': 'dashboard.queue_ai_doc',
  'queue:ai_analyze_asserts': 'dashboard.queue_ai_asserts',
  'queue:ai_scenario': 'dashboard.queue_ai_scenario',
  'queue:data_template': 'dashboard.queue_data_template',
  'queue:ai_monitor': 'dashboard.queue_ai_monitor',
  'queue:diff_evaluate': 'dashboard.queue_diff_evaluate',
  'queue:diagnose_failure': 'dashboard.queue_diagnose_failure',
  'queue:alert_analyze': 'dashboard.queue_alert_analyze',
}

const trendTitle = computed(() => {
  const map = { '24h': 'dashboard.trend_24h', '7d': 'dashboard.trend_7d', '30d': 'dashboard.trend_30d' }
  return t(map[trendPeriod.value] || 'dashboard.trend_default')
})

const workbenchColorMap = {
  danger: 'var(--red)',
  warning: 'var(--amber)',
  success: 'var(--green)',
  info: 'var(--text)',
}

const kpis = computed(() => {
  const o = overview.value
  const passRate = o.executions?.pass_rate_pct ?? 0
  return [
    { label: t('dashboard.kpi_apis'), desc: t('dashboard.desc_kpi_apis'), value: o.apis?.total ?? 0,
      sub: t('dashboard.kpi_apis_sub', { done: o.apis?.statuses?.done ?? 0, running: o.apis?.statuses?.running ?? 0, queued: o.apis?.statuses?.queued ?? 0, failed: o.apis?.statuses?.failed ?? 0 }),
      subColor: 'text-2', color: 'var(--text)' },
    { label: t('dashboard.kpi_scenarios'), desc: t('dashboard.desc_kpi_scenarios'), value: o.scenarios?.total ?? 0,
      sub: t('dashboard.kpi_scenarios_sub', { ai_generated: o.scenarios?.ai_generated ?? 0, ready: o.scenarios?.statuses?.ready ?? 0, draft: o.scenarios?.statuses?.draft ?? 0 }),
      subColor: 'text-2', color: 'var(--text)' },
    { label: t('dashboard.kpi_pass_rate'), desc: t('dashboard.desc_kpi_pass_rate'), value: `${passRate}%`,
      sub: t('dashboard.kpi_pass_sub', { single: o.executions?.by_type?.single ?? 0, scenario: o.executions?.by_type?.scenario ?? 0, monitor: o.executions?.by_type?.monitor ?? 0 }),
      subColor: passRate >= 80 ? 'green' : passRate >= 50 ? 'amber' : 'red',
      color: passRate >= 80 ? 'var(--green)' : passRate >= 50 ? 'var(--amber)' : 'var(--red)' },
    // SLA 全局可用性 KPI —— 替换原来的 monitors 卡片，与 SLA 报告区域联动
    { label: t('dashboard.kpi_sla'), desc: t('dashboard.desc_sla_availability'), value: slaData.value ? `${slaData.value.global_sla_pct}%` : '—',
      sub: slaData.value ? t('dashboard.kpi_sla_sub', { met: slaData.value.sla_met_count, total: slaData.value.total_api_count }) : t('common.loading'),
      subColor: slaData.value?.global_sla_pct >= 99 ? 'green' : slaData.value?.global_sla_pct >= 95 ? 'amber' : 'red',
      color: slaData.value?.global_sla_pct >= 99 ? 'var(--green)' : slaData.value?.global_sla_pct >= 95 ? 'var(--amber)' : 'var(--red)' },
    { label: t('dashboard.kpi_monitors'), desc: t('dashboard.desc_kpi_monitors'), value: o.monitors?.active ?? 0,
      sub: t('dashboard.kpi_monitor_sub', { alerts: o.alerts?.total ?? 0 }), subColor: 'text-2', color: 'var(--text)' },
    // AI 生成统计：场景生成任务状态
    { label: t('dashboard.kpi_ai_scenario_jobs'), desc: t('dashboard.desc_kpi_ai_scenario_jobs'), value: (o.ai_jobs?.scenario?.completed ?? 0) + (o.ai_jobs?.scenario?.running ?? 0) + (o.ai_jobs?.scenario?.queued ?? 0) + (o.ai_jobs?.scenario?.failed ?? 0),
      sub: t('dashboard.kpi_ai_jobs_sub', { completed: o.ai_jobs?.scenario?.completed ?? 0, running: o.ai_jobs?.scenario?.running ?? 0, queued: o.ai_jobs?.scenario?.queued ?? 0, failed: o.ai_jobs?.scenario?.failed ?? 0 }),
      subColor: 'text-2', color: 'var(--text)' },
    // AI 生成统计：巡检生成任务状态
    { label: t('dashboard.kpi_ai_monitor_jobs'), desc: t('dashboard.desc_kpi_ai_monitor_jobs'), value: (o.ai_jobs?.monitor?.completed ?? 0) + (o.ai_jobs?.monitor?.running ?? 0) + (o.ai_jobs?.monitor?.queued ?? 0) + (o.ai_jobs?.monitor?.failed ?? 0),
      sub: t('dashboard.kpi_ai_jobs_sub', { completed: o.ai_jobs?.monitor?.completed ?? 0, running: o.ai_jobs?.monitor?.running ?? 0, queued: o.ai_jobs?.monitor?.queued ?? 0, failed: o.ai_jobs?.monitor?.failed ?? 0 }),
      subColor: 'text-2', color: 'var(--text)' },
  ]
})

const workbenchCards = computed(() => {
  const counts = workbench.value.counts || {}
  return Object.entries(workbenchMeta).map(([type, meta]) => {
    const count = counts[type] || 0
    return {
      type,
      ...meta,
      title: t(meta.titleKey),
      desc: t(meta.descKey),
      button: t(meta.buttonKey),
      count,
      color: count ? (workbenchColorMap[meta.tag] || 'var(--text)') : 'var(--text)',
      subColor: count ? (meta.tag === 'danger' ? 'red' : meta.tag === 'warning' ? 'amber' : 'text-2') : 'text-3',
    }
  })
})

const queueRows = computed(() => {
  const queues = queueStatus.value.queues || {}
  return Object.entries(queueNameMap).map(([key, nameKey]) => ({
    key,
    name: t(nameKey),
    pending: queues[key]?.pending ?? 0,
    dlq: queues[key]?.dlq ?? 0,
  }))
})

const queueSummary = computed(() => queueRows.value.reduce((acc, row) => {
  acc.totalPending += Number(row.pending || 0)
  acc.totalDlq += Number(row.dlq || 0)
  return acc
}, { totalPending: 0, totalDlq: 0 }))

const queueHighlights = computed(() => queueRows.value.filter(row => row.pending || row.dlq).slice(0, 4))
// 全量产出类型列表，后端仅返回有数据的类型，前端补齐缺失类型为零值以确保 UI 完整
const ALL_GEN_TYPES = ['doc', 'asserts', 'scenario', 'data_template', 'monitor', 'chat_suggestion']
const aiGenerationNodes = computed(() => {
  const nodes = aiQuality.value.nodes || aiQuality.value.by_type || []
  const existing = new Set(nodes.map(n => n.type))
  const filled = [...nodes]
  for (const t of ALL_GEN_TYPES) {
    if (!existing.has(t)) {
      filled.push({ type: t, count: 0, accepted: 0, partially_accepted: 0, rejected: 0, pending_review: 0, acceptance_rate_pct: 0 })
    }
  }
  return filled
})
const acceptanceColor = computed(() => {
  const rate = aiQuality.value.quality?.acceptance_rate_pct || 0
  return rate >= 80 ? 'var(--green)' : rate >= 50 ? 'var(--amber)' : 'var(--red)'
})

// AI产出 KPI 卡片：仿概览 kpis 模式，v-for 驱动渲染
const aiKpis = computed(() => {
  const q = aiQuality.value
  return [
    { label: t('dashboard.ai_metric_generated'), desc: t('dashboard.desc_ai_generated'),
      value: q.generations?.total ?? 0,
      sub: t('dashboard.ai_kpi_generated_sub', { accepted: q.generations?.accepted ?? 0, pending: q.generations?.pending_review ?? 0 }),
      color: 'var(--text)', subColor: 'text-2' },
    { label: t('dashboard.ai_metric_acceptance'), desc: t('dashboard.desc_ai_acceptance'),
      value: `${q.quality?.acceptance_rate_pct ?? 0}%`,
      sub: t('dashboard.ai_metric_reviewed', { count: q.generations?.reviewed ?? 0 }),
      color: acceptanceColor.value, subColor: 'text-2' },
    { label: t('dashboard.ai_metric_pending'), desc: t('dashboard.desc_ai_pending'),
      value: q.quality?.pending_review_backlog ?? 0,
      sub: t('dashboard.ai_kpi_pending_sub'),
      color: (q.quality?.pending_review_backlog || 0) ? 'var(--amber)' : 'var(--text)',
      subColor: 'text-2' },
    { label: t('dashboard.ai_metric_dlq'), desc: t('dashboard.desc_ai_dlq'),
      value: q.quality?.dlq_backlog ?? 0,
      sub: t('dashboard.queue_dlq_short'),
      color: (q.quality?.dlq_backlog || 0) ? 'var(--red)' : 'var(--text)',
      subColor: 'text-2' },
    { label: t('dashboard.ai_metric_tokens_label'), desc: t('dashboard.desc_ai_tokens'),
      value: fmtNumber(q.generations?.total_tokens || 0),
      sub: t('dashboard.ai_kpi_tokens_sub', { input: fmtNumber(q.generations?.input_tokens || 0), output: fmtNumber(q.generations?.output_tokens || 0) }),
      color: 'var(--text)', subColor: 'text-2' },
  ]
})

function taskItems(type) {
  const task = (workbench.value.tasks || []).find(t => t.type === type)
  return task?.items || []
}

function openWorkbenchItem(item) {
  if (!item?.count) return
  router.push(item.action)
}

function fmtNumber(n) {
  const value = Number(n || 0)
  if (value >= 1000000) return `${(value / 1000000).toFixed(1)}M`
  if (value >= 1000) return `${(value / 1000).toFixed(1)}K`
  return String(Math.round(value))
}

function generationNodeLabel(row) {
  const type = row?.type || row
  const map = {
    doc: t('dashboard.ai_node_doc'),
    asserts: t('dashboard.ai_node_asserts'),
    scenario: t('dashboard.ai_node_scenario'),
    data_template: t('dashboard.ai_node_data_template'),
    monitor: t('dashboard.ai_node_monitor'),
    chat_suggestion: t('dashboard.ai_node_chat_suggestion'),
  }
  return row?.label || map[type] || type || '—'
}

// HTTP method → ElTag type 颜色映射
function methodTagType(m) {
  const map = { GET: 'info', POST: 'primary', PUT: 'warning', DELETE: 'danger', PATCH: 'success' }
  return map[m] || 'info'
}

// 审计操作 → ElTag type 颜色映射
function actionTagType(action) {
  const dangerActions = ['delete', 'batch_delete']
  const warningActions = ['update', 'toggle', 'execute', 'batch_execute']
  const successActions = ['create', 'import', 'generate', 'analyze', 'extract', 'batch_extract']
  if (dangerActions.includes(action)) return 'danger'
  if (warningActions.includes(action)) return 'warning'
  if (successActions.includes(action)) return 'success'
  return 'info'
}

// 审计操作中文标签
function actionLabel(action) {
  const map = {
    create: '创建', update: '更新', delete: '删除', batch_delete: '批量删除',
    execute: '执行', batch_execute: '批量执行', import: '导入', export: '导出',
    toggle: '开关', analyze: '分析', generate: '生成', extract: '提取', batch_extract: '批量提取',
  }
  return map[action] || action
}

const trendOption = computed(() => {
  const data = trendData.value
  // 时间桶标签截取
  const labels = data.map(d => trendGran.value === 'hour' ? d.bucket.slice(11) : d.bucket.slice(5))
  const passRates = data.map(d => d.pass_rate_pct)
  const totals = data.map(d => d.total)

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1c2030', borderColor: '#2a2f45',
      textStyle: { color: '#e2e6f0', fontSize: 12 },
      formatter: params => {
        const p = params[0]
        const d = data[p.dataIndex]
        return `${d.bucket}<br/>通过率: ${d.pass_rate_pct}%<br/>总执行: ${d.total} (通过 ${d.passed} / 失败 ${d.failed})`
      },
    },
    grid: { left: 48, right: 48, top: 12, bottom: 28 },
    xAxis: {
      type: 'category',
      data: labels,
      axisLine: { lineStyle: { color: '#2a2f45' } },
      axisLabel: { color: '#555d78', fontSize: 10 },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value', min: 0, max: 100,
      axisLabel: { color: '#555d78', fontSize: 10, formatter: '{value}%' },
      axisLine: { show: false },
      splitLine: { lineStyle: { color: '#1c2030' } },
    },
    series: [
      {
        name: '通过率',
        type: 'line', smooth: true, symbol: 'circle', symbolSize: 4,
        data: passRates,
        lineStyle: { color: '#4f8ef7', width: 2 },
        itemStyle: { color: '#4f8ef7' },
        areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(79,142,247,.25)' }, { offset: 1, color: 'transparent' }] } },
      },
      {
        name: '执行次数',
        type: 'bar', barWidth: '40%',
        data: totals,
        yAxisIndex: 0,
        itemStyle: { color: 'rgba(79,142,247,.15)', borderColor: 'rgba(79,142,247,.25)', borderWidth: 1 },
      },
    ],
  }
})

const pieOption = computed(() => {
  const a = overview.value.apis || {}
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item', backgroundColor: '#1c2030', borderColor: '#2a2f45', textStyle: { color: '#e2e6f0', fontSize: 12 } },
    legend: { bottom: 0, textStyle: { color: '#8b91a8', fontSize: 11 } },
    series: [{
      type: 'pie', radius: ['42%', '70%'], center: ['50%', '44%'],
      label: { show: false },
      data: [
        { name: t('dashboard.pie_done'), value: a.statuses?.done || 0, itemStyle: { color: '#4f8ef7' } },
        { name: t('dashboard.pie_running'), value: a.statuses?.running || 0, itemStyle: { color: '#f0a040' } },
        { name: t('dashboard.pie_queued'), value: a.statuses?.queued || 0, itemStyle: { color: '#909399' } },
        { name: t('dashboard.pie_failed'), value: a.statuses?.failed || 0, itemStyle: { color: '#f06060' } },
        { name: t('dashboard.pie_idle'), value: a.statuses?.idle || 0, itemStyle: { color: '#2a2f45' } },
      ],
    }],
  }
})

// 独立获取各项 Dashboard 增强数据
async function fetchTopFailing() {
  try {
    topFailing.value = (await statsApi.topFailing(projectStore.current, 10, topFailingHours.value)).items || []
  } catch { topFailing.value = [] }
}

async function fetchHealthScores() {
  try {
    healthScores.value = (await statsApi.healthScores(projectStore.current, 50)).items || []
  } catch { healthScores.value = [] }
}

async function fetchTrends() {
  try {
    trendData.value = (await statsApi.trends(projectStore.current, trendPeriod.value, trendGran.value)).trend || []
  } catch { trendData.value = [] }
}

async function fetchSla() {
  try {
    slaData.value = await statsApi.sla(projectStore.current, slaPeriod.value)
  } catch { slaData.value = null }
}

async function fetchAiQuality() {
  try {
    aiQuality.value = await statsApi.aiQuality(projectStore.current, aiQualityPeriod.value)
  } catch {
    aiQuality.value = { jobs: {}, generations: {}, quality: {}, nodes: [], recent_errors: [] }
  }
}

async function fetchAuditLogs() {
  try {
    const result = await auditApi.list({ limit: 15 })
    recentAuditLogs.value = result.logs || []
  } catch { recentAuditLogs.value = [] }
}

async function fetchWorkbench() {
  try {
    workbench.value = await statsApi.workbench(projectStore.current)
  } catch { workbench.value = { counts: {}, tasks: [] } }
}

async function fetchQueues() {
  try {
    queueStatus.value = await systemApi.queues()
  } catch { queueStatus.value = { queues: {}, dlq: {} } }
}

async function refresh() {
  // 防止多次并发调用（切换项目时 watch + onMounted 可能同时触发）
  if (loading.value) return
  loading.value = true
  try {
    const [ov, execs, alerts] = await Promise.all([
      statsApi.overview(projectStore.current),
      executionApi.list({ project_id: projectStore.current, limit: 30 }),
      alertApi.list({ project_id: projectStore.current, limit: 5 }),
    ])
    overview.value = ov
    recentExecs.value = execs.items || []
    recentAlerts.value = (alerts.items || []).filter(a => !a.is_recovery)

    // 并行获取增强 Dashboard 数据（不阻塞基础数据展示）
    await Promise.all([
      fetchTopFailing(),
      fetchHealthScores(),
      fetchTrends(),
      fetchSla(),
      fetchAiQuality(),
      fetchAuditLogs(),
      fetchWorkbench(),
      fetchQueues(),
    ])
  } catch (e) {
    toast.error(e.message || t('toast.refresh_failed'))
  } finally {
    loading.value = false
  }
}

// 切换项目时自动刷新 Dashboard 数据
watch(() => projectStore.current, () => { refresh() })

onMounted(async () => {
  await refresh()
  loadingInit.value = false
})
</script>

<style scoped>
.kpi-grid {
  display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin-bottom: 16px;
}
.kpi-card {
  background: var(--bg-2); border: 1px solid var(--border); border-radius: var(--radius-lg);
  padding: 18px 20px;
}
.kpi-label { font-size: 11px; font-weight: 600; color: var(--text-3); text-transform: uppercase; letter-spacing: .06em; }
.kpi-label--tip { cursor: help; border-bottom: 1px dashed var(--border-2); }
.kpi-value { font-size: 30px; font-weight: 700; font-family: var(--font-mono); margin: 8px 0 4px; line-height: 1; }
.kpi-sub   { font-size: 12px; }

.queue-strip { margin-bottom: 12px; }
.queue-strip-inner {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
}
.queue-strip-sub { margin-left: 10px; font-size: 12px; }
.queue-strip-actions {
  display: flex; align-items: center; justify-content: flex-end; gap: 8px; flex-wrap: wrap;
}

.ai-panel-head {
  display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 12px;
}
.ai-kpi-grid { grid-template-columns: repeat(6, minmax(0, 1fr)); }
.ai-node-grid {
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px;
}
.ai-node-card {
  background: var(--bg-1); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 12px; cursor: pointer; transition: border-color .15s ease, transform .15s ease;
}
.ai-node-card:hover { border-color: var(--accent); transform: translateY(-1px); }
.ai-node-head {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  font-size: 13px; font-weight: 600; margin-bottom: 10px;
}
.ai-node-stats {
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px; margin-bottom: 10px;
}
.ai-node-stats div {
  min-width: 0; background: var(--bg-2); border-radius: var(--radius); padding: 6px;
  display: flex; flex-direction: column; gap: 2px; align-items: center;
}
.ai-node-stats span { font-family: var(--font-mono); font-weight: 700; font-size: 16px; line-height: 1; }
.ai-node-stats small { font-size: 10px; color: var(--text-3); white-space: nowrap; }
.ai-summary-list { display: flex; flex-direction: column; gap: 10px; }
.ai-summary-list div {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 8px 0; border-bottom: 1px solid var(--border);
}
.ai-summary-list div:last-child { border-bottom: none; }
.ai-summary-list span { color: var(--text-2); font-size: 12px; }
.ai-summary-list b { font-family: var(--font-mono); font-size: 16px; }

.workbench-kpi-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.workbench-kpi-card {
  cursor: pointer;
  transition: border-color .15s ease, transform .15s ease, opacity .15s ease;
}
.workbench-kpi-card:hover {
  border-color: var(--accent);
  transform: translateY(-1px);
}
.workbench-kpi-card.is-disabled {
  cursor: default;
  opacity: .72;
}
.workbench-kpi-card.is-disabled:hover {
  border-color: var(--border);
  transform: none;
}

.charts-row { display: grid; grid-template-columns: 1fr 320px; gap: 12px; }

.empty-chart {
  height: 260px; display: flex; align-items: center; justify-content: center;
  color: var(--text-3); font-size: 13px;
}

/* 健康评分 */
.health-grid {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px;
}
.health-chip {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 8px 12px; background: var(--bg-1); border-radius: var(--radius);
  border: 1px solid var(--border);
  cursor: pointer; transition: border-color .2s;
}
.health-chip:hover { border-color: var(--accent); }
.health-score {
  font-size: 18px; font-weight: 700; font-family: var(--font-mono);
  min-width: 36px; text-align: right;
}
/* 评分等级颜色 */
.grade-excellent { color: var(--green); }
.grade-good      { color: #4f8ef7; }
.grade-fair      { color: var(--amber); }
.grade-poor      { color: var(--red); }

@media (max-width: 1100px) {
  .kpi-grid { grid-template-columns: repeat(2, 1fr); }
  .charts-row { grid-template-columns: 1fr; }
  .health-grid { grid-template-columns: repeat(2, 1fr); }
  .workbench-kpi-grid { grid-template-columns: repeat(2, 1fr); }
  .ai-kpi-grid { grid-template-columns: repeat(2, 1fr); }
  .ai-node-grid { grid-template-columns: 1fr; }
  .queue-strip-inner { align-items: flex-start; flex-direction: column; }
}

@media (max-width: 640px) {
  .workbench-kpi-grid { grid-template-columns: 1fr; }
  .ai-kpi-grid { grid-template-columns: 1fr; }
  .ai-panel-head { flex-direction: column; }
}
</style>
