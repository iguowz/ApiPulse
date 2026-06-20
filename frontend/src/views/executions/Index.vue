<template>
  <div class="page">
    <div class="page-header">
      <div>
        <div class="page-title">{{ $t('executions.title') }}</div>
        <div class="page-subtitle">{{ $t('executions.total', { total }) }}</div>
      </div>
      <div>
        <el-button @click="exportCsv" :disabled="exporting" :loading="exporting">{{ $t('executions.export_csv') }}</el-button>
      </div>
    </div>

    <!-- 筛选栏：支持关键字搜索、执行人/类型/触发/结果筛选 -->
    <div class="filter-bar">
      <el-input
        v-model="filter.keyword"
        :placeholder="$t('executions.placeholder_keyword')"
        size="small"
        style="width:180px"
        clearable
        @change="load"
        @clear="load"
      />
      <el-input
        v-model="filter.executor"
        :placeholder="$t('executions.filter_executor')"
        size="small"
        style="width:130px"
        clearable
        @change="load"
        @clear="load"
      />
      <el-select v-model="filter.type" :placeholder="$t('executions.filter_type')" size="small" style="width:130px" @change="load">
        <el-option :label="$t('executions.filter_type')" value="" />
        <el-option :label="$t('executions.type_single')" value="single" />
        <el-option :label="$t('executions.type_scenario')" value="scenario" />
        <el-option :label="$t('executions.type_monitor')" value="monitor" />
      </el-select>
      <el-select v-model="filter.trigger" :placeholder="$t('executions.filter_trigger')" size="small" style="width:120px" @change="load">
        <el-option :label="$t('executions.filter_trigger')" value="" />
        <el-option :label="$t('executions.trigger_manual')" value="manual" />
        <el-option :label="$t('executions.trigger_monitor')" value="monitor" />
        <el-option :label="$t('executions.trigger_scheduler')" value="scheduler" />
      </el-select>
      <el-select v-model="filter.passed" :placeholder="$t('executions.filter_result')" size="small" style="width:110px" @change="load">
        <el-option :label="$t('executions.filter_result')" value="" />
        <el-option :label="$t('common.pass')" value="true" />
        <el-option :label="$t('common.fail')" value="false" />
      </el-select>
      <el-button v-if="anyFilter" size="small" @click="clearFilters">{{ $t('apis.clear') }}</el-button>
    </div>

    <div class="page-body" style="padding-top:0">

      <el-card style="padding:0">
      <el-table
        :data="items"
        v-loading="loading"
        row-key="id"
        @row-click="(row) => router.push(`/executions/${row.id}`)"
        style="cursor:pointer"
        :empty-text="$t('executions.no_executions')"
      >
        <el-table-column :label="$t('executions.col_result')" width="80">
          <template #default="{ row }">
            <ResultTag :passed="row.passed" />
          </template>
        </el-table-column>
        <el-table-column :label="$t('executions.col_type')" width="90">
          <template #default="{ row }">
            <el-tag type="info" size="small">{{ $t(fmt.typeLabel(row.type)) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="$t('executions.col_trigger')" width="80">
          <template #default="{ row }">
            <span class="text-2">{{ $t('executions.trigger_' + (row.trigger || '')) || row.trigger }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="$t('executions.col_related')" min-width="200">
          <template #default="{ row }">
            <!-- 显示实体名称而非截断 ID：根据 type 查找 API 或场景名称 -->
            <span class="text-2" style="font-size:12px">
              {{ getRelatedName(row) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column :label="$t('executions.col_executor')" width="110">
          <template #default="{ row }">
            <span class="text-2">{{ row.executor || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="$t('executions.col_ip')" width="130">
          <template #default="{ row }">
            <span class="mono text-2" style="font-size:11px">{{ row.execution_ip || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="$t('executions.col_steps')" width="70">
          <template #default="{ row }">
            <span class="mono text-2">{{ row.steps?.length || 0 }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="$t('executions.col_duration')" width="100">
          <template #default="{ row }">
            <span class="mono text-2">{{ fmt.duration(row.duration_ms) }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="$t('executions.col_failure_reason')" min-width="180">
          <template #default="{ row }">
            <span v-if="row.failure_reason" class="truncate text-2" style="font-size:11px;color:var(--red)">{{ row.failure_reason }}</span>
            <span v-else class="text-3">—</span>
          </template>
        </el-table-column>
        <el-table-column :label="$t('executions.col_time')" width="150">
          <template #default="{ row }">
            <span class="text-2">{{ fmt.fromNow(row.started_at) }}</span>
          </template>
        </el-table-column>
      </el-table>

      <AppPagination v-model:page="page" :page-size="pageSize" :total="total" @page-change="load" />

      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { executionApi, apiApi, scenarioApi } from '@/api'
import { useProjectStore, useToastStore } from '@/stores'
import { fmt } from '@/utils'
import AppPagination from '@/components/AppPagination.vue'
import ResultTag from '@/components/ResultTag.vue'

const router = useRouter()
const { t } = useI18n()
const toast = useToastStore()
const projectStore = useProjectStore()
const items    = ref([])
const total    = ref(0)
const loading  = ref(false)
const page     = ref(1)
const pageSize = 20
const filter   = ref({ type: '', trigger: '', passed: '', executor: '', keyword: '' })

const nameMap = ref<Record<string, string>>({})  // id → name 映射，避免重复请求

const anyFilter = computed(() => filter.value.type || filter.value.trigger || filter.value.passed !== '' || filter.value.executor || filter.value.keyword)

async function load() {
  loading.value = true
  try {
    const params = {
      project_id: projectStore.current,
      skip: (page.value - 1) * pageSize,
      limit: pageSize,
    }
    if (filter.value.type)    params.type    = filter.value.type
    if (filter.value.trigger) params.trigger = filter.value.trigger
    if (filter.value.executor) params.executor = filter.value.executor
    if (filter.value.keyword) params.keyword = filter.value.keyword
    if (filter.value.passed !== '') params.passed = filter.value.passed === 'true'
    const res = await executionApi.list(params)
    items.value = res.items || []
    total.value = res.total || 0
    await loadNames()  // 加载页面上所有实体名称
  } catch (e) {
    toast.error(e.message || t('toast.load_failed'))
  } finally { loading.value = false }
}

// 根据执行记录类型查找对应实体名称：场景类型显示场景名，单接口/监控类型显示 API 名
function getRelatedName(row: any): string {
  // 场景类型：优先使用场景名
  if (row.type === 'scenario' && row.scenario_id) {
    return nameMap.value[row.scenario_id] || row.scenario_id.slice(0, 12) + '...'
  }
  // 单接口/监控类型：使用 API 名
  if (row.api_id) {
    return nameMap.value[row.api_id] || row.api_id.slice(0, 12) + '...'
  }
  return '—'
}

// 加载当前页实体名称映射：收集所有唯一的 API/场景 ID，并行获取名称
async function loadNames() {
  const apiIds = new Set<string>()
  const scenarioIds = new Set<string>()
  for (const item of items.value) {
    if (item.type === 'scenario' && item.scenario_id) {
      // 只获取尚未缓存的场景名称
      if (!nameMap.value[item.scenario_id]) scenarioIds.add(item.scenario_id)
    } else if (item.api_id) {
      // 只获取尚未缓存的 API 名称
      if (!nameMap.value[item.api_id]) apiIds.add(item.api_id)
    }
  }
  // 并行获取所有名称，单条失败不影响其他
  const promises: Promise<void>[] = []
  for (const id of apiIds) {
    promises.push(
      apiApi.get(id).then(res => { nameMap.value[id] = res.name || res.request?.path || id }).catch(() => { console.warn('resolve api name failed for', id) })
    )
  }
  for (const id of scenarioIds) {
    promises.push(
      scenarioApi.get(id).then(res => { nameMap.value[id] = res.name || id }).catch(() => { console.warn('resolve scenario name failed for', id) })
    )
  }
  await Promise.all(promises)
}

function clearFilters() {
  filter.value = { type: '', trigger: '', passed: '', executor: '', keyword: '' }
  page.value = 1
  load()
}

// ── CSV 导出：下载当前筛选条件下的执行记录 ──
const exporting = ref(false)
async function exportCsv() {
  exporting.value = true
  try {
    const params = { project_id: projectStore.current }
    if (filter.value.type)    params.type    = filter.value.type
    if (filter.value.trigger) params.trigger = filter.value.trigger
    if (filter.value.executor) params.executor = filter.value.executor
    if (filter.value.keyword) params.keyword = filter.value.keyword
    if (filter.value.passed !== '') params.passed = filter.value.passed === 'true'
    const blob = await executionApi.exportCsv(params)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `executions_${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
    toast.success(t('executions.export_csv_done'))
  } catch (e) {
    toast.error(e.message || t('executions.export_csv_failed'))
  } finally { exporting.value = false }
}

watch(() => projectStore.current, () => { page.value = 1; load() })
onMounted(load)
</script>

<style scoped>
.filter-bar {
  display: flex; gap: 10px; padding: 12px 24px; border-bottom: 0px solid var(--border);
  align-items: center;
}
</style>
