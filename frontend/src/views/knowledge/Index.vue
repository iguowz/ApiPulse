<template>
  <div class="page">
    <div class="page-header">
      <div>
        <div class="page-title">{{ $t('nav.knowledge') }}</div>
        <div class="page-subtitle">{{ $t('knowledge') || '知识库与记忆管理' }}</div>
      </div>
      <!-- 批量操作按钮：仅在知识库 Tab 显示 -->
      <div v-if="activeTab === 'knowledge'" class="flex gap-2">
        <el-button size="small" @click="batchExtract" :loading="extracting" :disabled="extracting">
          {{ extracting ? $t('knowledge_extracting') : $t('knowledge_batch_extract') }}
        </el-button>
        <el-button size="small" @click="consolidate" :loading="consolidating" :disabled="consolidating">
          {{ consolidating ? $t('knowledge_consolidating') : $t('knowledge_consolidate') }}
        </el-button>
        <el-button size="small" type="danger" @click="batchDelete" :disabled="!selected.length">
          {{ $t('knowledge_batch_delete') }}
        </el-button>
      </div>
    </div>

    <!-- 主 Tab：知识库 / 记忆 -->
    <el-tabs v-model="activeTab" class="knowledge-tabs">
      <el-tab-pane :label="$t('knowledge') || '知识库'" name="knowledge">
        <!-- 筛选栏 -->
        <div class="filter-bar">
          <el-input
            v-model="search"
            :placeholder="$t('knowledge_search_placeholder')"
            size="small"
            style="width:240px"
            clearable
            @change="load"
            @clear="load"
          />
          <el-select v-model="filterType" :placeholder="$t('knowledge_col_type')" size="small" style="width:150px" @change="load">
            <el-option :label="$t('common.all')" value="" />
            <el-option :label="$t('knowledge_type_field_pattern')" value="field_pattern" />
            <el-option :label="$t('knowledge_type_assertion_pattern')" value="assertion_pattern" />
            <el-option :label="$t('knowledge_type_doc_pattern')" value="doc_pattern" />
            <el-option :label="$t('knowledge_type_scenario_pattern')" value="scenario_pattern" />
          </el-select>
        </div>

        <div class="page-body" style="padding-top:0" v-loading="loading">
          <el-card style="padding:0">
          <el-table
            :data="items"
            row-key="id"
            max-height="calc(100vh - 320px)"
            :empty-text="$t('knowledge_empty')"
            @selection-change="onSelectionChange"
          >
            <el-table-column type="selection" width="40" />
            <el-table-column :label="$t('knowledge_col_type')" width="100">
              <template #default="{ row }">
                <el-tag size="small" type="info">{{ row.type ? $t('knowledge_type_' + row.type) : '—' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="$t('knowledge_col_title')" min-width="180">
              <template #default="{ row }">
                <span class="mono" style="font-size:13px;font-weight:600">{{ row.title }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="$t('knowledge_col_content')" min-width="240">
              <template #default="{ row }">
                <span class="text-2" style="font-size:12px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">
                  {{ (row.content || '').slice(0, 200) }}{{ (row.content || '').length > 200 ? '…' : '' }}
                </span>
              </template>
            </el-table-column>
            <el-table-column :label="$t('knowledge_col_tags')" min-width="160">
              <template #default="{ row }">
                <div v-if="row.tags?.length" class="tag-scroll-container">
                  <el-tag v-for="tag in row.tags" :key="tag" size="small" class="tag-chip">{{ tagLabel(tag) }}</el-tag>
                </div>
                <span v-else class="text-3">—</span>
              </template>
            </el-table-column>
            <el-table-column :label="$t('knowledge_col_usage')" width="70" align="center">
              <template #default="{ row }">
                <span class="mono text-2">{{ row.usage_count ?? 0 }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="$t('knowledge_col_confidence')" width="90" align="center">
              <template #default="{ row }">
                <span class="mono text-2">{{ row.confidence != null ? (row.confidence * 100).toFixed(0) + '%' : '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="$t('knowledge_col_time')" width="140">
              <template #default="{ row }">
                <span class="text-2">{{ fmt.fromNow(row.updated_at || row.created_at) }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="$t('common.actions')" width="140" fixed="right">
              <template #default="{ row }">
                <div class="flex items-center" style="gap:6px;flex-wrap:nowrap;white-space:nowrap">
                  <el-button size="small" link @click="openEdit(row)">{{ $t('common.edit') }}</el-button>
                  <el-popconfirm :title="$t('knowledge_confirm_delete')" @confirm="remove(row.id)">
                    <template #reference>
                      <el-button size="small" type="danger" link>{{ $t('common.delete') }}</el-button>
                    </template>
                  </el-popconfirm>
                </div>
              </template>
            </el-table-column>
          </el-table>

          <!-- 原生 el-pagination：显示总数 + 每页条数切换，v-model:current-page 双向绑定 page -->
          <div class="pagination-wrapper">
            <el-pagination
              v-model:current-page="page"
              :page-size="pageSize"
              :total="total"
              :page-sizes="[10, 20, 50]"
              layout="total, sizes, prev, pager, next"
              background
              small
              @size-change="onPageSizeChange"
              @current-change="load"
            />
          </div>
          </el-card>
        </div>
      </el-tab-pane>

      <el-tab-pane :label="$t('nav.memory') || '记忆'" name="memory">
        <MemoryPanel />
      </el-tab-pane>
    </el-tabs>

    <!-- 编辑对话框 -->
    <el-dialog
      v-model="showEdit"
      :title="$t('knowledge_edit_title')"
      width="560px"
      :close-on-click-modal="false"
    >
      <el-form label-position="top" :model="editForm">
        <el-form-item :label="$t('knowledge_form_title')" required>
          <el-input v-model="editForm.title" :placeholder="$t('knowledge_title_placeholder')" />
        </el-form-item>
        <el-form-item :label="$t('knowledge_form_content')" required>
          <el-input
            v-model="editForm.content"
            type="textarea"
            :rows="6"
            :placeholder="$t('knowledge_content_placeholder')"
          />
        </el-form-item>
        <el-form-item :label="$t('knowledge_form_tags')">
          <el-input v-model="editForm.tags_str" :placeholder="$t('knowledge_tags_placeholder')" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showEdit = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" @click="save" :loading="saving" :disabled="saving || !editForm.title">
          {{ $t('common.save') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch, defineAsyncComponent } from 'vue'
import { useI18n } from 'vue-i18n'
import { knowledgeApi } from '@/api'
import { useProjectStore, useToastStore } from '@/stores'
import { fmt } from '@/utils'

/* 记忆面板：懒加载，仅在切换到记忆 Tab 时拉取组件代码 */
const MemoryPanel = defineAsyncComponent(() => import('@/views/memory/Index.vue'))

const { t } = useI18n()
const toast = useToastStore()
const projectStore = useProjectStore()

const activeTab  = ref('knowledge')
const items    = ref<any[]>([])
const total    = ref(0)
const loading  = ref(false)
const page     = ref(1)
const pageSize = ref(20)
const search   = ref('')
const filterType = ref('')
const selected = ref<any[]>([])

// 批量操作状态
const extracting   = ref(false)
const consolidating = ref(false)

// 编辑对话框
const showEdit = ref(false)
const saving   = ref(false)
const editId   = ref<string | null>(null)
const editForm = ref({ title: '', content: '', tags_str: '' })

function onSelectionChange(rows: any[]) {
  selected.value = rows
}

async function load() {
  loading.value = true
  try {
    const params: any = {
      project_id: projectStore.current,
      skip: (page.value - 1) * pageSize.value,
      limit: pageSize.value,
    }
    if (search.value) params.search = search.value
    if (filterType.value) params.type = filterType.value
    const res = await knowledgeApi.list(params)
    items.value = res.items || []
    total.value = res.total || 0
  } catch (e: any) {
    toast.error(e.message || t('api_detail.knowledge_load_failed'))
  } finally { loading.value = false }
}

/* pageSize 切换时：重置到第 1 页并重新加载 */
function onPageSizeChange(val: number) {
  pageSize.value = val
  page.value = 1
  load()
}

function openEdit(row?: any) {
  if (row) {
    editId.value = row.id
    editForm.value = {
      title: row.title || '',
      content: row.content || '',
      tags_str: (row.tags || []).join(', '),
    }
  } else {
    editId.value = null
    editForm.value = { title: '', content: '', tags_str: '' }
  }
  showEdit.value = true
}

async function save() {
  if (!editForm.value.title || saving.value) return
  saving.value = true
  try {
    const tags = editForm.value.tags_str
      ? editForm.value.tags_str.split(',').map((t: string) => t.trim()).filter(Boolean)
      : []
    const data = {
      title: editForm.value.title,
      content: editForm.value.content,
      tags,
    }
    await knowledgeApi.update(editId.value!, data)
    toast.success(t('knowledge_toast_updated'))
    showEdit.value = false
    await load()
  } catch (e: any) {
    toast.error(e.message)
  } finally { saving.value = false }
}

async function remove(id: string) {
  try {
    await knowledgeApi.remove(id)
    toast.success(t('knowledge_toast_deleted'))
    await load()
  } catch (e: any) {
    toast.error(e.message)
  }
}

async function batchExtract() {
  extracting.value = true
  try {
    // batch-extract 为异步后台任务，后端立即返回 {queued:true}，通过 WebSocket 推送进度
    await knowledgeApi.batchExtract(projectStore.current)
    toast.success(t('knowledge_toast_batch_queued'))
    await load()
  } catch (e: any) {
    toast.error(e.message)
  } finally { extracting.value = false }
}

async function consolidate() {
  consolidating.value = true
  try {
    const res = await knowledgeApi.consolidate(projectStore.current)
    toast.success(t('knowledge_toast_consolidated', { before: res.before ?? '?', after: res.after ?? '?' }))
    await load()
  } catch (e: any) {
    toast.error(e.message)
  } finally { consolidating.value = false }
}

async function batchDelete() {
  const ids = selected.value.map(r => r.id)
  if (!ids.length) return
  try {
    const res = await knowledgeApi.batchDelete(ids, projectStore.current)
    // 使用后端实际删除数量，避免 deleted=0 时仍提示成功
    toast.success(t('knowledge_toast_batch_deleted', { n: res.deleted ?? 0 }))
    selected.value = []
    await load()
  } catch (e: any) {
    toast.error(e.message)
  }
}

// 标签翻译：后端 tag 可能带 "memory." 前缀（如 memory.tag_review），去掉前缀后尝试 i18n 翻译
// 同时处理 "memory.tag_api:UUID" 后缀，去掉 UUID 部分后查翻译
function tagLabel(tag: string): string {
  // 去掉 "memory." 或 "memory.tag_" 前缀，得到纯 key（如 "tag_api:UUID" 或 "review"）
  let clean = tag.startsWith('memory.tag_') ? tag.slice(11) : tag.startsWith('memory.') ? tag.slice(7) : tag
  // 去掉 ":UUID" 后缀（如 "tag_api:384fd1b8-..." → "tag_api"）
  const colonIdx = clean.indexOf(':')
  if (colonIdx !== -1) clean = clean.slice(0, colonIdx)
  const key = 'memory.tag_' + clean
  const translated = t(key)
  return translated !== key ? translated : tag
}

watch(() => projectStore.current, () => { page.value = 1; load() })
onMounted(load)
</script>

<style scoped>
/* 顶层 Tab 样式 */
.knowledge-tabs {
  margin: 0 24px;
}

/* 分页容器：居中显示，含总数和每页条数 */
.pagination-wrapper {
  padding: 12px 0;
  display: flex;
  justify-content: center;
}

.filter-bar {
  display: flex; gap: 10px; padding: 12px 24px; border-bottom: 0px solid var(--border);
  align-items: center;
}

.tag-chip {
  margin: 0;
  flex-shrink: 0;
}

/* 标签横向滚动容器：支持左右滑动查看多余标签 */
.tag-scroll-container {
  display: flex;
  gap: 4px;
  overflow-x: auto;
  overflow-y: hidden;
  white-space: nowrap;
  max-width: 100%;
  /* 隐藏滚动条，保持视觉美观 */
  scrollbar-width: none;
}
.tag-scroll-container::-webkit-scrollbar {
  display: none;
}
</style>
