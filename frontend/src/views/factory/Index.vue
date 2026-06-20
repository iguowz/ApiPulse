<template>
  <div class="page">
    <div class="page-header">
      <div>
        <div class="page-title">{{ $t('factory.title') }}</div>
        <div class="page-subtitle">{{ $t('factory.subtitle') }}</div>
      </div>
      <el-button v-if="activeTab === 'templates'" type="primary" size="small" @click="showCreateModal = true">{{ $t('factory.new_btn') }}</el-button>
    </div>

    <div class="page-body">
      <el-tabs v-model="activeTab" class="factory-tabs">
        <el-tab-pane :label="$t('factory.tab_templates')" name="templates" />
        <el-tab-pane :label="$t('factory.tab_datasets')" name="datasets" />
      </el-tabs>

      <div v-if="activeTab === 'templates'" class="factory-layout">
        <!-- 模板列表 -->
        <div class="tmpl-panel">
          <div class="panel-header">
            <span class="card-title">{{ $t('factory.panel_title') }}</span>
            <el-input v-model="tmplSearch" :placeholder="$t('factory.search_placeholder')" size="small" />
          </div>
          <div class="tmpl-list">
            <div
              v-for="t in filteredTemplates"
              :key="t.id"
              :class="['tmpl-item', selectedTmpl?.id === t.id && 'active']"
              @click="selectTemplate(t)"
            >
              <div class="tmpl-name">{{ t.name }}</div>
              <div class="tmpl-meta">
                <el-tag type="info" size="small">{{ $t('factory.field_count', { n: t.fields?.length || 0 }) }}</el-tag>
                <el-tag size="small" :type="t.source === 'ai_enhanced' ? 'success' : 'info'">{{ t.source || 'manual' }}</el-tag>
                <el-tag v-if="t.job_id" size="small" type="warning">AI</el-tag>
                <el-tag v-if="t.scenario_id" size="small" type="success">{{ scenarioLabel(t.scenario_id) || t.scenario_id }}</el-tag>
                <span class="text-3 mono" style="font-size:10px">{{ apiLabel(t.api_id) || $t('factory.common_template') }}</span>
              </div>
            </div>
            <div v-if="loadingTmpl" style="display:flex;align-items:center;justify-content:center;padding:30px">
              <span class="spinner"></span>
            </div>
            <el-empty v-else-if="!filteredTemplates.length" :description="$t('factory.no_templates')" :image-size="48" style="padding:30px" />
          </div>
        </div>

        <!-- 模板编辑 + 造数预览 -->
        <div class="tmpl-editor" v-if="selectedTmpl">
          <div class="editor-header">
            <div>
              <div style="font-weight:600;font-size:14px">{{ selectedTmpl.name }}</div>
              <div class="text-3 mono" style="font-size:11px">{{ apiLabel(selectedTmpl.api_id) || $t('factory.project_common_template') }}</div>
              <div v-if="selectedTmpl.scenario_id" class="text-3" style="font-size:11px;margin-top:2px">
                {{ $t('factory.linked_scenario') }}: {{ scenarioLabel(selectedTmpl.scenario_id) }}
              </div>
              <div class="editor-flags">
                <el-tag v-if="hasUnsavedChanges" size="small" type="warning">{{ $t('factory.unsaved_changes') }}</el-tag>
                <el-tag v-if="aiJob?.status" size="small" type="success">AI {{ aiJob.status }}</el-tag>
              </div>
            </div>
            <div class="flex items-center gap-8">
              <el-button size="small" @click="inferTemplate" :disabled="inferring">{{ inferring ? $t('factory.inferring') : '⟳ ' + $t('factory.infer_btn') }}</el-button>
              <!-- P1-1: AI 增强模板字段配置（语义识别 + 边界值 + invalid 候选） -->
              <el-button size="small" @click="aiEnhanceTemplate" :disabled="aiEnhancing">✨ {{ aiEnhancing ? $t('factory.ai_enhancing') : $t('factory.ai_enhance_btn') }}</el-button>
              <el-button size="small" @click="openDuplicateDialog">{{ $t('factory.duplicate_template') }}</el-button>
              <el-button size="small" type="warning" plain @click="createScenarioFromTemplate" :disabled="creatingScenario || !selectedTmpl.api_id">
                {{ creatingScenario ? $t('factory.creating_scenario') : $t('factory.add_scenario') }}
              </el-button>
              <el-button type="success" size="small" @click="generateData" :disabled="generating">{{ generating ? $t('factory.generating') : '▶ ' + $t('factory.generate_btn') }}</el-button>
              <el-button type="primary" size="small" @click="saveTmpl" :disabled="saving">{{ saving ? $t('factory.saving') : $t('common.save') }}</el-button>
              <el-button type="danger" size="small" @click="deleteTmpl">{{ $t('common.delete') }}</el-button>
            </div>
          </div>

          <!-- 字段列表 -->
          <div class="fields-table-wrap">
            <el-alert
              v-if="draftIssues.length"
              type="warning"
              :closable="false"
              show-icon
              class="validation-alert"
            >
              <template #title>
                <span v-for="issue in draftIssues.slice(0, 3)" :key="issue" class="issue-line">{{ issue }}</span>
              </template>
            </el-alert>
            <el-table :data="editFields" :empty-text="$t('factory.no_fields_hint')" size="small">
              <el-table-column :label="$t('factory.col_field_name')">
                <template #default="{ row: f }">
                  <el-input v-model="f.name" size="small" class="mono" style="width:140px;font-size:12px" />
                </template>
              </el-table-column>
              <el-table-column :label="$t('factory.col_faker_method')">
                <template #default="{ row: f }">
                  <!-- P0-1: faker 方法按分组渲染（12 组 94 个方法），支持搜索 -->
                  <el-select v-model="f.faker_method" size="small" style="width:140px;font-size:12px" clearable filterable :placeholder="$t('factory.faker_placeholder', '选择方法')">
                    <el-option-group v-for="grp in fakerGroups" :key="grp.group" :label="grp.label_zh">
                      <el-option v-for="m in grp.methods" :key="m" :label="m" :value="m" />
                    </el-option-group>
                  </el-select>
                </template>
              </el-table-column>
              <el-table-column :label="$t('factory.col_enum_values')">
                <template #default="{ row: f }">
                  <el-input v-model="f._enum_str" :placeholder="$t('factory.enum_placeholder')" size="small" style="width:100px;font-size:12px" @change="f.enum_values = parseEnum(f._enum_str)" />
                </template>
              </el-table-column>
              <el-table-column :label="$t('factory.col_boundary_min')">
                <template #default="{ row: f }">
                  <el-input v-model.number="f.boundary_min" type="number" size="small" style="width:70px;font-size:12px" />
                </template>
              </el-table-column>
              <el-table-column :label="$t('factory.col_boundary_max')">
                <template #default="{ row: f }">
                  <el-input v-model.number="f.boundary_max" type="number" size="small" style="width:70px;font-size:12px" />
                </template>
              </el-table-column>
              <el-table-column :label="$t('factory.col_fixed_value')">
                <template #default="{ row: f }">
                  <el-input v-model="f.fixed_value" :placeholder="$t('factory.fixed_placeholder')" size="small" style="width:90px;font-size:12px" />
                </template>
              </el-table-column>
              <el-table-column :label="$t('factory.col_null_rate')">
                <template #default="{ row: f }">
                  <el-input v-model.number="f.null_rate" type="number" :min="0" :max="1" :step="0.1" size="small" style="width:58px;font-size:12px" />
                </template>
              </el-table-column>
              <el-table-column :label="$t('factory.col_invalid_rate')">
                <template #default="{ row: f }">
                  <el-input v-model.number="f.invalid_rate" type="number" :min="0" :max="1" :step="0.1" size="small" style="width:58px;font-size:12px" />
                </template>
              </el-table-column>
              <!-- P0-2: 补齐 empty_rate 列（后端 _generate_field 支持但前端无入口） -->
              <el-table-column :label="$t('factory.col_empty_rate')">
                <template #default="{ row: f }">
                  <el-input v-model.number="f.empty_rate" type="number" :min="0" :max="1" :step="0.1" size="small" style="width:58px;font-size:12px" />
                </template>
              </el-table-column>
              <!-- P0-2: invalid_values 候选池（配合 invalid_rate，逗号分隔输入） -->
              <el-table-column :label="$t('factory.col_invalid_values')">
                <template #default="{ row: f }">
                  <el-input v-model="f._invalid_str" :placeholder="$t('factory.invalid_values_placeholder', '值1,值2')" size="small" style="width:110px;font-size:12px" @change="f.invalid_values = parseEnum(f._invalid_str)" />
                </template>
              </el-table-column>
              <el-table-column label="" width="50">
                <template #default="{ row: f, $index: i }">
                  <el-button type="danger" size="small" :icon="Close" @click="editFields.splice(i,1)" />
                </template>
              </el-table-column>
            </el-table>
            <div style="padding:12px 0">
              <el-button size="small" @click="addField">{{ $t('factory.add_field') }}</el-button>
              <el-button size="small" @click="duplicateLastField" :disabled="!editFields.length">{{ $t('factory.duplicate_field') }}</el-button>
              <el-button size="small" @click="sortFields" :disabled="!editFields.length">{{ $t('factory.sort_fields') }}</el-button>
              <el-button size="small" @click="removeEmptyFields" :disabled="!editFields.length">{{ $t('factory.remove_empty_fields') }}</el-button>
            </div>
          </div>

          <!-- 生成预览 -->
          <div v-if="generatedData.length" class="gen-preview">
            <div class="preview-header">
              <span class="card-title">{{ $t('factory.preview_title', { n: genCount }) }} · {{ $t('factory.current_draft') }}</span>
              <div class="flex items-center gap-8">
                <!-- P1-2: count 放开到 100（后端支持上限），增加 20/50/100 档位 -->
                <el-select v-model.number="genCount" @change="generateData" size="small" style="width:90px">
                  <el-option v-for="n in [1,3,5,10,20,50,100]" :key="n" :label="n + ' 条'" :value="n" />
                </el-select>
                <el-button type="primary" size="small" @click="openSaveDatasetDialog">{{ $t('factory.save_dataset') }}</el-button>
                <el-button size="small" @click="copyGenData">{{ $t('factory.copy_json') }}</el-button>
                <!-- P1-2: 导出 CSV/JSON 文件（前端 Blob 下载，无需后端端点） -->
                <el-dropdown size="small" @command="exportGenData">
                  <el-button size="small">⬇ {{ $t('factory.export_btn', '导出') }}</el-button>
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item command="json">JSON</el-dropdown-item>
                      <el-dropdown-item command="csv">CSV</el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
              </div>
            </div>
            <pre class="code-block gen-code">{{ jsonPretty(generatedData) }}</pre>
          </div>
        </div>

        <!-- 未选择时的占位 -->
        <div class="tmpl-editor empty-editor" v-else>
          <el-empty :description="$t('factory.panel_empty')" :image-size="48" />
        </div>
      </div>

      <div v-else class="dataset-layout">
        <div class="dataset-panel">
          <div class="panel-header dataset-header">
            <div>
              <span class="card-title">{{ $t('factory.datasets_title') }}</span>
              <div class="text-3 dataset-subtitle">{{ $t('factory.datasets_subtitle') }}</div>
            </div>
            <el-button size="small" @click="loadDatasets">{{ $t('common.refresh') }}</el-button>
          </div>
          <el-table
            :data="datasets"
            v-loading="loadingDatasets"
            size="small"
            class="dataset-table"
            :empty-text="$t('factory.datasets_empty')"
          >
            <el-table-column prop="name" :label="$t('common.name')" min-width="180" />
            <el-table-column :label="$t('factory.dataset_template')" min-width="160">
              <template #default="{ row: ds }">
                <span>{{ templateLabel(ds.template_id) || ds.template_id || $t('factory.common_template') }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="$t('factory.col_api')" min-width="180">
              <template #default="{ row: ds }">
                <span class="mono text-3">{{ apiLabel(ds.api_id) || ds.api_id || '-' }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="count" :label="$t('factory.dataset_count')" width="100" />
            <el-table-column :label="$t('factory.dataset_created')" width="180">
              <template #default="{ row: ds }">{{ fmt.time(ds.created_at) }}</template>
            </el-table-column>
            <el-table-column :label="$t('common.actions')" width="170" fixed="right">
              <template #default="{ row: ds }">
                <el-button size="small" @click="openDataset(ds)">{{ $t('common.preview') }}</el-button>
                <el-button size="small" type="danger" @click="deleteDataset(ds)">{{ $t('common.delete') }}</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </div>
    </div>

    <!-- 新建模板 dialog -->
    <el-dialog v-model="showCreateModal" :title="$t('factory.dialog_new')" width="420px">
      <el-form label-position="top">
        <el-form-item :label="$t('factory.form_tmpl_name')">
          <el-input v-model="newTmpl.name" :placeholder="$t('factory.placeholder_tmpl_name')" />
        </el-form-item>
        <el-form-item :label="$t('factory.form_scenario_id')">
          <el-select v-model="newTmpl.scenario_id" clearable filterable :placeholder="$t('factory.scenario_select_placeholder')" style="width:100%" @change="onScenarioSelect">
            <el-option
              v-for="s in scenarioList"
              :key="s.id"
              :label="s.name"
              :value="s.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="$t('factory.form_api_id')">
          <el-select v-model="newTmpl.api_id" clearable filterable :placeholder="$t('factory.api_select_placeholder')" style="width:100%">
            <el-option
              v-for="api in apiList"
              :key="api.id"
              :label="apiLabel(api.id)"
              :value="api.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateModal = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" @click="createTemplate" :disabled="creating || !newTmpl.name">
          {{ creating ? $t('factory.creating') : $t('common.create') }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="inferPreviewVisible" :title="$t('factory.infer_preview_title')" width="680px">
      <el-table :data="inferPreviewFields" size="small" max-height="360">
        <el-table-column prop="name" :label="$t('factory.infer_preview_field')" />
        <el-table-column prop="faker_method" :label="$t('factory.infer_preview_method')" />
        <el-table-column prop="description" :label="$t('factory.infer_preview_description')" />
      </el-table>
      <template #footer>
        <el-button @click="inferPreviewVisible = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" @click="applyInferPreview">{{ $t('factory.apply_to_draft') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="duplicateDialogVisible" :title="$t('factory.duplicate_template_title')" width="420px">
      <el-form label-position="top">
        <el-form-item :label="$t('factory.form_tmpl_name')">
          <el-input v-model="duplicateName" :placeholder="$t('factory.duplicate_name_placeholder')" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="duplicateDialogVisible = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" :disabled="duplicating || !duplicateName.trim()" @click="duplicateTemplate">
          {{ duplicating ? $t('factory.duplicating') : $t('factory.duplicate_template') }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="saveDatasetVisible" :title="$t('factory.save_dataset_title')" width="420px">
      <el-form label-position="top">
        <el-form-item :label="$t('factory.dataset_name')">
          <el-input v-model="datasetName" :placeholder="$t('factory.dataset_name_placeholder')" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="saveDatasetVisible = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" :disabled="savingDataset || !datasetName.trim()" @click="saveDataset">
          {{ savingDataset ? $t('factory.saving_dataset') : $t('common.save') }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="datasetPreviewVisible" :title="selectedDataset?.name || $t('factory.dataset_preview')" width="780px">
      <div class="dataset-preview-meta">
        <el-tag size="small" type="info">{{ $t('factory.dataset_record_count', { n: selectedDataset?.count || 0 }) }}</el-tag>
        <span class="text-3">{{ templateLabel(selectedDataset?.template_id) || selectedDataset?.template_id || $t('factory.common_template') }}</span>
      </div>
      <pre class="code-block dataset-code">{{ jsonPretty(selectedDataset?.records || []) }}</pre>
      <template #footer>
        <el-button @click="copyDataset">{{ $t('factory.copy_json') }}</el-button>
        <el-dropdown @command="exportDataset">
          <el-button>⬇ {{ $t('factory.export_btn') }}</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="json">JSON</el-dropdown-item>
              <el-dropdown-item command="csv">CSV</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button type="primary" @click="datasetPreviewVisible = false">{{ $t('common.close') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'
import { Close } from '@element-plus/icons-vue'
import { useRouter, useRoute } from 'vue-router'
import { factoryApi, apiApi, scenarioApi } from '@/api'
import { useToastStore, useProjectStore } from '@/stores'
import { fmt, jsonPretty, copyText } from '@/utils'

const { t } = useI18n()
const toast = useToastStore()
const projectStore = useProjectStore()
const router = useRouter()
const route = useRoute()

const templates       = ref([])
const loadingTmpl      = ref(false)
const selectedTmpl    = ref(null)
const editFields      = ref([])
const apiList         = ref([])
const scenarioList    = ref([])
const activeTab       = ref('templates')
const generatedData   = ref([])
const genCount        = ref(3)
const tmplSearch      = ref('')
const showCreateModal = ref(false)
const newTmpl         = ref({ name: '', api_id: '', scenario_id: '' })
const originalSnapshot = ref('')
const inferPreviewVisible = ref(false)
const inferPreviewFields = ref([])
const duplicateDialogVisible = ref(false)
const duplicateName = ref('')
const datasets = ref([])
const loadingDatasets = ref(false)
const saveDatasetVisible = ref(false)
const datasetName = ref('')
const savingDataset = ref(false)
const datasetPreviewVisible = ref(false)
const selectedDataset = ref(null)

const saving     = ref(false)
const creating   = ref(false)
const generating = ref(false)
const inferring  = ref(false)
const duplicating = ref(false)
const creatingScenario = ref(false)
// P1-1: AI 增强模板状态
const aiEnhancing = ref(false)
const aiJob = ref(null)

// P0-1: faker 方法从后端分组加载（替代硬编码 25 个，后端实际支持 94 个）
const fakerGroups = ref([])
// 兼容：保留扁平方法列表供旧逻辑引用
const fakerMethods = ref([])
async function loadFakerMethods() {
  try {
    const res = await factoryApi.fakerMethods()
    fakerGroups.value = res.groups || []
    fakerMethods.value = fakerGroups.value.flatMap(g => g.methods)
  } catch {
    // 接口失败兜底：保留最常用方法
    fakerMethods.value = ['name','email','phone_number','address','uuid4','random_int','word','iso8601']
  }
}
loadFakerMethods()

const filteredTemplates = computed(() => {
  if (!tmplSearch.value) return templates.value
  const q = tmplSearch.value.toLowerCase()
  return templates.value.filter(t => t.name.toLowerCase().includes(q) || t.api_id?.includes(q))
})

const draftIssues = computed(() => selectedTmpl.value ? validateDraft(buildDraftTemplate(), false) : [])
const hasUnsavedChanges = computed(() => selectedTmpl.value && originalSnapshot.value !== JSON.stringify(buildDraftTemplate()))

function apiLabel(apiId) {
  if (!apiId) return ''
  const api = apiList.value.find(a => a.id === apiId)
  if (!api) return String(apiId).slice(0, 8)
  const req = api.request || {}
  return `${req.method || api.method || ''} ${req.path || api.path || api.name || api.id}`.trim()
}

function scenarioLabel(scenarioId) {
  if (!scenarioId) return ''
  const s = scenarioList.value.find(s => s.id === scenarioId)
  return s ? s.name : String(scenarioId).slice(0, 8)
}

function templateLabel(templateId) {
  if (!templateId) return ''
  return templates.value.find(tmpl => tmpl.id === templateId)?.name || ''
}

function parseEnum(s) {
  if (!s) return null
  const raw = String(s).trim()
  try {
    const parsed = JSON.parse(raw)
    if (Array.isArray(parsed)) return parsed
  } catch {}
  return raw.split(',').map(x => x.trim()).filter(Boolean)
}

function decorateField(f = {}) {
  return {
    name: f.name || '',
    faker_method: f.faker_method || '',
    enum_values: f.enum_values || null,
    boundary_min: f.boundary_min ?? null,
    boundary_max: f.boundary_max ?? null,
    fixed_value: f.fixed_value ?? null,
    null_rate: f.null_rate ?? 0,
    invalid_rate: f.invalid_rate ?? 0,
    empty_rate: f.empty_rate ?? 0,
    invalid_values: f.invalid_values || null,
    nested_template: f.nested_template || null,
    description: f.description || '',
    _enum_str: f.enum_values ? JSON.stringify(f.enum_values) : '',
    _invalid_str: f.invalid_values ? JSON.stringify(f.invalid_values) : '',
  }
}

function normalizeField(f) {
  return {
    name: String(f.name || '').trim(),
    faker_method: f.faker_method || null,
    enum_values: parseEnum(f._enum_str) || null,
    boundary_min: f.boundary_min !== '' && f.boundary_min != null ? Number(f.boundary_min) : null,
    boundary_max: f.boundary_max !== '' && f.boundary_max != null ? Number(f.boundary_max) : null,
    fixed_value: f.fixed_value != null && f.fixed_value !== '' ? f.fixed_value : null,
    null_rate: Number(f.null_rate || 0),
    empty_rate: Number(f.empty_rate || 0),
    invalid_values: parseEnum(f._invalid_str) || null,
    invalid_rate: Number(f.invalid_rate || 0),
    nested_template: f.nested_template || null,
    description: f.description || '',
  }
}

function buildDraftTemplate() {
  if (!selectedTmpl.value) return null
  return {
    ...selectedTmpl.value,
    project_id: projectStore.current,
    api_id: selectedTmpl.value.api_id || '',
    fields: editFields.value.map(normalizeField),
  }
}

function validateDraft(template, includeEmpty = true) {
  if (!template) return []
  const issues = []
  const seen = new Set()
  const validFakers = new Set(fakerMethods.value)
  template.fields.forEach((f, index) => {
    const label = `第 ${index + 1} 行`
    if (!f.name) issues.push(`${label}: 字段名不能为空`)
    if (f.name && seen.has(f.name)) issues.push(`${label}: 字段名重复 ${f.name}`)
    if (f.name) seen.add(f.name)
    ;['null_rate', 'empty_rate', 'invalid_rate'].forEach(key => {
      if (Number.isNaN(Number(f[key])) || Number(f[key]) < 0 || Number(f[key]) > 1) issues.push(`${label}: ${key} 必须在 0..1`)
    })
    if (f.boundary_min != null && f.boundary_max != null && Number(f.boundary_min) > Number(f.boundary_max)) issues.push(`${label}: 最小边界不能大于最大边界`)
    if (f.faker_method && validFakers.size && !validFakers.has(f.faker_method)) issues.push(`${label}: faker 方法不在白名单 ${f.faker_method}`)
  })
  if (includeEmpty && !template.fields.length) issues.push('至少需要一个字段')
  return issues
}

// P0-2: addField 补齐 empty_rate/invalid_values/_invalid_str（后端支持但前端此前无入口）
function addField() {
  editFields.value.push(decorateField())
}

function duplicateLastField() {
  const source = editFields.value[editFields.value.length - 1]
  if (!source) return
  editFields.value.push({ ...decorateField(source), name: `${source.name || 'field'}_copy` })
}

function sortFields() {
  editFields.value = [...editFields.value].sort((a, b) => String(a.name || '').localeCompare(String(b.name || '')))
}

function removeEmptyFields() {
  editFields.value = editFields.value.filter(f => String(f.name || '').trim())
}

function selectTemplate(t) {
  selectedTmpl.value = t
  // P0-2: 回显 _invalid_str（invalid_values 数组转逗号字符串，与 _enum_str 一致）
  editFields.value = (t.fields || []).map(decorateField)
  generatedData.value = []
  aiJob.value = t.job_id ? { job_id: t.job_id, status: 'queued' } : null
  originalSnapshot.value = JSON.stringify(buildDraftTemplate())
}

async function loadTemplates() {
  loadingTmpl.value = true
  try {
    templates.value = await factoryApi.listTemplates(projectStore.current)
  } catch (e) {
    toast.error(e.message || t('factory.toast_load_failed'))
  } finally { loadingTmpl.value = false }
}

async function loadApis() {
  try {
    const res = await apiApi.list({ project_id: projectStore.current, limit: 500 })
    apiList.value = Array.isArray(res) ? res : (res.items || [])
  } catch {
    apiList.value = []
  }
}

async function loadScenarios() {
  try {
    const res = await scenarioApi.list({ project_id: projectStore.current, limit: 500 })
    scenarioList.value = Array.isArray(res) ? res : (res.items || [])
  } catch {
    scenarioList.value = []
  }
}

async function loadDatasets() {
  loadingDatasets.value = true
  try {
    datasets.value = await factoryApi.listDatasets(projectStore.current)
  } catch (e) {
    toast.error(e.message || t('factory.load_datasets_failed'))
  } finally { loadingDatasets.value = false }
}

// 选择场景时，自动从场景首个 API 步骤填充 api_id（允许手动覆盖）
function onScenarioSelect(scenarioId) {
  if (!scenarioId) return
  const s = scenarioList.value.find(s => s.id === scenarioId)
  if (!s) return
  const steps = s.steps || []
  for (const step of steps) {
    if (step.type === 'api' && step.api_id) {
      if (!newTmpl.value.api_id) {
        newTmpl.value.api_id = step.api_id
      }
      break
    }
  }
}

async function createTemplate() {
  creating.value = true
  try {
    const created = await factoryApi.createTemplate({
      ...newTmpl.value,
      project_id: projectStore.current,
      source: 'manual',
      fields: [],
    })
    toast.success(t('factory.toast_created'))
    showCreateModal.value = false
    newTmpl.value = { name: '', api_id: '', scenario_id: '' }
    await loadTemplates()
    selectTemplate(templates.value.find(x => x.id === created.id) || created)
  } catch (e) { toast.error(e.message) }
  finally { creating.value = false }
}

async function saveTmpl() {
  saving.value = true
  try {
    const draft = buildDraftTemplate()
    const issues = validateDraft(draft)
    if (issues.length) {
      toast.error(issues[0])
      return
    }
    await factoryApi.updateTemplate(selectedTmpl.value.id, {
      fields: draft.fields,
      name: selectedTmpl.value.name,
      api_id: selectedTmpl.value.api_id || '',
      source: selectedTmpl.value.source || 'manual',
    })
    toast.success(t('factory.toast_saved'))
    await loadTemplates()
    const latest = templates.value.find(x => x.id === selectedTmpl.value.id)
    if (latest) selectTemplate(latest)
  } catch (e) { toast.error(e.message) }
  finally { saving.value = false }
}

async function deleteTmpl() {
  try {
    await ElMessageBox.confirm(t('factory.confirm_delete_msg', { name: selectedTmpl.value.name }), t('factory.confirm_delete_title'), { type: 'warning' })
    await factoryApi.deleteTemplate(selectedTmpl.value.id)
    toast.success(t('factory.toast_deleted'))
    selectedTmpl.value = null
    editFields.value = []
    await loadTemplates()
  } catch (e) {
    // ElMessageBox reject 可能返回 'cancel'(按钮) 或 'close'(X关闭)，均忽略
    if (e !== 'cancel' && e !== 'close' && e.message) toast.error(e.message)
  }
}

function defaultDuplicateName() {
  return `${selectedTmpl.value?.name || t('factory.new')} - copy`
}

function openDuplicateDialog() {
  if (!selectedTmpl.value?.id) {
    toast.error(t('factory.toast_select_first'))
    return
  }
  duplicateName.value = defaultDuplicateName()
  duplicateDialogVisible.value = true
}

async function duplicateTemplate() {
  if (!selectedTmpl.value?.id || !duplicateName.value.trim()) return
  duplicating.value = true
  try {
    const copied = await factoryApi.duplicateTemplate(selectedTmpl.value.id, { name: duplicateName.value.trim() })
    toast.success(t('factory.template_duplicated'))
    duplicateDialogVisible.value = false
    await loadTemplates()
    selectTemplate(templates.value.find(x => x.id === copied.id) || copied)
  } catch (e) {
    toast.error(e.message || t('factory.duplicate_template_failed'))
  } finally {
    duplicating.value = false
  }
}

async function createScenarioFromTemplate() {
  if (!selectedTmpl.value?.id) {
    toast.error(t('factory.toast_select_first'))
    return
  }
  if (!selectedTmpl.value?.api_id) {
    toast.error(t('factory.scenario_requires_api'))
    return
  }
  creatingScenario.value = true
  try {
    const scenario = await factoryApi.createScenario(selectedTmpl.value.id)
    toast.success(t('factory.scenario_created'))
    router.push(`/scenarios/${scenario.id}`)
  } catch (e) {
    toast.error(e.message || t('factory.scenario_create_failed'))
  } finally {
    creatingScenario.value = false
  }
}

async function inferTemplate() {
  if (!selectedTmpl.value?.api_id) { toast.error(t('factory.toast_infer_hint')); return }
  inferring.value = true
  try {
    const res = await factoryApi.infer(selectedTmpl.value.api_id, projectStore.current)
    inferPreviewFields.value = (res.template?.fields || []).map(decorateField)
    inferPreviewVisible.value = true
    toast.success(t('factory.toast_inferred', { n: inferPreviewFields.value.length }))
  } catch (e) { toast.error(e.message) }
  finally { inferring.value = false }
}

function applyInferPreview() {
  editFields.value = inferPreviewFields.value.map(decorateField)
  inferPreviewVisible.value = false
  generatedData.value = []
}

// P1-1: AI 增强模板 —— 异步入队，完成后结果在审核中心（/generations）展示
async function aiEnhanceTemplate() {
  if (!selectedTmpl.value?.id) { toast.error(t('factory.toast_select_first')); return }
  aiEnhancing.value = true
  try {
    const res = await factoryApi.aiEnhance(selectedTmpl.value.id)
    aiJob.value = res
    // 异步任务，提示用户去审核中心查看结果
    toast.successAction(
      t('factory.ai_enhance_queued'),
      t('factory.go_review'),
      () => router.push('/generations?type=data_template&status=pending_review')
    )
  } catch (e) {
    toast.error(e.message || t('factory.ai_enhance_error'))
  } finally {
    aiEnhancing.value = false
  }
}

async function generateData() {
  if (generating.value) return  // 防止并行调用
  generating.value = true
  try {
    const draft = buildDraftTemplate()
    const issues = validateDraft(draft)
    if (issues.length) {
      toast.error(issues[0])
      return
    }
    const res = await factoryApi.generate({
      template: draft,
      count: genCount.value,
      project_id: projectStore.current,
    })
    generatedData.value = res.data || []
  } catch (e) { toast.error(e.message) }
  finally { generating.value = false }
}

async function copyGenData() {
  await copyText(JSON.stringify(generatedData.value, null, 2))
  toast.success(t('factory.toast_copied'))
}

function datasetDefaultName() {
  const base = selectedTmpl.value?.name || 'dataset'
  const d = new Date()
  const pad = n => String(n).padStart(2, '0')
  return `${base}-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}`
}

function openSaveDatasetDialog() {
  if (!generatedData.value.length) {
    toast.error(t('factory.no_generated_data'))
    return
  }
  datasetName.value = datasetDefaultName()
  saveDatasetVisible.value = true
}

async function saveDataset() {
  if (!generatedData.value.length || !datasetName.value.trim()) return
  savingDataset.value = true
  try {
    await factoryApi.createDataset({
      name: datasetName.value.trim(),
      template_id: selectedTmpl.value?.id || '',
      api_id: selectedTmpl.value?.api_id || '',
      project_id: projectStore.current,
      source: 'generated',
      records: generatedData.value,
    })
    toast.success(t('factory.dataset_saved'))
    saveDatasetVisible.value = false
    await loadDatasets()
  } catch (e) {
    toast.error(e.message || t('factory.dataset_save_failed'))
  } finally {
    savingDataset.value = false
  }
}

async function openDataset(ds) {
  try {
    selectedDataset.value = await factoryApi.getDataset(ds.id)
    datasetPreviewVisible.value = true
  } catch (e) {
    toast.error(e.message || t('factory.load_dataset_failed'))
  }
}

async function deleteDataset(ds) {
  try {
    await ElMessageBox.confirm(t('factory.dataset_delete_confirm', { name: ds.name }), t('factory.confirm_delete_title'), { type: 'warning' })
    await factoryApi.deleteDataset(ds.id)
    toast.success(t('factory.dataset_deleted'))
    if (selectedDataset.value?.id === ds.id) {
      selectedDataset.value = null
      datasetPreviewVisible.value = false
    }
    await loadDatasets()
  } catch (e) {
    // ElMessageBox reject 可能返回 'cancel'(按钮) 或 'close'(X关闭)，均忽略
    if (e !== 'cancel' && e !== 'close' && e.message) toast.error(e.message || t('factory.delete_dataset_failed'))
  }
}

async function copyDataset() {
  await copyText(JSON.stringify(selectedDataset.value?.records || [], null, 2))
  toast.success(t('factory.toast_copied'))
}

function exportDataset(format) {
  const rows = selectedDataset.value?.records || []
  if (!rows.length) return
  downloadRows(rows, selectedDataset.value?.name || 'dataset', format)
  toast.success(t('factory.toast_exported', '已导出'))
}

// P1-2/P2-3: 生成预览与沉淀数据集共用导出逻辑，避免两个入口 CSV 转义行为不一致。
function downloadRows(data, baseName, format) {
  let blob, filename
  if (format === 'csv') {
    // CSV：收集所有字段名作表头，每行一个对象
    const flatRows = data.map(r => flattenRecord(r))
    const allKeys = [...new Set(flatRows.flatMap(r => Object.keys(r)))]
    const header = allKeys.join(',')
    const rows = flatRows.map(r => allKeys.map(k => {
      const v = r[k]
      // 含逗号/换行/引号的值用双引号包裹并转义内部引号
      const s = v == null ? '' : String(v)
      return /[,"\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
    }).join(','))
    blob = new Blob([header + '\n' + rows.join('\n')], { type: 'text/csv;charset=utf-8;' })
    filename = `${baseName}.csv`
  } else {
    // JSON
    blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    filename = `${baseName}.json`
  }
  // 触发下载
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = filename
  link.click()
  URL.revokeObjectURL(link.href)
}

// P1-2: 导出生成数据为 JSON/CSV 文件（前端 Blob 下载）
function exportGenData(format) {
  const data = generatedData.value
  if (!data.length) return
  downloadRows(data, selectedTmpl.value?.name || 'data', format)
  toast.success(t('factory.toast_exported', '已导出'))
}

function flattenRecord(obj, prefix = '', out = {}) {
  Object.entries(obj || {}).forEach(([key, value]) => {
    const path = prefix ? `${prefix}.${key}` : key
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      flattenRecord(value, path, out)
    } else {
      out[path] = value
    }
  })
  return out
}

// 切换项目时自动刷新模板列表，重置编辑状态
watch(() => projectStore.current, () => {
  loadTemplates()
  loadApis()
  loadDatasets()
  selectedTmpl.value = null
  editFields.value = []
  generatedData.value = []
  aiJob.value = null
  inferPreviewVisible.value = false
  inferPreviewFields.value = []
  datasetPreviewVisible.value = false
  selectedDataset.value = null
  originalSnapshot.value = ''
})

// P1-1: onMounted 检查 api_id/scenario_id query
// - api_id: 从 API 详情页跳转时自动创建模板并推断字段
// - scenario_id: 从场景页"添加至数据工厂"跳转时预选场景
onMounted(async () => {
  await Promise.all([loadTemplates(), loadApis(), loadDatasets(), loadScenarios()])
  const apiId = route.query.api_id
  const scenarioId = route.query.scenario_id
  if (scenarioId) {
    // 从场景页跳转：自动打开创建对话框，预选场景并触发 api_id 自动填充
    showCreateModal.value = true
    newTmpl.value.scenario_id = scenarioId
    onScenarioSelect(scenarioId)
  }
  if (apiId) {
    // 检查是否已有该 API 的模板，无则自动创建 + 推断
    const existing = templates.value.find(t => t.api_id === apiId)
    if (existing) {
      selectTemplate(existing)
    } else {
      // 自动创建空模板
      try {
        const newT = await factoryApi.createTemplate({
          name: `auto:${String(apiId).slice(0, 8)}`,
          api_id: apiId,
          project_id: projectStore.current,
          source: 'inferred',
          fields: [],
        })
        templates.value.unshift(newT)
        selectTemplate(newT)
        // 自动触发字段推断（infer 基于 API 请求体结构）
        const inferred = await factoryApi.infer(apiId, projectStore.current)
        editFields.value = (inferred.template?.fields || []).map(decorateField)
        toast.success(t('factory.toast_auto_created', { n: editFields.value.length }))
      } catch (e) {
        toast.error(e.message || t('factory.toast_auto_create_failed'))
      }
    }
  }
})
</script>

<style scoped>
.factory-layout {
  display: grid;
  grid-template-columns: 260px 1fr;
  gap: 12px;
  height: calc(100% - 52px);
  min-height: 0;
}

.factory-tabs { margin-bottom: 12px; flex-shrink: 0; }

.tmpl-panel {
  background: var(--bg-2); border: 1px solid var(--border);
  border-radius: var(--radius-lg); display: flex; flex-direction: column;
  min-height: 0; overflow: hidden;
}
.panel-header { padding: 14px 14px 10px; border-bottom: 1px solid var(--border); display: flex; flex-direction: column; gap: 8px; }
.tmpl-list    { flex: 1; overflow-y: auto; }
.tmpl-item {
  padding: 10px 14px; border-bottom: 1px solid var(--border);
  cursor: pointer; transition: background .12s;
}
.tmpl-item:hover  { background: var(--bg-hover); }
.tmpl-item.active { background: rgba(79,142,247,.1); border-left: 2px solid var(--accent); }
.tmpl-name { font-size: 13px; font-weight: 500; margin-bottom: 4px; }
.tmpl-meta { display: flex; align-items: center; gap: 8px; }

.tmpl-editor {
  background: var(--bg-2); border: 1px solid var(--border);
  border-radius: var(--radius-lg); display: flex; flex-direction: column;
  min-height: 0; overflow: hidden;
}
.empty-editor { align-items: center; justify-content: center; }

.editor-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 18px; border-bottom: 1px solid var(--border); flex-shrink: 0;
}
.editor-flags { display: flex; align-items: center; gap: 6px; margin-top: 6px; }
.fields-table-wrap { flex: 1; overflow: auto; padding: 0 18px; }
.validation-alert { margin: 12px 0; }
.issue-line { display: block; line-height: 1.5; }

.gen-preview    { border-top: 1px solid var(--border); flex-shrink: 0; }
.preview-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 18px; border-bottom: 1px solid var(--border);
}
.gen-code { margin: 0; border-radius: 0; border: none; max-height: 220px; overflow-y: auto; }

.dataset-layout { height: calc(100% - 52px); min-height: 0; }
.dataset-panel {
  background: var(--bg-2); border: 1px solid var(--border);
  border-radius: var(--radius-lg); display: flex; flex-direction: column;
  height: 100%; min-height: 0; overflow: hidden;
}
.dataset-header { flex-direction: row; align-items: center; justify-content: space-between; }
.dataset-subtitle { margin-top: 4px; font-size: 12px; }
.dataset-table { flex: 1; min-height: 0; }
.dataset-preview-meta { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.dataset-code { max-height: 420px; overflow: auto; }
</style>
