<template>
  <div class="page">
    <div class="page-header">
      <div>
        <div class="page-title">{{ $t('nav.knowledge') }}</div>
        <div class="page-subtitle">{{ $t('knowledge') || 'ReMe 记忆系统' }}</div>
      </div>
      <div class="flex gap-2">
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
      <el-select v-model="filterType" :placeholder="$t('knowledge_col_type')" size="small" style="width:130px" @change="load">
        <el-option :label="$t('common.all')" value="" />
        <el-option :label="$t('knowledge_type_api')" value="api" />
        <el-option :label="$t('knowledge_type_scenario')" value="scenario" />
        <el-option :label="$t('knowledge_type_monitor')" value="monitor" />
        <el-option :label="$t('knowledge_type_general')" value="general" />
      </el-select>
    </div>

    <div class="page-body" style="padding-top:0" v-loading="loading">
      <el-card style="padding:0">
      <el-table
        :data="items"
        row-key="id"
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
        <!-- 标签列：支持横向滚动查看所有标签 -->
        <el-table-column :label="$t('knowledge_col_tags')" min-width="160">
          <template #default="{ row }">
            <div v-if="row.tags?.length" class="tag-scroll-container">
              <el-tag v-for="tag in row.tags" :key="tag" size="small" class="tag-chip">{{ tag }}</el-tag>
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
        <!-- Bug3 修复：操作列用 link 按钮 + nowrap 防止换行 -->
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

      <AppPagination v-model:page="page" :page-size="pageSize" :total="total" @page-change="load" />
      </el-card>
    </div>

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
import { ref, onMounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { knowledgeApi } from '@/api'
import { useProjectStore, useToastStore } from '@/stores'
import { fmt } from '@/utils'
import AppPagination from '@/components/AppPagination.vue'

const { t } = useI18n()
const toast = useToastStore()
const projectStore = useProjectStore()

const items    = ref<any[]>([])
const total    = ref(0)
const loading  = ref(false)
const page     = ref(1)
const pageSize = 20
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
      skip: (page.value - 1) * pageSize,
      limit: pageSize,
    }
    if (search.value) params.search = search.value
    if (filterType.value) params.type = filterType.value
    const res = await knowledgeApi.list(params)
    items.value = res.items || []
    total.value = res.total || 0
  } catch (e: any) {
    toast.error(e.message || t('settings.knowledge_load_failed'))
  } finally { loading.value = false }
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
    const res = await knowledgeApi.batchExtract(projectStore.current)
    toast.success(t('knowledge_toast_batch_done', { created: res.created ?? 0, merged: res.merged ?? 0 }))
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
    await knowledgeApi.batchDelete(ids)
    toast.success(t('knowledge_toast_batch_deleted', { n: ids.length }))
    selected.value = []
    await load()
  } catch (e: any) {
    toast.error(e.message)
  }
}

watch(() => projectStore.current, () => { page.value = 1; load() })
onMounted(load)
</script>

<style scoped>
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
