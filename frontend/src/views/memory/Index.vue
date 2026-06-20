<template>
  <div>
    <!-- 全局检索栏 -->
    <div class="filter-bar">
      <el-input
        v-model="searchQuery"
        :placeholder="$t('memory.searchPlaceholder')"
        size="small"
        style="width:280px"
        clearable
        @keyup.enter="doSearch"
        @clear="loadActive"
      />
      <el-button size="small" type="primary" @click="doSearch" :loading="searching">
        {{ $t('memory.search') }}
      </el-button>
      <!-- 项目下拉切换：选项来自已添加的项目，可清除展示全部 -->
      <el-select
        v-model="projectStore.current"
        size="small"
        style="width:200px;margin-left:12px"
        :placeholder="$t('memory.selectProject')"
        clearable
        @change="onProjectChange"
      >
        <el-option
          v-for="p in projectStore.projects"
          :key="p.id"
          :label="p.name"
          :value="p.id"
        />
      </el-select>
    </div>

    <!-- Tab 切换 L1/L2/L3 -->
    <div class="page-body" style="padding-top:0">
      <el-tabs v-model="activeTab" @tab-change="loadActive">
        <el-tab-pane :label="$t('memory.tabL1')" name="l1">
          <el-card style="padding:0" v-loading="loading.l1">
            <el-table :data="l1Items" row-key="key" max-height="calc(100vh - 280px)" :empty-text="$t('memory.emptyL1')">
              <el-table-column :label="$t('memory.colKey')" width="220">
                <template #default="{ row }">
                  <span class="mono" style="font-size:12px;font-weight:600">{{ row.key }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('memory.colContent')" min-width="300">
                <template #default="{ row }">
                  <span class="text-2" style="font-size:12px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden">
                    {{ (row.content || '').slice(0, 300) }}{{ (row.content || '').length > 300 ? '…' : '' }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('memory.colSource')" width="100">
                <template #default="{ row }">
                  <el-tag size="small">{{ $t('memory.source_' + row.source) || row.source || '—' }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column :label="$t('memory.colTags')" width="180">
                <template #default="{ row }">
                  <el-tag v-for="tag in (row.tags || [])" :key="tag" size="small" class="tag-chip">{{ tagLabel(tag) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column :label="$t('memory.colConfirmedCount')" width="80" align="center">
                <template #default="{ row }">{{ row.confirmed_count ?? 0 }}</template>
              </el-table-column>
              <el-table-column :label="$t('memory.colUpdatedAt')" width="160">
                <template #default="{ row }">{{ row.updated_at || row.created_at || '—' }}</template>
              </el-table-column>
              <el-table-column :label="$t('memory.colAction')" width="80" align="center">
                <template #default="{ row }">
                  <el-popconfirm :title="$t('memory.confirmDeleteL1')" @confirm="removeL1(row.key)">
                    <template #reference>
                      <el-button type="danger" size="small" text>{{ $t('memory.delete') }}</el-button>
                    </template>
                  </el-popconfirm>
                </template>
              </el-table-column>
            </el-table>
            <div class="flex-between" style="padding:12px 16px">
              <span class="text-3">{{ $t('memory.totalItems', { n: l1Total }) }}</span>
              <el-pagination
                v-model:current-page="l1Page"
                :page-size="50"
                :total="l1Total"
                layout="prev,next"
                small
                @current-change="loadL1"
              />
            </div>
          </el-card>
        </el-tab-pane>

        <el-tab-pane :label="$t('memory.tabL2')" name="l2">
          <el-card style="padding:0" v-loading="loading.l2">
            <el-table :data="l2Items" row-key="id" max-height="calc(100vh - 280px)" :empty-text="$t('memory.emptyL2')">
              <el-table-column :label="$t('memory.colTitle')" min-width="200">
                <template #default="{ row }">
                  <span class="mono" style="font-size:13px;font-weight:600">{{ row.title }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('memory.colType')" width="120">
                <template #default="{ row }">
                  <el-tag size="small" type="info">{{ typeLabel(row.type) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column :label="$t('memory.colContent')" min-width="280">
                <template #default="{ row }">
                  <span class="text-2" style="font-size:12px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden">
                    {{ (row.content || '').slice(0, 300) }}{{ (row.content || '').length > 300 ? '…' : '' }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('memory.colTags')" width="180">
                <template #default="{ row }">
                  <el-tag v-for="tag in (row.tags || [])" :key="tag" size="small" class="tag-chip">{{ tagLabel(tag) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column :label="$t('memory.colCreatedAt')" width="160">
                <template #default="{ row }">{{ row.created_at || '—' }}</template>
              </el-table-column>
              <el-table-column :label="$t('memory.colAction')" width="80" align="center">
                <template #default="{ row }">
                  <el-popconfirm :title="$t('memory.confirmDeleteL2')" @confirm="removeL2(row.id)">
                    <template #reference>
                      <el-button type="danger" size="small" text>{{ $t('memory.delete') }}</el-button>
                    </template>
                  </el-popconfirm>
                </template>
              </el-table-column>
            </el-table>
            <!-- L2 分页 -->
            <div class="flex-between" style="padding:12px 16px">
              <span class="text-3">{{ $t('memory.totalItems', { n: l2Total }) }}</span>
              <el-pagination
                v-model:current-page="l2Page"
                :page-size="l2PageSize"
                :total="l2Total"
                layout="prev,next"
                small
                @current-change="loadL2"
              />
            </div>
          </el-card>
        </el-tab-pane>

        <el-tab-pane :label="$t('memory.tabL3')" name="l3">
          <el-card style="padding:0" v-loading="loading.l3">
            <el-table :data="l3Items" row-key="id" max-height="calc(100vh - 280px)" :empty-text="$t('memory.emptyL3')">
              <el-table-column :label="$t('memory.colSessionId')" width="200">
                <template #default="{ row }">
                  <span class="mono" style="font-size:11px">{{ row.session_id?.slice(0, 12) }}…</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('memory.colSummary')" min-width="300">
                <template #default="{ row }">
                  <span class="text-2" style="font-size:12px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden">
                    {{ (row.summary || '').slice(0, 300) }}{{ (row.summary || '').length > 300 ? '…' : '' }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('memory.colUser')" width="100">
                <template #default="{ row }">{{ row.user_id || '—' }}</template>
              </el-table-column>
              <el-table-column :label="$t('memory.colTags')" width="160">
                <template #default="{ row }">
                  <el-tag v-for="tag in (row.tags || [])" :key="tag" size="small" class="tag-chip">{{ tagLabel(tag) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column :label="$t('memory.colCreatedAt')" width="160">
                <template #default="{ row }">{{ row.created_at || '—' }}</template>
              </el-table-column>
              <el-table-column :label="$t('memory.colExpiresAt')" width="160">
                <template #default="{ row }">{{ row.expires_at || '—' }}</template>
              </el-table-column>
              <!-- P1-3: L3 删除按钮（后端已有 DELETE /memory/l3/{session_id} 端点） -->
              <el-table-column :label="$t('memory.colAction')" width="80" align="center">
                <template #default="{ row }">
                  <el-popconfirm :title="$t('memory.confirmDeleteL3')" @confirm="removeL3(row.session_id)">
                    <template #reference>
                      <el-button type="danger" size="small" text>{{ $t('memory.delete') }}</el-button>
                    </template>
                  </el-popconfirm>
                </template>
              </el-table-column>
            </el-table>
            <!-- L3 分页 -->
            <div class="flex-between" style="padding:12px 16px">
              <span class="text-3">{{ $t('memory.totalItems', { n: l3Total }) }}</span>
              <el-pagination
                v-model:current-page="l3Page"
                :page-size="l3PageSize"
                :total="l3Total"
                layout="prev,next"
                small
                @current-change="loadL3"
              />
            </div>
          </el-card>
        </el-tab-pane>

        <el-tab-pane :label="$t('memory.tabSearch')" name="search">
          <el-card style="padding:0" v-loading="searching">
            <div v-if="searchResults" class="search-results">
              <!-- L1 结果 -->
              <div v-if="searchResults.l1?.length" class="result-group">
                <div class="result-group-title">{{ $t('memory.resultGroupL1', { n: searchResults.l1.length }) }}</div>
                <div v-for="item in searchResults.l1" :key="item.key" class="result-item">
                  <div class="result-label">{{ item.key }}</div>
                  <div class="text-2">{{ item.content?.slice(0, 200) }}</div>
                </div>
              </div>
              <!-- L2 结果 -->
              <div v-if="searchResults.l2?.length" class="result-group">
                <div class="result-group-title">{{ $t('memory.resultGroupL2', { n: searchResults.l2.length }) }}</div>
                <div v-for="item in searchResults.l2" :key="item.id" class="result-item">
                  <div class="result-label">[{{ item.type }}] {{ item.title }}</div>
                  <div class="text-2">{{ item.content?.slice(0, 200) }}</div>
                </div>
              </div>
              <!-- L3 结果 -->
              <div v-if="searchResults.l3?.length" class="result-group">
                <div class="result-group-title">{{ $t('memory.resultGroupL3', { n: searchResults.l3.length }) }}</div>
                <div v-for="item in searchResults.l3" :key="item.id" class="result-item">
                  <div class="text-2">{{ item.summary?.slice(0, 200) }}</div>
                </div>
              </div>
              <!-- 语义检索结果 -->
              <div v-if="searchResults.semantic?.length" class="result-group">
                <div class="result-group-title">{{ $t('memory.resultGroupSemantic', { n: searchResults.semantic.length }) }}</div>
                <div v-for="(item, idx) in searchResults.semantic" :key="idx" class="result-item">
                  <div class="text-2">{{ typeof item === 'string' ? item : (item.text || item.content || JSON.stringify(item).slice(0, 200)) }}</div>
                </div>
              </div>
              <!-- 无结果 -->
              <div v-if="!searchResults.l1?.length && !searchResults.l2?.length && !searchResults.l3?.length && !searchResults.semantic?.length" class="text-3" style="padding:24px;text-align:center">
                {{ $t('memory.noResults') }}
              </div>
            </div>
            <div v-else class="text-3" style="padding:24px;text-align:center">
              {{ $t('memory.searchHint') }}
            </div>
          </el-card>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { memoryApi } from '@/api'
import { useProjectStore, useToastStore } from '@/stores'

const { t } = useI18n()
const projectStore = useProjectStore()
const toast = useToastStore()

const activeTab = ref('l1')
const searchQuery = ref('')
// 使用 store 获取当前项目；为 null 时不传 project_id（后端返回全部项目数据）
const projectId = computed(() => projectStore.current || null)
const searching = ref(false)
const searchResults = ref(null)

const loading = reactive({ l1: false, l2: false, l3: false })

// L1 state
const l1Items = ref([])
const l1Total = ref(0)
const l1Page = ref(1)

// L2 state
const l2Items = ref([])
const l2Total = ref(0)
const l2Page = ref(1)
const l2PageSize = 30

// L3 state
const l3Items = ref([])
const l3Total = ref(0)
const l3Page = ref(1)
const l3PageSize = 30

// ── loaders ───────────────────────────────────────────────────

async function loadL1() {
  loading.l1 = true
  try {
    // L1 为跨项目长期记忆，不传 project_id
    const params = { skip: (l1Page.value - 1) * 50, limit: 50 }
    if (searchQuery.value) params.search = searchQuery.value
    const res = await memoryApi.listL1(params)
    l1Items.value = res.items || []
    l1Total.value = res.total || 0
  } catch (e) {
    toast.error(t('memory.loadFailed'))
    console.error('loadL1 failed:', e)
  } finally {
    loading.l1 = false
  }
}

async function loadL2() {
  loading.l2 = true
  try {
    const params = { skip: (l2Page.value - 1) * l2PageSize, limit: l2PageSize }
    // 不选项目时，不传 project_id，后端返回全部项目数据
    if (projectId.value) params.project_id = projectId.value
    if (searchQuery.value) params.search = searchQuery.value
    const res = await memoryApi.listL2(params)
    l2Items.value = res.items || []
    l2Total.value = res.total || 0
  } catch (e) {
    toast.error(t('memory.loadFailed'))
    console.error('loadL2 failed:', e)
  } finally {
    loading.l2 = false
  }
}

async function loadL3() {
  loading.l3 = true
  try {
    const params = { skip: (l3Page.value - 1) * l3PageSize, limit: l3PageSize }
    if (projectId.value) params.project_id = projectId.value
    if (searchQuery.value) params.search = searchQuery.value
    const res = await memoryApi.listL3(params)
    l3Items.value = res.items || []
    l3Total.value = res.total || 0
  } catch (e) {
    toast.error(t('memory.loadFailed'))
    console.error('loadL3 failed:', e)
  } finally {
    loading.l3 = false
  }
}

async function doSearch() {
  searching.value = true
  searchResults.value = null
  try {
    const params = { query: searchQuery.value || '', limit: 10 }
    if (projectId.value) params.project_id = projectId.value
    searchResults.value = await memoryApi.search(params)
  } catch (e) {
    toast.error(t('memory.loadFailed'))
    console.error('search failed:', e)
  } finally {
    searching.value = false
  }
}

function loadActive() {
  if (activeTab.value === 'l1') loadL1()
  else if (activeTab.value === 'l2') loadL2()
  else if (activeTab.value === 'l3') loadL3()
}

/* 项目下拉切换：调用 store.select() 同步状态，loadActive 由 watch 自动触发 */
function onProjectChange(newId) {
  l2Page.value = 1
  l3Page.value = 1
  projectStore.select(newId)
}

// ── delete ────────────────────────────────────────────────────

async function removeL1(key) {
  try {
    await memoryApi.deleteL1(key)
    loadL1()
  } catch (e) {
    toast.error(t('memory.deleteFailed'))
    console.error('delete L1 failed:', e)
  }
}

async function removeL2(id) {
  try {
    await memoryApi.deleteL2(id)
    loadL2()
  } catch (e) {
    toast.error(t('memory.deleteFailed'))
    console.error('delete L2 failed:', e)
  }
}

// P1-3: 删除 L3 会话记忆
async function removeL3(sessionId) {
  try {
    await memoryApi.deleteL3(sessionId)
    loadL3()
  } catch (e) {
    toast.error(t('memory.deleteFailed'))
    console.error('delete L3 failed:', e)
  }
}

// 类型翻译：后端 type 可能带 "memory.type_" 前缀，去掉前缀后尝试 i18n 翻译
function typeLabel(type) {
  if (!type) return '—'
  // 去掉可能的 "memory.type_" 前缀
  const clean = type.startsWith('memory.type_') ? type.slice(12) : type
  // 优先查 memory.type_<clean>，再查 knowledge_type_<clean>，都不行则显示 clean
  const memoryKey = 'memory.type_' + clean
  const translated = t(memoryKey)
  if (translated !== memoryKey) return translated
  const knowledgeKey = 'knowledge_type_' + clean
  const kTranslated = t(knowledgeKey)
  return kTranslated !== knowledgeKey ? kTranslated : clean
}

// 标签翻译：后端 tag 可能带 "memory." 前缀（如 memory.tag_review），去掉前缀后尝试 i18n 翻译
// 同时处理 "memory.tag_api:UUID" 后缀，去掉 UUID 部分后查翻译
function tagLabel(tag) {
  // 去掉 "memory." 或 "memory.tag_" 前缀，得到纯 key（如 "tag_api:UUID" 或 "review"）
  let clean = tag.startsWith('memory.tag_') ? tag.slice(11) : tag.startsWith('memory.') ? tag.slice(7) : tag
  // 去掉 ":UUID" 后缀（如 "tag_api:384fd1b8-..." → "tag_api"）
  const colonIdx = clean.indexOf(':')
  if (colonIdx !== -1) clean = clean.slice(0, colonIdx)
  const key = 'memory.tag_' + clean
  const translated = t(key)
  return translated !== key ? translated : tag
}

onMounted(() => loadL1())

// 切换项目时自动刷新当前 tab
watch(() => projectStore.current, () => { loadActive() })
</script>

<style scoped>
.tag-chip {
  margin-right: 4px;
  margin-bottom: 2px;
}
.search-results {
  padding: 8px 0;
}
.result-group {
  padding: 12px 16px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.result-group-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-color-primary);
  margin-bottom: 8px;
}
.result-item {
  padding: 8px 12px;
  margin-bottom: 4px;
  background: var(--el-fill-color-light);
  border-radius: 4px;
}
.result-label {
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 4px;
}
</style>
