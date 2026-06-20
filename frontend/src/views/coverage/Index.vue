<template>
  <div class="page">
    <!-- 支持嵌入 Dashboard 标签页时隐藏独立页 header -->
    <div v-if="!embedded" class="page-header">
      <div>
        <div class="page-title">{{ $t('coverage.title') }}</div>
        <div class="page-subtitle">{{ $t('coverage.subtitle') }}</div>
      </div>
    </div>

    <div class="page-body" v-loading="loading">
      <!-- 空状态 -->
      <div v-if="!loading && matrix.length === 0" class="empty-state">
        <p>{{ $t('coverage.empty') }}</p>
      </div>

      <template v-else>
        <!-- 汇总卡 -->
        <div class="summary-row">
          <div class="summary-item">
            <span class="summary-val">{{ modules.length }}</span>
            <el-tooltip :content="$t('coverage.desc_overall')" placement="top" :show-after="400">
              <span class="summary-label summary-label--tip">{{ $t('coverage.overall') }}</span>
            </el-tooltip>
          </div>
          <div class="summary-item">
            <span class="summary-val">{{ totalApis }}</span>
            <el-tooltip :content="$t('coverage.desc_api_count')" placement="top" :show-after="400">
              <span class="summary-label summary-label--tip">{{ $t('coverage.api_count') }}</span>
            </el-tooltip>
          </div>
        </div>

        <!-- 热力图矩阵: 行=模块, 列=维度 -->
        <div class="heatmap-container">
          <table class="heatmap-table">
            <thead>
              <tr>
                <th class="col-module">{{ $t('coverage.overall') }}</th>
                <th v-for="dim in dimensions" :key="dim" class="col-dim">
                  <el-tooltip :content="$t('coverage.desc_dim_' + dim)" placement="top" :show-after="400">
                    <span class="dim-label dim-label--tip">{{ $t('coverage.dim_' + dim) }}</span>
                  </el-tooltip>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in matrix" :key="row.module">
                <td class="col-module">
                  <span class="module-name">{{ row.module === '_all' ? $t('coverage.overall') : row.module }}</span>
                  <span class="module-total">{{ row.total }}</span>
                </td>
                <td
                  v-for="dim in dimensions"
                  :key="dim"
                  class="col-cell"
                  :style="{ background: heatColor(row[dim]) }"
                  :title="`${row.module} · ${$t('coverage.dim_' + dim)}: ${row[dim]}%`"
                >
                  <span class="cell-val" :class="{ 'cell-low': row[dim] < 40 }">{{ row[dim] }}%</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- 图例 -->
        <div class="legend">
          <el-tooltip :content="$t('coverage.desc_coverage_low')" placement="top" :show-after="400">
            <span class="legend-item"><span class="legend-swatch" style="background:#f06060"></span> 0-30% {{ $t('coverage.coverage_low') }}</span>
          </el-tooltip>
          <el-tooltip :content="$t('coverage.desc_coverage_medium')" placement="top" :show-after="400">
            <span class="legend-item"><span class="legend-swatch" style="background:#f0b44c"></span> 30-60% {{ $t('coverage.coverage_medium') }}</span>
          </el-tooltip>
          <el-tooltip :content="$t('coverage.desc_coverage_high')" placement="top" :show-after="400">
            <span class="legend-item"><span class="legend-swatch" style="background:#3ecf8e"></span> 60-100% {{ $t('coverage.coverage_high') }}</span>
          </el-tooltip>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
// 支持嵌入 Dashboard 标签页，隐藏独立页面头
defineProps({ embedded: { type: Boolean, default: false } })
import { ref, computed, watch, onMounted } from 'vue'
import { statsApi } from '@/api'
import { useProjectStore, useToastStore } from '@/stores'

const projectStore = useProjectStore()
const toast = useToastStore()

const loading = ref(false)
const matrix = ref([])
const dimensions = ['doc', 'asserts', 'scenario', 'monitor', 'execute']

// 从 matrix 中提取模块列表（排除 _all 汇总行）
const modules = computed(() => matrix.value.filter(r => r.module !== '_all'))
// 全局汇总行中的 total 即为总 API 数
const totalApis = computed(() => {
  const all = matrix.value.find(r => r.module === '_all')
  return all ? all.total : 0
})

// 热力图颜色映射: 红(0-30) → 黄(30-60) → 绿(60-100)
function heatColor(pct) {
  if (pct == null) return 'transparent'
  if (pct < 30) {
    // 红→黄 过渡
    const t = pct / 30
    const r = 240, g = Math.round(96 + t * (176 - 96)), b = Math.round(96 + t * (76 - 96))
    return `rgb(${r},${g},${b})`
  }
  if (pct < 60) {
    // 黄→绿 过渡
    const t = (pct - 30) / 30
    const r = Math.round(240 + t * (62 - 240)), g = Math.round(176 + t * (207 - 176)), b = Math.round(76 + t * (142 - 76))
    return `rgb(${r},${g},${b})`
  }
  // 绿: 60-100
  return `rgb(62,207,142)`
}

async function load() {
  loading.value = true
  try {
    const res = await statsApi.coverage(projectStore.current || 'default')
    matrix.value = res.matrix || []
  } catch (e) {
    console.error('Failed to load coverage:', e)
    toast.error(e.message || '加载覆盖率数据失败')
    matrix.value = []
  } finally {
    loading.value = false
  }
}

// 项目切换时自动重新加载覆盖度数据（嵌入 Dashboard 时由其父组件控制项目切换）
watch(() => projectStore.current, () => { load() })

onMounted(() => { load() })
</script>

<style scoped>
.page { max-width: 1200px; margin: 0 auto; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
.page-title { font-size: 20px; font-weight: 700; color: var(--text-1); }
.page-subtitle { font-size: 13px; color: var(--text-3); margin-top: 4px; }

/* 汇总卡 */
.summary-row { display: flex; gap: 12px; margin-bottom: 20px; }
.summary-item {
  display: flex; flex-direction: column; align-items: center; gap: 4px;
  background: var(--bg-2); border-radius: var(--radius); padding: 16px 32px;
  border: 1px solid var(--border);
}
.summary-val { font-size: 28px; font-weight: 700; color: var(--accent); }
.summary-label { font-size: 11px; color: var(--text-3); text-transform: uppercase; letter-spacing: .06em; }
.summary-label--tip { cursor: help; border-bottom: 1px dashed var(--border-2); }

/* 热力图表格 */
.heatmap-container { overflow-x: auto; margin-bottom: 16px; }
.heatmap-table {
  width: 100%; border-collapse: separate; border-spacing: 2px;
}
.heatmap-table th {
  font-size: 12px; color: var(--text-3); text-transform: uppercase; letter-spacing: .05em;
  padding: 6px 10px; text-align: center; white-space: nowrap;
}
.heatmap-table th.col-module { text-align: left; padding-left: 12px; }
.heatmap-table th.col-dim { min-width: 80px; }
.dim-label { display: inline-block; }
.dim-label--tip { cursor: help; border-bottom: 1px dashed var(--border-2); }

.heatmap-table td { padding: 0; }
.heatmap-table td.col-module {
  padding: 8px 12px; white-space: nowrap;
  display: flex; align-items: center; gap: 8px;
}
.module-name { font-size: 13px; color: var(--text-1); font-weight: 500; }
.module-total { font-size: 10px; color: var(--text-3); background: var(--bg-3); border-radius: 8px; padding: 1px 6px; }

/* 热力图单元格 */
.col-cell {
  text-align: center; vertical-align: middle;
  border-radius: 4px; min-width: 80px; height: 42px;
  transition: transform .1s;
}
.col-cell:hover { transform: scale(1.05); z-index: 1; }
.cell-val {
  font-size: 13px; font-weight: 600; color: #fff;
  text-shadow: 0 1px 2px rgba(0,0,0,.25);
}
.cell-low { color: #fff; text-shadow: 0 1px 2px rgba(0,0,0,.4); }

/* 图例 */
.legend { display: flex; gap: 20px; justify-content: center; font-size: 12px; color: var(--text-3); }
.legend-item { display: flex; align-items: center; gap: 6px; }
.legend-swatch { width: 14px; height: 14px; border-radius: 3px; display: inline-block; }

.empty-state { text-align: center; padding: 60px 20px; color: var(--text-3); }
</style>
