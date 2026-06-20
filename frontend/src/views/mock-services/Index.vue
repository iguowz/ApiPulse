<template>
  <div class="page">
    <div class="page-header">
      <div>
        <div class="page-title">{{ $t('mockServices.title') }}</div>
        <div class="page-subtitle">{{ $t('mockServices.subtitle') }}</div>
      </div>
      <div class="flex items-center gap-8">
        <el-button size="small" @click="loadAll">{{ $t('common.refresh') }}</el-button>
        <el-button type="primary" size="small" @click="openServiceDialog()">{{ $t('mockServices.new_service') }}</el-button>
      </div>
    </div>

    <div class="page-body">
      <div class="mock-layout">
        <el-card class="service-list-card">
          <template #header>
            <div class="flex items-center justify-between">
              <span class="card-title">{{ $t('mockServices.services') }}</span>
              <el-tag size="small" type="info">{{ services.length }}</el-tag>
            </div>
          </template>
          <div v-loading="loadingServices" class="service-list">
            <div
              v-for="svc in services"
              :key="svc.id"
              :class="['service-item', selectedService?.id === svc.id && 'active']"
              @click="selectService(svc, true)"
            >
              <div class="flex items-center justify-between gap-8">
                <span class="service-name">{{ svc.name }}</span>
                <el-tag size="small" :type="svc.enabled ? 'success' : 'info'">
                  {{ svc.enabled ? $t('common.enabled') : $t('common.disabled') }}
                </el-tag>
              </div>
              <div class="text-3 mono service-url">{{ publicUrl(svc, false) }}</div>
              <div class="service-meta">
                <el-tag size="small" type="info">{{ $t('mockServices.route_count', { count: svc.route_count || 0 }) }}</el-tag>
                <el-tag v-if="svc.public_enabled" size="small" :type="svc.access_key ? 'warning' : 'danger'">
                  {{ svc.access_key ? $t('mockServices.public_enabled') : $t('mockServices.public_without_key') }}
                </el-tag>
                <el-tag v-if="svc.error_count" size="small" type="danger">{{ $t('mockServices.error_count', { count: svc.error_count }) }}</el-tag>
              </div>
            </div>
            <el-empty v-if="!loadingServices && !services.length" :description="$t('mockServices.no_services')" :image-size="48" />
          </div>
        </el-card>

        <div class="service-detail" v-if="selectedService">
          <el-card>
            <template #header>
              <div class="flex items-center justify-between gap-12">
                <div>
                  <span class="card-title">{{ selectedService.name }}</span>
                  <div class="text-3 mono detail-url">{{ publicUrl(selectedService) }}</div>
                </div>
                <div class="flex items-center gap-8">
                  <el-button type="primary" size="small" @click="openManualExecute()">{{ $t('mockServices.manual_execute') }}</el-button>
                  <el-button size="small" @click="copyUrl(selectedService)">{{ $t('mockServices.copy_url') }}</el-button>
                  <el-button size="small" @click="rotateServiceKey(selectedService)">{{ $t('mockServices.rotate_key') }}</el-button>
                  <el-button size="small" @click="openServiceDialog(selectedService)">{{ $t('common.edit') }}</el-button>
                  <el-button size="small" type="danger" @click="deleteService(selectedService)">✕</el-button>
                </div>
              </div>
            </template>
            <div class="service-kpis">
              <div>
                <span class="kpi-label">{{ $t('mockServices.slug') }}</span>
                <span class="mono">{{ selectedService.slug }}</span>
              </div>
              <div>
                <span class="kpi-label">{{ $t('mockServices.access_key') }}</span>
                <span class="mono">{{ selectedService.access_key ? maskKey(selectedService.access_key) : $t('common.none') }}</span>
              </div>
              <div>
                <span class="kpi-label">{{ $t('mockServices.call_count') }}</span>
                <span>{{ stats.call_count || 0 }} / {{ stats.error_count || 0 }} {{ $t('common.failed') }}</span>
              </div>
              <div>
                <span class="kpi-label">{{ $t('mockServices.route_status') }}</span>
                <span>{{ stats.route_count || routes.length }} / {{ stats.disabled_route_count || 0 }} {{ $t('common.disabled') }}</span>
              </div>
            </div>
          </el-card>

          <el-tabs v-model="activeTab" style="margin-top:12px">
            <el-tab-pane :label="$t('mockServices.tab_routes')" name="routes" />
            <el-tab-pane :label="$t('mockServices.tab_logs')" name="logs" />
            <el-tab-pane :label="$t('mockServices.tab_traffic')" name="traffic" />
            <el-tab-pane :label="$t('mockServices.tab_sources')" name="sources" />
            <el-tab-pane :label="$t('mockServices.tab_rules')" name="rules" />
          </el-tabs>

          <el-card v-show="activeTab === 'routes'">
            <template #header>
              <div class="flex items-center justify-between">
                <span class="card-title">{{ $t('mockServices.routes') }}</span>
                <div class="flex items-center gap-8">
                  <el-input v-model="routeKeyword" size="small" clearable :placeholder="$t('common.search')" style="width:220px" />
                  <el-button type="primary" size="small" @click="openRouteDialog()">{{ $t('mockServices.new_route') }}</el-button>
                </div>
              </div>
            </template>
            <el-table :data="filteredRoutes" size="small" :empty-text="$t('mockServices.no_routes')">
              <el-table-column :label="$t('common.status')" width="96">
                <template #default="{ row }">
                  <el-switch v-model="row.enabled" size="small" @change="toggleRoute(row)" />
                </template>
              </el-table-column>
              <el-table-column :label="$t('mockServices.priority')" prop="priority" width="80" />
              <el-table-column label="Method" prop="method" width="90" />
              <el-table-column :label="$t('mockServices.path')" prop="path" min-width="180" />
              <el-table-column :label="$t('mockServices.conditions')" min-width="180">
                <template #default="{ row }">{{ conditionSummary(row.match?.conditions || []) }}</template>
              </el-table-column>
              <el-table-column :label="$t('mockServices.status_code')" width="100">
                <template #default="{ row }">{{ row.response?.status_code || 200 }}</template>
              </el-table-column>
              <el-table-column :label="$t('common.actions')" width="230">
                <template #default="{ row }">
                  <el-button size="small" @click="useRouteAsTest(row)">{{ $t('mockServices.execute_route') }}</el-button>
                  <el-button size="small" @click="duplicateRoute(row)">{{ $t('mockServices.duplicate') }}</el-button>
                  <el-button size="small" @click="openRouteDialog(row)">{{ $t('common.edit') }}</el-button>
                  <el-button size="small" type="danger" @click="deleteRoute(row)">✕</el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-card>

          <el-card v-show="activeTab === 'logs'">
            <template #header>
              <div class="flex items-center justify-between">
                <span class="card-title">{{ $t('mockServices.call_logs') }}</span>
                <div class="flex items-center gap-8">
                  <el-select v-model="logFilters.matched" size="small" style="width:130px" @change="loadLogs">
                    <el-option :label="$t('common.all')" value="" />
                    <el-option :label="$t('mockServices.matched')" value="true" />
                    <el-option :label="$t('mockServices.unmatched')" value="false" />
                  </el-select>
                  <el-input v-model="logFilters.status_code" size="small" clearable :placeholder="$t('mockServices.status_code')" style="width:120px" @keyup.enter="loadLogs" />
                  <el-button size="small" @click="loadLogs">{{ $t('common.refresh') }}</el-button>
                </div>
              </div>
            </template>
            <el-table :data="logs" size="small" :empty-text="$t('common.no_data')" max-height="420" @row-click="openLogDetail">
              <el-table-column label="Method" prop="method" width="90" />
              <el-table-column :label="$t('mockServices.path')" prop="path" min-width="180" />
              <el-table-column :label="$t('mockServices.matched')" width="90">
                <template #default="{ row }">
                  <el-tag :type="row.matched ? 'success' : 'info'" size="small">{{ row.matched ? $t('common.yes') : $t('common.no') }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column :label="$t('mockServices.matched_case')" min-width="130">
                <template #default="{ row }">
                  <span class="text-2">{{ row.case_name || row.response_summary?.matched_case || '—' }}</span>
                </template>
              </el-table-column>
              <el-table-column label="SQL" min-width="130">
                <template #default="{ row }">
                  <el-tag v-for="ref in (row.sql_refs || []).slice(0, 2)" :key="ref" size="small" type="warning" effect="plain" style="margin-right:4px">{{ ref }}</el-tag>
                  <span v-if="!(row.sql_refs || []).length" class="text-3">—</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('mockServices.status_code')" prop="status_code" width="100" />
              <el-table-column :label="$t('common.duration')" width="100">
                <template #default="{ row }">{{ row.duration_ms }}ms</template>
              </el-table-column>
              <el-table-column :label="$t('common.time')" width="150">
                <template #default="{ row }">{{ fmt.fromNow(row.created_at) }}</template>
              </el-table-column>
            </el-table>
          </el-card>

          <el-card v-show="activeTab === 'traffic'">
            <template #header>
              <div class="flex items-center justify-between">
                <span class="card-title">{{ $t('mockServices.traffic_records') }}</span>
                <div class="flex items-center gap-8">
                  <el-input v-model="trafficFilters.path" size="small" clearable :placeholder="$t('mockServices.path')" style="width:180px" @keyup.enter="loadTrafficRecords" />
                  <el-button size="small" @click="loadTrafficRecords">{{ $t('common.refresh') }}</el-button>
                </div>
              </div>
            </template>
            <el-table :data="trafficRecords" size="small" :empty-text="$t('common.no_data')" max-height="420">
              <el-table-column label="Method" prop="method" width="90" />
              <el-table-column :label="$t('mockServices.path')" prop="path" min-width="180" />
              <el-table-column :label="$t('mockServices.source')" prop="source_type" width="120" />
              <el-table-column :label="$t('mockServices.status_code')" width="100">
                <template #default="{ row }">{{ row.response?.status_code || '-' }}</template>
              </el-table-column>
              <el-table-column :label="$t('common.time')" width="150">
                <template #default="{ row }">{{ fmt.fromNow(row.created_at) }}</template>
              </el-table-column>
              <el-table-column :label="$t('common.actions')" width="180">
                <template #default="{ row }">
                  <el-button size="small" @click="createRouteFromTraffic(row)">{{ $t('mockServices.create_route') }}</el-button>
                  <el-button size="small" @click="previewTraffic(row)">{{ $t('mockServices.preview') }}</el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-card>

          <el-card v-show="activeTab === 'sources'">
            <template #header>
              <div class="flex items-center justify-between">
                <span class="card-title">{{ $t('mockServices.traffic_sources') }}</span>
                <el-button type="primary" size="small" @click="openSourceDialog()">{{ $t('mockServices.new_source') }}</el-button>
              </div>
            </template>
            <el-table :data="sources" size="small" :empty-text="$t('common.no_data')">
              <el-table-column :label="$t('common.name')" prop="name" min-width="160" />
              <el-table-column :label="$t('common.type')" prop="type" width="130" />
              <el-table-column :label="$t('mockServices.record_mode')" prop="record_mode" width="150" />
              <el-table-column :label="$t('mockServices.access_key')" min-width="180">
                <template #default="{ row }">{{ maskKey(row.access_key) }}</template>
              </el-table-column>
              <el-table-column :label="$t('common.actions')" width="260">
                <template #default="{ row }">
                  <el-button size="small" @click="copyMitmCommand(row)">{{ $t('mockServices.copy_mitm') }}</el-button>
                  <el-button size="small" @click="rotateSourceKey(row)">{{ $t('mockServices.rotate_key') }}</el-button>
                  <el-button size="small" @click="openSourceDialog(row)">{{ $t('common.edit') }}</el-button>
                  <el-button size="small" type="danger" @click="deleteSource(row)">✕</el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-card>

          <el-card v-show="activeTab === 'rules'">
            <template #header>
              <div class="flex items-center justify-between">
                <span class="card-title">{{ $t('mockServices.proxy_rules') }}</span>
                <el-button type="primary" size="small" @click="openRuleDialog()">{{ $t('mockServices.new_rule') }}</el-button>
              </div>
            </template>
            <el-table :data="rules" size="small" :empty-text="$t('common.no_data')">
              <el-table-column :label="$t('common.status')" width="90">
                <template #default="{ row }">
                  <el-tag size="small" :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? $t('common.enabled') : $t('common.disabled') }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column :label="$t('common.name')" prop="name" min-width="160" />
              <el-table-column :label="$t('mockServices.action')" prop="action" width="150" />
              <el-table-column :label="$t('mockServices.conditions')" min-width="220">
                <template #default="{ row }">{{ conditionSummary(row.conditions || []) }}</template>
              </el-table-column>
              <el-table-column :label="$t('common.actions')" width="230">
                <template #default="{ row }">
                  <el-button size="small" @click="openRuleTest(row)">{{ $t('mockServices.test_rule') }}</el-button>
                  <el-button size="small" @click="openRuleDialog(row)">{{ $t('common.edit') }}</el-button>
                  <el-button size="small" type="danger" @click="deleteRule(row)">✕</el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </div>

        <el-card v-else class="empty-detail">
          <el-empty :description="$t('mockServices.select_service_hint')" :image-size="64" />
        </el-card>
      </div>
    </div>

    <el-dialog v-model="serviceDialog.visible" :title="serviceDialog.id ? $t('mockServices.edit_service') : $t('mockServices.new_service')" width="720px">
      <el-form label-position="top">
        <div class="form-grid">
          <el-form-item :label="$t('common.name')"><el-input v-model="serviceForm.name" /></el-form-item>
          <el-form-item :label="$t('mockServices.slug')"><el-input v-model="serviceForm.slug" /></el-form-item>
        </div>
        <el-form-item :label="$t('common.description')"><el-input v-model="serviceForm.description" type="textarea" :rows="2" /></el-form-item>
        <div class="form-grid">
          <el-form-item :label="$t('common.enabled')"><el-switch v-model="serviceForm.enabled" /></el-form-item>
          <el-form-item :label="$t('mockServices.public_enabled')"><el-switch v-model="serviceForm.public_enabled" /></el-form-item>
        </div>
        <el-form-item :label="$t('mockServices.access_key')">
          <el-input v-model="serviceForm.access_key" :placeholder="$t('mockServices.access_key_required')">
            <template #append><el-button @click="serviceForm.access_key = randomKey()">{{ $t('mockServices.generate_key') }}</el-button></template>
          </el-input>
        </el-form-item>
        <el-form-item :label="$t('mockServices.default_response')">
          <el-input v-model="serviceForm.defaultResponseJson" type="textarea" :rows="5" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="serviceDialog.visible = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" @click="saveService">{{ $t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="routeDialog.visible" :title="routeDialog.id ? $t('mockServices.edit_route') : $t('mockServices.new_route')" width="860px">
      <el-form label-position="top">
        <div class="form-grid">
          <el-form-item :label="$t('common.name')"><el-input v-model="routeForm.name" /></el-form-item>
          <el-form-item :label="$t('mockServices.priority')"><el-input-number v-model="routeForm.priority" :min="1" style="width:100%" /></el-form-item>
        </div>
        <div class="form-grid">
          <el-form-item label="Method">
            <el-select v-model="routeForm.method" style="width:100%"><el-option v-for="m in ['ANY', ...methods]" :key="m" :label="m" :value="m" /></el-select>
          </el-form-item>
          <el-form-item :label="$t('mockServices.path')"><el-input v-model="routeForm.path" /></el-form-item>
        </div>
        <el-form-item :label="$t('common.enabled')"><el-switch v-model="routeForm.enabled" /></el-form-item>
        <condition-editor v-model="routeForm.conditions" :t="t" />
        <div class="form-grid">
          <el-form-item :label="$t('mockServices.status_code')"><el-input-number v-model="routeForm.status_code" :min="100" :max="599" style="width:100%" /></el-form-item>
          <el-form-item :label="$t('mockServices.latency_ms')"><el-input-number v-model="routeForm.latency_ms" :min="0" style="width:100%" /></el-form-item>
        </div>
        <div class="form-grid">
          <el-form-item :label="$t('mockServices.body_type')">
            <el-select v-model="routeForm.body_type" style="width:100%">
              <el-option label="JSON" value="json" /><el-option label="Text" value="text" /><el-option label="Empty" value="empty" />
            </el-select>
          </el-form-item>
          <el-form-item :label="$t('mockServices.headers_json')"><el-input v-model="routeForm.headersJson" /></el-form-item>
        </div>
        <el-form-item :label="$t('mockServices.response_body')"><el-input v-model="routeForm.bodyText" type="textarea" :rows="7" /></el-form-item>
        <el-form-item :label="$t('mockServices.body_template')"><el-input v-model="routeForm.body_template" type="textarea" :rows="4" :placeholder="$t('mockServices.template_hint')" /></el-form-item>
        <el-form-item :label="$t('mockServices.response_cases')">
          <el-input
            v-model="routeForm.responseCasesJson"
            type="textarea"
            :rows="8"
            :placeholder="$t('mockServices.response_cases_placeholder')"
          />
          <span class="text-3 hint-line">{{ $t('mockServices.response_cases_hint') }}</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="routeDialog.visible = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" @click="saveRoute">{{ $t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="sourceDialog.visible" :title="sourceDialog.id ? $t('mockServices.edit_source') : $t('mockServices.new_source')" width="680px">
      <el-form label-position="top">
        <div class="form-grid">
          <el-form-item :label="$t('common.name')"><el-input v-model="sourceForm.name" /></el-form-item>
          <el-form-item :label="$t('common.type')">
            <el-select v-model="sourceForm.type" style="width:100%"><el-option v-for="item in sourceTypes" :key="item" :label="item" :value="item" /></el-select>
          </el-form-item>
        </div>
        <div class="form-grid">
          <el-form-item :label="$t('mockServices.record_mode')">
            <el-select v-model="sourceForm.record_mode" style="width:100%"><el-option v-for="item in recordModes" :key="item" :label="item" :value="item" /></el-select>
          </el-form-item>
          <el-form-item :label="$t('common.enabled')"><el-switch v-model="sourceForm.enabled" /></el-form-item>
        </div>
        <el-form-item :label="$t('mockServices.access_key')">
          <el-input v-model="sourceForm.access_key"><template #append><el-button @click="sourceForm.access_key = randomKey()">{{ $t('mockServices.generate_key') }}</el-button></template></el-input>
        </el-form-item>
        <div class="form-grid">
          <el-form-item :label="$t('mockServices.filter_host')"><el-input v-model="sourceForm.filter_host" /></el-form-item>
          <el-form-item :label="$t('mockServices.filter_url')"><el-input v-model="sourceForm.filter_url" /></el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="sourceDialog.visible = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" @click="saveSource">{{ $t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="ruleDialog.visible" :title="ruleDialog.id ? $t('mockServices.edit_rule') : $t('mockServices.new_rule')" width="860px">
      <el-form label-position="top">
        <div class="form-grid">
          <el-form-item :label="$t('common.name')"><el-input v-model="ruleForm.name" /></el-form-item>
          <el-form-item :label="$t('mockServices.priority')"><el-input-number v-model="ruleForm.priority" :min="1" style="width:100%" /></el-form-item>
        </div>
        <div class="form-grid">
          <el-form-item :label="$t('mockServices.source')">
            <el-select v-model="ruleForm.source_id" clearable style="width:100%"><el-option v-for="src in sources" :key="src.id" :label="src.name" :value="src.id" /></el-select>
          </el-form-item>
          <el-form-item :label="$t('mockServices.action')">
            <el-select v-model="ruleForm.action" style="width:100%"><el-option v-for="a in actions" :key="a" :label="a" :value="a" /></el-select>
          </el-form-item>
        </div>
        <el-form-item :label="$t('common.enabled')"><el-switch v-model="ruleForm.enabled" /></el-form-item>
        <condition-editor v-model="ruleForm.conditions" :t="t" />
        <patch-editor v-model="ruleForm.patch_list" :t="t" />
        <el-form-item :label="$t('mockServices.record')"><el-switch v-model="ruleForm.record" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="ruleDialog.visible = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" @click="saveRule">{{ $t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="logDialog.visible" :title="$t('mockServices.log_detail')" width="760px">
      <pre class="code-block">{{ jsonPretty(logDialog.row || {}) }}</pre>
      <template #footer>
        <el-button @click="replayLog(logDialog.row)">{{ $t('mockServices.replay') }}</el-button>
        <el-button @click="logDialog.visible = false">{{ $t('common.cancel') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="previewDialog.visible" :title="previewDialog.title" width="760px">
      <pre class="code-block">{{ jsonPretty(previewDialog.data) }}</pre>
    </el-dialog>

    <el-dialog v-model="ruleTestDialog.visible" :title="$t('mockServices.test_rule')" width="760px">
      <el-input v-model="ruleTestDialog.sampleJson" type="textarea" :rows="8" />
      <div style="margin-top:12px"><el-button type="primary" @click="runRuleTest">{{ $t('mockServices.run_test') }}</el-button></div>
      <pre v-if="ruleTestDialog.result" class="code-block">{{ jsonPretty(ruleTestDialog.result) }}</pre>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, defineComponent, h, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElButton, ElInput, ElMessageBox, ElOption, ElSelect, ElTable, ElTableColumn } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { apiApi, mockServiceApi, trafficApi } from '@/api'
import { useProjectStore, useToastStore } from '@/stores'
import { copyText, fmt, jsonPretty } from '@/utils'

const ConditionEditor = defineComponent({
  props: { modelValue: { type: Array, default: () => [] }, t: { type: Function, required: true } },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    const update = (idx, key, value) => {
      const next = [...props.modelValue]
      next[idx] = { ...next[idx], [key]: value }
      emit('update:modelValue', next)
    }
    const remove = idx => emit('update:modelValue', props.modelValue.filter((_, i) => i !== idx))
    const add = () => emit('update:modelValue', [...props.modelValue, { target: 'query', key: '', operator: 'equals', value: '' }])
    return () => h('div', { class: 'mini-editor' }, [
      h('div', { class: 'mini-editor-title' }, [
        h('span', props.t('mockServices.conditions')),
        h(ElButton, { size: 'small', onClick: add }, () => props.t('common.new')),
      ]),
      h(ElTable, { data: props.modelValue, size: 'small' }, () => [
        h(ElTableColumn, { label: props.t('mockServices.target'), width: 140 }, { default: ({ row, $index }) => h(ElSelect, { modelValue: row.target, 'onUpdate:modelValue': v => update($index, 'target', v), size: 'small' }, () => ['query','header','cookie','body_field','jsonpath','method','host','path','status_code'].map(v => h(ElOption, { label: v, value: v, key: v }))) }),
        h(ElTableColumn, { label: props.t('mockServices.field'), minWidth: 140 }, { default: ({ row, $index }) => h(ElInput, { modelValue: row.key, 'onUpdate:modelValue': v => update($index, 'key', v), size: 'small' }) }),
        h(ElTableColumn, { label: props.t('mockServices.operator'), width: 130 }, { default: ({ row, $index }) => h(ElSelect, { modelValue: row.operator || 'equals', 'onUpdate:modelValue': v => update($index, 'operator', v), size: 'small' }, () => ['equals','contains','regex','exists','in','gt','gte','lt','lte'].map(v => h(ElOption, { label: v, value: v, key: v }))) }),
        h(ElTableColumn, { label: props.t('mockServices.expected'), minWidth: 160 }, { default: ({ row, $index }) => h(ElInput, { modelValue: row.value, 'onUpdate:modelValue': v => update($index, 'value', v), size: 'small' }) }),
        h(ElTableColumn, { label: props.t('common.actions'), width: 90 }, { default: ({ $index }) => h(ElButton, { size: 'small', type: 'danger', onClick: () => remove($index) }, () => props.t('common.delete')) }),
      ]),
    ])
  },
})

const PatchEditor = defineComponent({
  props: { modelValue: { type: Array, default: () => [] }, t: { type: Function, required: true } },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    const update = (idx, key, value) => {
      const next = [...props.modelValue]
      next[idx] = { ...next[idx], [key]: value }
      emit('update:modelValue', next)
    }
    const remove = idx => emit('update:modelValue', props.modelValue.filter((_, i) => i !== idx))
    const add = () => emit('update:modelValue', [...props.modelValue, { target: 'header', key: '', value: '' }])
    return () => h('div', { class: 'mini-editor' }, [
      h('div', { class: 'mini-editor-title' }, [
        h('span', props.t('mockServices.patches')),
        h(ElButton, { size: 'small', onClick: add }, () => props.t('common.new')),
      ]),
      h(ElTable, { data: props.modelValue, size: 'small' }, () => [
        h(ElTableColumn, { label: props.t('mockServices.target'), width: 160 }, { default: ({ row, $index }) => h(ElSelect, { modelValue: row.target, 'onUpdate:modelValue': v => update($index, 'target', v), size: 'small' }, () => ['header','query','body_jsonpath','body','status_code','response_body'].map(v => h(ElOption, { label: v, value: v, key: v }))) }),
        h(ElTableColumn, { label: props.t('mockServices.field'), minWidth: 140 }, { default: ({ row, $index }) => h(ElInput, { modelValue: row.key, 'onUpdate:modelValue': v => update($index, 'key', v), size: 'small' }) }),
        h(ElTableColumn, { label: props.t('mockServices.value'), minWidth: 180 }, { default: ({ row, $index }) => h(ElInput, { modelValue: row.value, 'onUpdate:modelValue': v => update($index, 'value', v), size: 'small' }) }),
        h(ElTableColumn, { label: props.t('common.actions'), width: 90 }, { default: ({ $index }) => h(ElButton, { size: 'small', type: 'danger', onClick: () => remove($index) }, () => props.t('common.delete')) }),
      ]),
    ])
  },
})

const { t } = useI18n()
const toast = useToastStore()
const projectStore = useProjectStore()
const route = useRoute()
const router = useRouter()

const methods = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']
const sourceTypes = ['mitmproxy', 'service_forward', 'app_capture']
const recordModes = ['record', 'asset', 'asset_and_mock']
const actions = ['pass_through', 'record', 'drop', 'modify_request', 'modify_response', 'mock_response']
const services = ref([])
const routes = ref([])
const logs = ref([])
const trafficRecords = ref([])
const sources = ref([])
const rules = ref([])
const stats = ref({})
const selectedService = ref(null)
const loadingServices = ref(false)
const activeTab = ref('routes')
const testing = ref(false)
const testResult = ref(null)
const routeKeyword = ref('')
const serviceDialog = ref({ visible: false, id: '' })
const routeDialog = ref({ visible: false, id: '' })
const sourceDialog = ref({ visible: false, id: '' })
const ruleDialog = ref({ visible: false, id: '' })
const logDialog = ref({ visible: false, row: null })
const previewDialog = ref({ visible: false, title: '', data: null })
const ruleTestDialog = ref({ visible: false, rule: null, sampleJson: '{\n  "method": "GET",\n  "url": "https://example.com/api/users",\n  "path": "/api/users",\n  "query": {},\n  "headers": {},\n  "body": {}\n}', result: null })
const serviceForm = ref(defaultServiceForm())
const routeForm = ref(defaultRouteForm())
const sourceForm = ref(defaultSourceForm())
const ruleForm = ref(defaultRuleForm())
const testForm = ref({ method: 'GET', path: '/', queryJson: '{}', headersJson: '{}', bodyText: '{}' })
const logFilters = ref({ matched: '', status_code: '' })
const trafficFilters = ref({ path: '' })

const filteredRoutes = computed(() => {
  const kw = routeKeyword.value.trim().toLowerCase()
  if (!kw) return routes.value
  return routes.value.filter(r => `${r.method} ${r.path} ${r.name}`.toLowerCase().includes(kw))
})

function defaultServiceForm() {
  return { name: '', slug: '', description: '', enabled: true, public_enabled: false, access_key: randomKey(), defaultResponseJson: '{\n  "status_code": 404,\n  "headers": {"content-type": "application/json"},\n  "body_type": "json",\n  "body": {"message": "mock route not matched"},\n  "latency_ms": 0\n}' }
}
function defaultRouteForm() {
  return { name: '', enabled: true, priority: 100, method: 'GET', path: '/', conditions: [], status_code: 200, latency_ms: 0, body_type: 'json', headersJson: '{"content-type":"application/json"}', bodyText: '{\n  "message": "mock response"\n}', body_template: '', responseCasesJson: '[]' }
}
function defaultSourceForm() {
  return { name: '', type: 'mitmproxy', enabled: true, access_key: randomKey(), record_mode: 'asset', filter_host: '', filter_url: '' }
}
function defaultRuleForm() {
  return { name: '', enabled: true, priority: 100, source_id: '', direction: 'both', action: 'pass_through', record: true, conditions: [], patch_list: [] }
}
function randomKey() {
  return Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2)
}
function parseJson(text, fallback = {}) {
  if (!text?.trim()) return fallback
  return JSON.parse(text)
}
function parseMaybeJson(text, bodyType = 'json') {
  if (bodyType === 'empty') return null
  if (bodyType === 'json') return parseJson(text, {})
  return text || ''
}
function maskKey(key) {
  if (!key) return t('common.none')
  return `${key.slice(0, 6)}...${key.slice(-4)}`
}
function publicUrl(svc, absolute = true) {
  const path = `/mock-api/${svc.slug}`
  return absolute ? `${window.location.origin}${path}` : path
}
function conditionSummary(conditions = []) {
  if (!conditions.length) return t('mockServices.no_conditions')
  return conditions.map(c => `${c.target}${c.key ? '.' + c.key : ''} ${c.operator || 'equals'} ${c.value ?? ''}`).join('; ')
}
function executablePath(path = '/') {
  const cleaned = String(path || '/').replace('*', '').trim()
  return cleaned && cleaned !== '/' ? (cleaned.startsWith('/') ? cleaned : `/${cleaned}`) : '/'
}
function openManualExecute() {
  testForm.value.path = executablePath(testForm.value.path)
  runTest()
}

async function loadServices() {
  loadingServices.value = true
  try {
    const res = await mockServiceApi.list(projectStore.current)
    services.value = res.items || []
    const routeId = route.params.id
    const next = services.value.find(s => s.id === routeId) || services.value.find(s => s.id === selectedService.value?.id) || services.value[0] || null
    if (next) await selectService(next, false)
    else selectedService.value = null
  } catch (e) {
    toast.error(e.message || t('mockServices.load_failed'))
  } finally {
    loadingServices.value = false
  }
}
async function loadRoutes() {
  if (!selectedService.value) return
  const res = await mockServiceApi.routes(selectedService.value.id)
  routes.value = res.items || []
}
async function loadLogs() {
  if (!selectedService.value) return
  const params = { limit: 100 }
  if (logFilters.value.matched) params.matched = logFilters.value.matched === 'true'
  if (logFilters.value.status_code) params.status_code = Number(logFilters.value.status_code)
  const res = await mockServiceApi.logs(selectedService.value.id, params)
  logs.value = res.items || []
}
async function loadTrafficRecords() {
  const res = await trafficApi.records({ project_id: projectStore.current, limit: 100, path: trafficFilters.value.path || undefined })
  trafficRecords.value = res.items || []
}
async function loadSources() {
  const res = await trafficApi.sources({ project_id: projectStore.current })
  sources.value = res.items || []
}
async function loadRules() {
  const res = await trafficApi.rules({ project_id: projectStore.current })
  rules.value = res.items || []
}
async function loadStats() {
  if (!selectedService.value) return
  stats.value = await mockServiceApi.stats(selectedService.value.id)
}
async function loadAll() {
  await loadServices()
  await Promise.all([loadRoutes(), loadLogs(), loadTrafficRecords(), loadSources(), loadRules(), loadStats()])
}
async function selectService(svc, push = false) {
  selectedService.value = svc
  testResult.value = null
  if (push && route.params.id !== svc.id) router.replace(`/mock-services/${svc.id}`)
  await Promise.all([loadRoutes(), loadLogs(), loadTrafficRecords(), loadSources(), loadRules(), loadStats()])
}
function openServiceDialog(svc = null) {
  serviceDialog.value = { visible: true, id: svc?.id || '' }
  serviceForm.value = svc ? {
    name: svc.name, slug: svc.slug, description: svc.description || '', enabled: !!svc.enabled, public_enabled: !!svc.public_enabled,
    access_key: svc.access_key || randomKey(), defaultResponseJson: JSON.stringify(svc.default_response || parseJson(defaultServiceForm().defaultResponseJson), null, 2),
  } : defaultServiceForm()
}
async function saveService() {
  if (serviceForm.value.public_enabled && !serviceForm.value.access_key) {
    toast.error(t('mockServices.access_key_required'))
    return
  }
  let default_response
  try { default_response = parseJson(serviceForm.value.defaultResponseJson, {}) } catch { toast.error(t('mockServices.json_error')); return }
  const payload = { ...serviceForm.value, default_response, project_id: projectStore.current }
  delete payload.defaultResponseJson
  const saved = serviceDialog.value.id ? await mockServiceApi.update(serviceDialog.value.id, payload) : await mockServiceApi.create(payload)
  serviceDialog.value.visible = false
  toast.success(t('mockServices.saved'))
  await loadServices()
  await selectService(saved, true)
}
async function deleteService(svc) {
  await ElMessageBox.confirm(t('mockServices.confirm_delete_service', { name: svc.name }), t('common.confirm'), { type: 'warning' })
  await mockServiceApi.remove(svc.id)
  toast.success(t('mockServices.deleted'))
  selectedService.value = null
  await router.replace('/mock-services')
  await loadServices()
}
function openRouteDialog(row = null) {
  routeDialog.value = { visible: true, id: row?.id || '' }
  if (!row) { routeForm.value = defaultRouteForm(); return }
  const bodyType = row.response?.body_type || (typeof row.response?.body === 'object' ? 'json' : 'text')
  routeForm.value = {
    name: row.name || '', enabled: row.enabled !== false, priority: row.priority || 100, method: row.method || 'GET', path: row.path || '/',
    conditions: row.match?.conditions || [], status_code: row.response?.status_code || 200, latency_ms: row.response?.latency_ms || 0,
    body_type: bodyType, headersJson: JSON.stringify(row.response?.headers || { 'content-type': bodyType === 'json' ? 'application/json' : 'text/plain' }),
    bodyText: bodyType === 'json' ? JSON.stringify(row.response?.body ?? { message: 'mock response' }, null, 2) : String(row.response?.body ?? ''),
    body_template: row.response?.body_template || '',
    responseCasesJson: JSON.stringify(row.response_cases || [], null, 2),
  }
}
function routePayloadFromForm() {
  const headers = parseJson(routeForm.value.headersJson, {})
  const response_cases = parseJson(routeForm.value.responseCasesJson, [])
  return {
    name: routeForm.value.name, enabled: routeForm.value.enabled, priority: routeForm.value.priority, method: routeForm.value.method, path: routeForm.value.path,
    match: { conditions: routeForm.value.conditions },
    response: { status_code: routeForm.value.status_code, headers, body_type: routeForm.value.body_type, body: parseMaybeJson(routeForm.value.bodyText, routeForm.value.body_type), body_template: routeForm.value.body_template, latency_ms: routeForm.value.latency_ms },
    response_cases,
  }
}
async function saveRoute() {
  if (!selectedService.value) return
  let payload
  try { payload = routePayloadFromForm() } catch { toast.error(t('mockServices.json_error')); return }
  if (routeDialog.value.id) await mockServiceApi.updateRoute(selectedService.value.id, routeDialog.value.id, payload)
  else await mockServiceApi.createRoute(selectedService.value.id, payload)
  routeDialog.value.visible = false
  toast.success(t('mockServices.saved'))
  await Promise.all([loadRoutes(), loadStats()])
}
async function toggleRoute(row) {
  await mockServiceApi.updateRoute(selectedService.value.id, row.id, row)
  await loadRoutes()
}
async function duplicateRoute(row) {
  const copy = { ...row, name: `${row.name || row.path} Copy`, priority: (row.priority || 100) + 1 }
  delete copy.id
  await mockServiceApi.createRoute(selectedService.value.id, copy)
  toast.success(t('mockServices.saved'))
  await loadRoutes()
}
async function deleteRoute(row) {
  await mockServiceApi.removeRoute(selectedService.value.id, row.id)
  toast.success(t('mockServices.deleted'))
  await Promise.all([loadRoutes(), loadStats()])
}
function useRouteAsTest(row) {
  testForm.value = { method: row.method === 'ANY' ? 'GET' : row.method, path: executablePath(row.path), queryJson: '{}', headersJson: '{}', bodyText: '{}' }
  // 直接执行该路由，调用 mock 代理地址并显示返回
  runTest()
}
async function runTest() {
  if (!selectedService.value) return
  testing.value = true
  testResult.value = null
  try {
    // 简化格式：/mock-api/{service_slug}/{path}，mock_key 可选
    const base = `${window.location.origin}/mock-api/${selectedService.value.slug}`
    let path = testForm.value.path.startsWith('/') ? testForm.value.path : `/${testForm.value.path}`
    const queryObj = parseJson(testForm.value.queryJson, {})
    const queryString = new URLSearchParams(queryObj).toString()
    const mockKey = selectedService.value.access_key || ''
    const url = `${base}${path}${queryString ? `?${queryString}` : ''}`

    const headers = { ...parseJson(testForm.value.headersJson, {}), 'X-Mock-Key': mockKey }
    const noBodyMethods = ['GET', 'HEAD']
    const body = !noBodyMethods.includes(testForm.value.method.toUpperCase()) && testForm.value.bodyText
      ? testForm.value.bodyText
      : undefined

    const start = performance.now()
    const res = await fetch(url, { method: testForm.value.method, headers, body })
    const duration_ms = Math.round(performance.now() - start)

    const responseHeaders = {}
    res.headers.forEach((val, key) => { responseHeaders[key] = val })

    let responseBody = ''
    const contentType = res.headers.get('content-type') || ''
    if (contentType.includes('application/json')) {
      responseBody = JSON.stringify(await res.json(), null, 2)
    } else {
      responseBody = await res.text()
    }

    testResult.value = {
      status: res.status,
      statusText: res.statusText,
      responseHeaders,
      body: responseBody,
      duration_ms,
    }
    // 执行完成后 toast 提示并跳转到调用日志 tab
    toast.success(`[${testForm.value.method}] ${path} → ${res.status} (${duration_ms}ms)`)
    activeTab.value = 'logs'
    await loadLogs()
  } catch (e) { toast.error(e.message || t('mockServices.test_failed')) } finally { testing.value = false }
}
async function copyCurl() {
  const base = `${window.location.origin}/mock-api/${selectedService.value.slug}`
  const path = testForm.value.path.startsWith('/') ? testForm.value.path : `/${testForm.value.path}`
  const queryObj = parseJson(testForm.value.queryJson, {})
  const queryString = new URLSearchParams(queryObj).toString()
  const mockKey = selectedService.value.access_key || ''
  const url = `${base}${path}${queryString ? `?${queryString}` : ''}`
  await copyText(`curl -X ${testForm.value.method} '${url}' -H 'X-Mock-Key: ${mockKey}' -d '${testForm.value.bodyText || ''}'`)
  toast.success(t('factory.toast_copied'))
}
function openLogDetail(row) { logDialog.value = { visible: true, row } }
function replayLog(row) {
  if (!row) return
  logDialog.value.visible = false
  testForm.value = { method: row.method, path: row.path, queryJson: JSON.stringify(row.request_summary?.query || {}, null, 2), headersJson: JSON.stringify(row.request_summary?.headers || {}, null, 2), bodyText: JSON.stringify(row.request_summary?.body || {}, null, 2) }
  runTest()
}
async function createRouteFromTraffic(row) {
  await mockServiceApi.fromTraffic(selectedService.value.id, { record_id: row.id, enabled: false })
  toast.success(t('mockServices.route_draft_created'))
  await loadRoutes()
  activeTab.value = 'routes'
}
function previewTraffic(row) { previewDialog.value = { visible: true, title: t('mockServices.traffic_detail'), data: row } }
async function openRouteFromApi(apiId) {
  const api = await apiApi.get(apiId)
  if (!selectedService.value) {
    openServiceDialog({ name: api.name || 'API Mock', slug: '', description: api.doc?.summary || '', enabled: true, public_enabled: false, access_key: randomKey(), default_response: parseJson(defaultServiceForm().defaultResponseJson) })
    toast.info(t('mockServices.create_service_first'))
    return
  }
  await mockServiceApi.importApi(selectedService.value.id, { api_id: apiId })
  toast.success(t('mockServices.route_draft_created'))
  await loadRoutes()
}
async function copyUrl(svc) { await copyText(publicUrl(svc)); toast.success(t('factory.toast_copied')) }
async function rotateServiceKey(svc) {
  const updated = await mockServiceApi.rotateKey(svc.id)
  toast.success(t('mockServices.key_rotated'))
  await loadServices()
  await selectService(updated, false)
}
async function rotateSourceKey(row) { await trafficApi.rotateSourceKey(row.id); toast.success(t('mockServices.key_rotated')); await loadSources() }
async function copyMitmCommand(row) {
  const cmd = `mitmproxy -s mitmproxy_capture/capture_addon.py --set api_pulse_url=${window.location.origin} --set project_id=${projectStore.current} --set source_id=${row.id} --set access_key=${row.access_key}`
  await copyText(cmd)
  toast.success(t('factory.toast_copied'))
}
function openSourceDialog(row = null) {
  sourceDialog.value = { visible: true, id: row?.id || '' }
  sourceForm.value = row ? { ...defaultSourceForm(), ...row } : defaultSourceForm()
}
async function saveSource() {
  const payload = { ...sourceForm.value, project_id: projectStore.current }
  if (sourceDialog.value.id) await trafficApi.updateSource(sourceDialog.value.id, payload)
  else await trafficApi.createSource(payload)
  sourceDialog.value.visible = false
  toast.success(t('mockServices.saved'))
  await loadSources()
}
async function deleteSource(row) {
  await trafficApi.removeSource(row.id)
  toast.success(t('mockServices.deleted'))
  await Promise.all([loadSources(), loadRules()])
}
function openRuleDialog(row = null) {
  ruleDialog.value = { visible: true, id: row?.id || '' }
  ruleForm.value = row ? { ...defaultRuleForm(), ...row, conditions: row.conditions || [], patch_list: row.patch_list || [] } : defaultRuleForm()
}
async function saveRule() {
  const payload = { ...ruleForm.value, project_id: projectStore.current }
  if (ruleDialog.value.id) await trafficApi.updateRule(ruleDialog.value.id, payload)
  else await trafficApi.createRule(payload)
  ruleDialog.value.visible = false
  toast.success(t('mockServices.saved'))
  await loadRules()
}
async function deleteRule(row) {
  await trafficApi.removeRule(row.id)
  toast.success(t('mockServices.deleted'))
  await loadRules()
}
function openRuleTest(row) {
  ruleTestDialog.value.visible = true
  ruleTestDialog.value.rule = row
  ruleTestDialog.value.result = null
}
async function runRuleTest() {
  try {
    ruleTestDialog.value.result = await trafficApi.testRule({ project_id: projectStore.current, rule: ruleTestDialog.value.rule, sample: parseJson(ruleTestDialog.value.sampleJson, {}) })
  } catch (e) { toast.error(e.message || t('mockServices.test_failed')) }
}

watch(() => projectStore.current, async () => {
  selectedService.value = null
  routes.value = []
  logs.value = []
  trafficRecords.value = []
  sources.value = []
  rules.value = []
  await router.replace('/mock-services')
  await loadAll()
})
watch(() => route.params.id, async id => {
  if (!id || selectedService.value?.id === id) return
  const svc = services.value.find(s => s.id === id)
  if (svc) await selectService(svc, false)
})
onMounted(async () => {
  await loadAll()
  if (route.query.api_id) await openRouteFromApi(route.query.api_id)
})
</script>

<style scoped>
.mock-layout { display: grid; grid-template-columns: 320px 1fr; gap: 12px; }
.service-list-card { min-height: 560px; }
.service-list { display: flex; flex-direction: column; gap: 8px; }
.service-item { padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--bg-1); cursor: pointer; }
.service-item.active { border-color: var(--accent); background: var(--bg-2); }
.service-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.service-url,.detail-url { margin-top: 6px; font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.service-meta { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
.service-kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.service-kpis > div { display: flex; flex-direction: column; gap: 4px; padding: 10px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--bg-1); min-width: 0; }
.kpi-label { font-size: 11px; color: var(--text-3); }
.test-grid,.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.test-grid { grid-template-columns: 120px 1fr; }
.result-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; margin-top: 12px; }
.mini-editor { margin: 10px 0 14px; border: 1px solid var(--border); border-radius: var(--radius); padding: 10px; }
.mini-editor-title { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; font-weight: 600; }
.empty-detail { min-height: 560px; display: flex; align-items: center; justify-content: center; }
.code-block { max-height: 420px; overflow: auto; }
.hint-line { display: inline-block; margin-top: 6px; font-size: 11px; }
.case-hit { display: flex; align-items: center; gap: 8px; margin-top: 10px; font-size: 12px; }
@media (max-width: 1180px) {
  .mock-layout,.service-kpis,.form-grid,.result-grid { grid-template-columns: 1fr; }
}
</style>
