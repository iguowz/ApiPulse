<template>
  <div class="page">
    <div class="page-header">
      <div>
        <div class="page-title">{{ $t('settings.title') }}</div>
        <div class="page-subtitle">{{ $t('settings.subtitle') }}</div>
      </div>
    </div>

    <div class="page-body" v-loading="loading">
      <el-tabs v-model="activeTab" @tab-change="onTabChange">
        <!-- Tab 1: 大模型配置 -->
        <el-tab-pane :label="$t('settings.llm')" name="llm">
          <div class="settings-layout">
            <!-- LLM 配置卡片 -->
            <el-card>
              <template #header>
                <div class="flex items-center justify-between">
                  <span>{{ $t('settings.llm_card_title') }}</span>
                  <el-tag :type="configured ? 'success' : 'info'">{{ configured ? $t('settings.llm_configured') : $t('settings.llm_unconfigured') }}</el-tag>
                </div>
              </template>

              <el-form label-position="top">
                <el-form-item :label="$t('settings.llm_provider')">
                  <el-select v-model="form.provider" @change="onProviderChange" style="width:100%">
                    <el-option v-for="p in presets" :key="p.id" :value="p.id" :label="p.name" />
                    <el-option value="custom" :label="$t('settings.llm_custom')" />
                  </el-select>
                </el-form-item>

                <div v-if="form.provider === 'custom'" style="display:flex;gap:12px">
                  <el-form-item :label="$t('settings.llm_base_url')" style="flex:1">
                    <el-input v-model="form.base_url" placeholder="https://api.openai.com/v1" class="mono" />
                  </el-form-item>
                  <el-form-item :label="$t('settings.llm_model_name')" style="flex:1">
                    <el-input v-model="form.model" placeholder="gpt-4o-mini" class="mono" />
                  </el-form-item>
                </div>

                <el-form-item v-else :label="$t('settings.llm_model')">
                  <div style="display:flex;gap:8px;width:100%">
                    <el-select v-model="form.model" style="flex:1;min-width:0">
                      <el-option v-for="m in currentPresetModels" :key="m" :value="m" :label="m" />
                    </el-select>
                    <el-button v-if="isLocalPreset" @click="discoverModels" :loading="discovering" style="flex-shrink:0">
                      {{ discovering ? $t('settings.llm_discovering') : $t('settings.llm_discover') }}
                    </el-button>
                  </div>
                </el-form-item>

                <el-form-item :label="isLocalPreset ? $t('settings.llm_api_key_optional') : $t('settings.llm_api_key')">
                  <div style="display:flex;gap:8px;width:100%">
                    <el-input
                      :type="showKey ? 'text' : 'password'"
                      v-model="form.api_key"
                      placeholder="sk-…"
                      class="mono"
                      style="flex:1;min-width:0"
                    />
                    <el-button @click="showKey = !showKey" style="flex-shrink:0">{{ showKey ? $t('settings.llm_hide_key') : $t('settings.llm_show_key') }}</el-button>
                  </div>
                </el-form-item>

                <div class="form-grid">
                  <el-form-item :label="$t('settings.llm_temperature')">
                    <el-input-number v-model="form.temperature" :min="0" :max="2" :step="0.1" style="width:100%" />
                  </el-form-item>
                  <el-form-item :label="$t('settings.llm_max_tokens')">
                    <el-input-number v-model="form.max_tokens" :min="256" :max="32000" :step="256" style="width:100%" />
                  </el-form-item>
                  <el-form-item :label="$t('settings.llm_stream')">
                    <el-switch v-model="form.stream" />
                  </el-form-item>
                </div>

                <!-- el-divider>{{ $t('settings.llm_task_routes') }}</el-divider -->
                <el-collapse v-model="activeNames" @change="handleChange" style="margin-bottom: 18px;">
                <el-collapse-item :title="$t('settings.llm_task_routes')" name="llm_task_routes">
                <div class="task-route-list">
                  <div v-for="task in llmTaskTypes" :key="task" class="task-route-row">
                    <div class="task-route-head">
                      <el-switch v-model="taskRoutes[task].enabled" />
                      <span class="mono">{{ task }}</span>
                      <span class="text-3">{{ $t('settings.llm_effective_model') }}: {{ taskRoutes[task].effective?.model || form.model }}</span>
                    </div>
                    <div v-if="taskRoutes[task].enabled" class="task-route-grid">
                      <el-select v-model="taskRoutes[task].provider" size="small" :placeholder="$t('settings.llm_inherit')" @change="onTaskProviderChange(task)">
                        <el-option :label="$t('settings.llm_inherit')" value="" />
                        <el-option v-for="p in presets" :key="p.id" :value="p.id" :label="p.name" />
                        <el-option value="custom" :label="$t('settings.llm_custom')" />
                      </el-select>
                      <el-input v-model="taskRoutes[task].base_url" size="small" class="mono" :placeholder="$t('settings.llm_base_url')" />
                      <el-input v-model="taskRoutes[task].model" size="small" class="mono" :placeholder="$t('settings.llm_model')" />
                      <el-input-number v-model="taskRoutes[task].temperature" size="small" :min="0" :max="2" :step="0.1" :placeholder="$t('settings.llm_temperature')" />
                      <el-input-number v-model="taskRoutes[task].max_tokens" size="small" :min="256" :max="32000" :step="256" :placeholder="$t('settings.llm_max_tokens')" />
                      <el-switch v-model="taskRoutes[task].stream" size="small" :active-text="$t('settings.llm_stream')" />
                    </div>
                  </div>
                </div>
                </el-collapse-item>
                </el-collapse>

                <div style="display:flex;gap:8px">
                  <el-button type="primary" @click="saveConfig" :loading="saving" :disabled="saving">
                    {{ saving ? $t('settings.llm_saving') : $t('settings.llm_save') }}
                  </el-button>
                  <el-button @click="testConnection" :loading="testing" :disabled="testing">
                    {{ testing ? $t('settings.llm_testing') : $t('settings.llm_test') }}
                  </el-button>
                </div>

                <el-alert
                  v-if="testResult"
                  :type="testResult.success ? 'success' : 'error'"
                  :closable="false"
                  style="margin-top:12px"
                >
                  <template #title>
                    <span>{{ testResult.success ? $t('settings.llm_test_success') : $t('settings.llm_test_failed') }}</span>
                    <span class="mono text-2" style="font-size:12px;margin-left:8px">{{ testResult.model }}</span>
                  </template>
                  {{ testResult.message }}
                </el-alert>
              </el-form>
            </el-card>

            <!-- 可用提供商 -->
            <el-card>
              <template #header>
                <span>{{ $t('settings.llm_presets_title') }}</span>
              </template>
              <div v-for="p in presets" :key="p.id" class="provider-item">
                <div class="provider-name">{{ p.name }}</div>
                <div class="text-3 mono" style="font-size:11px">{{ p.base_url }}</div>
                <div class="text-2" style="font-size:11px;margin-top:4px">
                  {{ $t('settings.llm_presets_models') }}: {{ (p.models || []).join(', ') }}
                </div>
              </div>
              <el-divider />
              <p class="text-2" style="font-size:11px;line-height:1.6">
                {{ $t('settings.llm_presets_desc') }}
              </p>
            </el-card>
          </div>
        </el-tab-pane>

        <!-- Tab 2: AI 任务与队列 -->
        <el-tab-pane :label="$t('settings.ai_jobs')" name="ai-jobs">
          <div class="settings-stack">
            <el-card v-loading="aiOpsLoading">
              <template #header>
                <div class="flex items-center justify-between">
                  <div>
                    <span>{{ $t('settings.ai_jobs_card_title') }}</span>
                    <span v-if="aiJobStatusBadges.length" class="text-2" style="font-size:12px;margin-left:8px">
                      <el-tag v-for="s in aiJobStatusBadges" :key="s.status" size="small" :type="statusTagType(s.status)" style="margin-right:4px">
                        {{ s.status }} {{ s.count }}
                      </el-tag>
                    </span>
                  </div>
                  <el-button size="small" @click="loadAiOps" :loading="aiOpsLoading">{{ $t('settings.dlq_refresh') }}</el-button>
                </div>
              </template>
              <el-table :data="aiQueueRows" size="small" :empty-text="$t('common.no_data')">
                <el-table-column :label="$t('settings.queue_name')" min-width="170">
                  <template #default="{ row }">
                    <div style="font-weight:600">{{ queueDisplayName(row) }}</div>
                    <div class="mono text-3" style="font-size:11px">{{ row.id }}</div>
                  </template>
                </el-table-column>
                <el-table-column :label="$t('settings.queue_key')" min-width="180">
                  <template #default="{ row }">
                    <span class="mono text-2" style="font-size:11px">{{ row.queue_key }}</span>
                  </template>
                </el-table-column>
                <el-table-column :label="$t('settings.queue_pending')" width="90" align="center">
                  <template #default="{ row }">
                    <el-tag :type="row.pending ? 'warning' : 'info'" size="small">{{ row.pending ?? '—' }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column :label="$t('settings.queue_dlq')" width="90" align="center">
                  <template #default="{ row }">
                    <el-tag :type="row.dlq ? 'danger' : 'info'" size="small">{{ row.dlq ?? '—' }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column :label="$t('settings.queue_recent_error')" min-width="220">
                  <template #default="{ row }">
                    <span class="text-2" style="font-size:12px">{{ queueRecentError(row) }}</span>
                  </template>
                </el-table-column>
                <el-table-column :label="$t('common.actions')" width="130">
                  <template #default="{ row }">
                    <el-button size="small" @click="viewQueueDlq(row)">{{ $t('settings.queue_view_dlq') }}</el-button>
                  </template>
                </el-table-column>
              </el-table>
            </el-card>

            <el-card v-loading="aiOpsLoading">
              <template #header>
                <div class="flex items-center justify-between">
                  <span>{{ $t('settings.ai_jobs_recent') }}</span>
                  <el-select v-model="aiJobStatusFilter" size="small" style="width:150px" @change="loadAiOps">
                    <el-option :label="$t('settings.job_status_all')" value="" />
                    <el-option v-for="s in aiJobStatusOptions" :key="s" :label="jobStatusLabel(s)" :value="s" />
                  </el-select>
                </div>
              </template>
              <el-table :data="aiJobs" size="small" :empty-text="$t('common.no_data')" max-height="320">
                <el-table-column :label="$t('settings.job_id')" min-width="180">
                  <template #default="{ row }">
                    <span class="mono text-2" style="font-size:11px">{{ row.job_id || '—' }}</span>
                  </template>
                </el-table-column>
                <el-table-column :label="$t('settings.queue_name')" width="130">
                  <template #default="{ row }">{{ queueDisplayName({ id: row.queue }) }}</template>
                </el-table-column>
                <el-table-column :label="$t('settings.job_status')" width="120">
                  <template #default="{ row }">
                    <el-tag :type="statusTagType(row.status)" size="small">{{ jobStatusLabel(row.status) }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column :label="$t('settings.job_targets')" min-width="160">
                  <template #default="{ row }">
                    <span class="mono text-2" style="font-size:11px">{{ (row.target_ids || []).join(', ') || '—' }}</span>
                  </template>
                </el-table-column>
                <el-table-column :label="$t('settings.queue_recent_error')" min-width="220">
                  <template #default="{ row }">
                    <span class="text-2" style="font-size:12px">{{ row.error || row.summary || '—' }}</span>
                  </template>
                </el-table-column>
                <el-table-column :label="$t('common.actions')" width="120">
                  <template #default="{ row }">
                    <el-button
                      size="small"
                      :disabled="row.status !== 'dlq' || aiJobRetrying === row.job_id"
                      @click="retryAiJob(row)"
                    >
                      {{ aiJobRetrying === row.job_id ? '…' : $t('settings.job_retry') }}
                    </el-button>
                  </template>
                </el-table-column>
              </el-table>
            </el-card>
          </div>
        </el-tab-pane>

        <!-- Tab 3: Prompt 管理 -->
        <el-tab-pane :label="$t('settings.prompts')" name="prompts">
          <el-card v-loading="promptLoading">
            <template #header>
              <div class="flex items-center justify-between" style="gap:8px">
                <span>{{ $t('settings.prompts') }}</span>
                <div class="flex items-center" style="gap:8px">
                  <el-select v-model="promptTaskType" size="small" style="width:150px" @change="loadPrompts">
                    <el-option :label="$t('settings.prompt_all_tasks')" value="" />
                    <el-option v-for="opt in promptTaskOptions" :key="opt.value" :label="t(opt.label)" :value="opt.value" />
                  </el-select>
                  <el-button size="small" @click="loadPrompts" :loading="promptLoading">{{ $t('settings.dlq_refresh') }}</el-button>
                  <el-button size="small" type="warning" plain :disabled="!promptTaskType" @click="resetPromptTask">
                    {{ $t('settings.prompt_reset') }}
                  </el-button>
                  <el-button size="small" type="primary" @click="openPromptDialog">{{ $t('settings.prompt_new') }}</el-button>
                </div>
              </div>
            </template>
            <el-table :data="promptItems" size="small" :empty-text="$t('common.no_data')" max-height="420">
              <el-table-column :label="$t('settings.prompt_task_type')" width="120">
                <template #default="{ row }">{{ promptTaskLabel(row.task_type) }}</template>
              </el-table-column>
              <el-table-column :label="$t('settings.prompt_version')" width="90">
                <template #default="{ row }">
                  <span class="mono">v{{ row.version }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('settings.prompt_name')" min-width="150" prop="name" />
              <el-table-column :label="$t('settings.prompt_description')" min-width="180">
                <template #default="{ row }">
                  <span class="text-2" style="font-size:12px">{{ row.description || '—' }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('settings.prompt_active')" width="90" align="center">
                <template #default="{ row }">
                  <el-tag :type="row.active ? 'success' : 'info'" size="small">{{ row.active ? $t('common.enabled') : $t('common.disabled') }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column :label="$t('common.time')" width="150">
                <template #default="{ row }">
                  <span class="text-2" style="font-size:12px">{{ fmt.fromNow(row.updated_at || row.created_at) }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('common.actions')" width="260" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" @click="viewPrompt(row)">{{ $t('common.preview') }}</el-button>
                  <el-button size="small" @click="viewPromptDiff(row)">{{ $t('settings.prompt_diff') }}</el-button>
                  <el-button size="small" type="primary" :disabled="row.active" @click="activatePrompt(row)">
                    {{ $t('settings.prompt_activate') }}
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-tab-pane>

        <!-- Tab 4: 死信队列 -->
        <el-tab-pane :label="$t('settings.dlq')" name="dlq">
          <el-card>
            <template #header>
              <div class="flex items-center justify-between">
                <span>{{ $t('settings.dlq_card_title') }}</span>
                <div class="flex items-center" style="gap:8px">
                  <!-- 死信队列筛选：使用 queueDisplayName 通过 i18n key 映射翻译队列名 -->
                  <el-select v-model="selectedDlqQueue" size="small" style="width:180px" :placeholder="$t('settings.dlq_select_queue')" clearable @change="loadDlq">
                    <el-option value="" :label="$t('settings.dlq_all_queues')" />
                    <el-option v-for="q in dlqQueueOptions" :key="q.id" :label="queueDisplayName(q)" :value="q.id" />
                  </el-select>
                  <el-button size="small" @click="loadDlq" :loading="dlqLoading">{{ $t('settings.dlq_refresh') }}</el-button>
                </div>
              </div>
            </template>
            <el-empty v-if="!dlqLoading && !dlqItems.length" :description="$t('settings.dlq_empty')" :image-size="48" />
            <el-table v-else :data="dlqItems" style="width:100%" v-loading="dlqLoading">
              <el-table-column :label="$t('settings.dlq_col_index')" width="60">
                <template #default="{ $index }">
                  <span class="mono text-2">{{ $index }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('settings.dlq_target')" min-width="200">
                <template #default="{ row }">
                  <span class="mono text-2" style="font-size:12px">{{ dlqTarget(row) }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('settings.queue_name')" min-width="130">
                <template #default="{ row }">
                  <span class="text-2" style="font-size:12px">{{ queueDisplayName({ id: row.queue || selectedDlqQueue }) }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('settings.dlq_fail_count')" width="100">
                <template #default="{ row }">
                  <el-tag type="danger" size="small">{{ row.fail_count ?? 1 }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column :label="$t('settings.queue_recent_error')" min-width="180">
                <template #default="{ row }">
                  <span class="text-2" style="font-size:12px">{{ row.error || '—' }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('settings.dlq_time')" width="180">
                <template #default="{ row }">
                  <span class="text-2" style="font-size:12px">{{ row.ts || '—' }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('common.actions')" width="160">
                <template #default="{ row }">
                  <el-button size="small" @click="retryDlq(row)" :disabled="dlqRetrying === row.queue + ':' + row.index">
                    {{ dlqRetrying === row.queue + ':' + row.index ? '…' : $t('settings.dlq_retry') }}
                  </el-button>
                  <el-button size="small" type="danger" @click="removeDlq(row)" :disabled="dlqRemoving === row.queue + ':' + row.index">
                    {{ dlqRemoving === row.queue + ':' + row.index ? '…' : $t('settings.dlq_remove') }}
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-tab-pane>

        <!-- Tab 3: 执行环境 -->
        <el-tab-pane :label="$t('settings.environments')" name="env">
          <el-card v-loading="envLoading">
            <template #header>
              <div class="flex items-center justify-between">
                <span>{{ $t('settings.env_card_title') }}</span>
                <el-button type="primary" size="small" @click="openEnvDialog()">{{ $t('settings.env_new') }}</el-button>
              </div>
            </template>

            <el-empty v-if="!envLoading && !environments.length" :description="$t('settings.env_empty_hint')" :image-size="48" />

            <el-table v-else :data="environments" style="width:100%">
              <el-table-column :label="$t('settings.env_col_name')" min-width="120">
                <template #default="{ row }">
                  <span style="font-weight:600">{{ row.name }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('settings.env_col_base_url')" min-width="180">
                <template #default="{ row }">
                  <span class="mono text-2" style="font-size:12px">{{ row.base_url || '—' }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('settings.env_col_headers')" min-width="100">
                <template #default="{ row }">
                  <el-tag v-if="Object.keys(row.headers || {}).length" size="small" type="info">{{ $t('settings.count_items', { n: Object.keys(row.headers).length }) }}</el-tag>
                  <span v-else class="text-3">—</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('settings.env_col_vars')" min-width="100">
                <template #default="{ row }">
                  <el-tag v-if="Object.keys(row.variables || {}).length" size="small" type="info">{{ $t('settings.count_items', { n: Object.keys(row.variables).length }) }}</el-tag>
                  <span v-else class="text-3">—</span>
                </template>
              </el-table-column>
              <el-table-column label="鉴权模板" min-width="100">
                <template #default="{ row }">
                  <el-tag v-if="(row.auth_templates || []).length" size="small" type="success">{{ $t('settings.count_items', { n: row.auth_templates.length }) }}</el-tag>
                  <span v-else class="text-3">—</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('settings.env_col_description')" min-width="140">
                <template #default="{ row }">
                  <span class="text-2" style="font-size:12px">{{ row.description || '—' }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('common.actions')" width="140" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" @click="openEnvDialog(row)">{{ $t('common.edit') }}</el-button>
                  <el-popconfirm :title="$t('settings.env_confirm_delete')" @confirm="deleteEnv(row.id)">
                    <template #reference>
                      <el-button size="small" type="danger">{{ $t('settings.env_delete') }}</el-button>
                    </template>
                  </el-popconfirm>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-tab-pane>

        <!-- Tab 4: 通用 -->
        <el-tab-pane :label="$t('settings.database_services')" name="database">
          <div class="settings-layout">
            <el-card v-loading="dbLoading">
              <template #header>
                <div class="flex items-center justify-between">
                  <span>{{ $t('settings.database_services') }}</span>
                  <el-button size="small" @click="openDbDialog()">{{ $t('common.new') }}</el-button>
                </div>
              </template>
              <el-table :data="dbServices" size="small" max-height="260" :empty-text="$t('common.no_data')">
                <el-table-column :label="$t('common.name')" prop="name" min-width="130" />
                <el-table-column :label="$t('common.type')" prop="type" width="110" />
                <el-table-column :label="$t('settings.db_database')" prop="database" min-width="130" />
                <el-table-column label="连接状态" width="150">
                  <template #default="{ row }">
                    <el-tag v-if="dbTestState[row.id]?.ok" size="small" type="success">可用 {{ dbTestState[row.id].duration_ms }}ms</el-tag>
                    <el-tooltip v-else-if="dbTestState[row.id]?.error" :content="dbTestState[row.id].error">
                      <el-tag size="small" type="danger">失败</el-tag>
                    </el-tooltip>
                    <el-tag v-else size="small" type="info">{{ row.enabled ? '未测试' : '已停用' }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column :label="$t('common.actions')" width="190">
                  <template #default="{ row }">
                    <el-button size="small" @click="openDbDialog(row)">{{ $t('common.edit') }}</el-button>
                    <el-button size="small" @click="runDbTest(row)">{{ $t('settings.db_test') }}</el-button>
                    <el-button size="small" type="danger" @click="deleteDbService(row)">✕</el-button>
                  </template>
                </el-table-column>
              </el-table>
            </el-card>

            <el-card v-loading="snippetLoading">
              <template #header>
                <div class="flex items-center justify-between">
                  <span>{{ $t('settings.sql_snippets') }}</span>
                  <el-button size="small" @click="openSnippetDialog()">{{ $t('common.new') }}</el-button>
                </div>
                <div class="sql-snippet-filters">
                  <el-input v-model="snippetSearch" size="small" clearable :placeholder="$t('settings.sql_search_placeholder')" />
                  <el-select v-model="snippetServiceFilter" size="small" clearable :placeholder="$t('settings.sql_service_filter_placeholder')">
                    <el-option v-for="svc in dbServices" :key="svc.id" :label="svc.name" :value="svc.id" />
                  </el-select>
                </div>
              </template>
              <el-table :data="filteredSqlSnippets" size="small" max-height="260" :empty-text="$t('common.no_data')">
                <el-table-column :label="$t('common.name')" prop="name" min-width="140" />
                <el-table-column :label="$t('settings.database_service')" min-width="130">
                  <template #default="{ row }">{{ serviceName(row.db_service_id) }}</template>
                </el-table-column>
                <el-table-column :label="$t('settings.sql_status')" width="90">
                  <template #default="{ row }">
                    <el-tag size="small" :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? $t('settings.sql_enabled') : $t('settings.sql_disabled') }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column :label="$t('settings.sql_updated_at')" width="150">
                  <template #default="{ row }">{{ fmt(row.updated_at) }}</template>
                </el-table-column>
                <el-table-column :label="$t('common.actions')" width="230">
                  <template #default="{ row }">
                    <el-button size="small" @click="openSnippetDialog(row)">{{ $t('common.edit') }}</el-button>
                    <el-button size="small" @click="copySnippetId(row)">{{ $t('settings.sql_copy_id') }}</el-button>
                    <el-button size="small" type="danger" @click="deleteSnippet(row)">{{ $t('common.delete') }}</el-button>
                  </template>
                </el-table-column>
              </el-table>
              <div v-if="sqlRunResult" class="sql-result">
                <el-tabs model-value="table">
                  <el-tab-pane :label="$t('settings.sql_result_table')" name="table">
                    <el-table v-if="sqlRunResult.rows?.length" :data="sqlRunResult.rows" size="small" max-height="220">
                      <el-table-column v-for="col in sqlRunResult.columns || []" :key="col" :prop="col" :label="col" min-width="120" />
                    </el-table>
                    <el-empty v-else :description="$t('settings.sql_no_rows')" />
                  </el-tab-pane>
                  <el-tab-pane :label="$t('settings.sql_raw_json')" name="json">
                    <pre class="code-block">{{ JSON.stringify(sqlRunResult, null, 2) }}</pre>
                  </el-tab-pane>
                </el-tabs>
              </div>
            </el-card>
          </div>
        </el-tab-pane>

        <!-- Tab 4: 通用 -->
        <el-tab-pane :label="$t('settings.general')" name="general">
          <el-card>
            <template #header>
              <span>{{ $t('settings.general') }}</span>
            </template>
            <el-form label-position="top">
              <el-form-item :label="$t('settings.language')">
                <el-radio-group v-model="locale" size="small" @change="onLangChange">
                  <el-radio-button value="zh-CN">中文</el-radio-button>
                  <el-radio-button value="en">English</el-radio-button>
                </el-radio-group>
              </el-form-item>
              <!-- 自动触发下游AI流程：审核采纳后自动入队后续任务（场景→监控/数据模板） -->
              <el-form-item :label="$t('settings.auto_trigger_ai')">
                <el-switch v-model="autoTriggerAi" @change="onAutoTriggerChange" />
                <span class="text-3" style="margin-left:8px">{{ $t('settings.auto_trigger_ai_desc') }}</span>
              </el-form-item>
              <!-- 进入审核流程：开启后AI产出需人工审核，关闭后自动采纳 -->
              <el-form-item :label="$t('settings.auto_review_flow')">
                <el-switch v-model="autoReviewFlow" @change="onAutoReviewFlowChange" />
                <span class="text-3" style="margin-left:8px">{{ $t('settings.auto_review_flow_desc') }}</span>
              </el-form-item>
              <!-- 导航栏自动收起：点击菜单导航后自动移除焦点，侧边栏收起 -->
              <el-form-item :label="$t('settings.nav_auto_collapse')">
                <el-switch v-model="navAutoCollapse" @change="onNavAutoCollapseChange" />
                <span class="text-3" style="margin-left:8px">{{ $t('settings.nav_auto_collapse_desc') }}</span>
              </el-form-item>
            </el-form>
          </el-card>
        </el-tab-pane>

        <!-- Tab 5: 项目管理 -->
        <el-tab-pane :label="$t('settings.project_mgmt')" name="projects">
          <el-card v-loading="projectLoading">
            <template #header>
              <div class="flex items-center justify-between">
                <span>{{ $t('settings.project_mgmt') }}</span>
                <el-button type="primary" size="small" @click="openProjectDialog()">{{ $t('common.newProject') }}</el-button>
              </div>
            </template>

            <el-empty v-if="!projectLoading && !projects.length" :description="$t('common.noProject')" :image-size="48" />

            <el-table v-else :data="projects" style="width:100%">
              <el-table-column :label="$t('common.projectName')" min-width="140">
                <template #default="{ row }">
                  <span style="font-weight:600">{{ row.name }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('common.projectSlug')" min-width="100">
                <template #default="{ row }">
                  <span class="mono text-2" style="font-size:12px">{{ row.slug }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('common.description')" min-width="140">
                <template #default="{ row }">
                  <span class="text-2" style="font-size:12px">{{ row.description || '—' }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('common.actions')" width="160" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" @click="openProjectDialog(row)">{{ $t('common.edit') }}</el-button>
                  <el-popconfirm :title="$t('settings.project_confirm_delete')" @confirm="deleteProject(row.id)">
                    <template #reference>
                      <el-button size="small" type="danger">{{ $t('common.delete') }}</el-button>
                    </template>
                  </el-popconfirm>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-tab-pane>

        <!-- Tab 6: 告警渠道 (从独立页面迁移至此，统一管理多渠道告警) -->
        <el-tab-pane :label="$t('alert_channels.title')" name="alert-channels">
          <el-card v-loading="channelsLoading">
            <template #header>
              <div class="flex items-center justify-between">
                <span>{{ $t('alert_channels.title') }}</span>
                <el-button type="primary" size="small" @click="openCreate">{{ $t('alert_channels.new_btn') }}</el-button>
              </div>
            </template>

            <el-empty v-if="!channelsLoading && !channels.length" :description="$t('settings.dlq_empty')" :image-size="48" />

            <el-table v-else :data="channels" style="width:100%">
              <el-table-column :label="$t('alert_channels.col_name')" min-width="140">
                <template #default="{ row }">
                  <span>{{ row.name }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('alert_channels.col_type')" width="100">
                <template #default="{ row }">
                  <el-tag size="small" :type="typeTagType(row.type)">{{ typeLabel(row.type) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column :label="$t('alert_channels.col_url')" min-width="240">
                <template #default="{ row }">
                  <span class="mono text-2" style="font-size:11px;word-break:break-all">{{ row.url }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('alert_channels.col_status')" width="80">
                <template #default="{ row }">
                  <el-tag size="small" :type="row.enabled ? 'success' : 'info'">
                    {{ row.enabled ? $t('common.enabled') : $t('common.disabled') }}
                  </el-tag>
                </template>
              </el-table-column>
              <!-- Bug2 修复：操作列按钮用 icon + nowrap 防止换行 -->
              <el-table-column :label="$t('common.actions')" width="180">
                <template #default="{ row }">
                  <div class="flex items-center" style="gap:6px;flex-wrap:nowrap;white-space:nowrap">
                    <el-button size="small" link @click="toggleEnabled(row)">{{ row.enabled ? $t('common.disable') : $t('common.enable') }}</el-button>
                    <el-button size="small" link @click="editChannel(row)">{{ $t('common.edit') }}</el-button>
                    <el-button type="danger" size="small" link @click="deleteChannel(row)">✕</el-button>
                  </div>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-tab-pane>
      </el-tabs>
    </div>

    <!-- 新建/编辑环境 Dialog -->
    <el-dialog
      v-model="showEnvDialog"
      :title="editingEnvId ? $t('settings.env_edit_dialog') : $t('settings.env_new_dialog')"
      width="560px"
      :close-on-click-modal="false"
    >
      <el-form label-position="top" :model="envForm">
        <el-form-item :label="$t('settings.env_form_name')" required>
          <el-input v-model="envForm.name" :placeholder="$t('settings.env_form_name_placeholder')" @keyup.enter="saveEnv" />
        </el-form-item>
        <el-form-item :label="$t('settings.env_form_base_url')">
          <el-input v-model="envForm.base_url" :placeholder="$t('settings.env_form_base_url_placeholder')" class="mono" />
        </el-form-item>
        <el-form-item :label="$t('settings.env_form_headers')">
          <el-input
            v-model="envForm.headers_str"
            type="textarea"
            :rows="3"
            :placeholder="$t('settings.env_form_headers_placeholder')"
            :class="{ 'input-error': envHeadersJsonError }"
            @input="envHeadersJsonError = false"
          />
          <span v-if="envHeadersJsonError" class="field-error">{{ $t('settings.env_json_error') }}</span>
        </el-form-item>
        <el-form-item :label="$t('settings.env_form_vars')">
          <el-input
            v-model="envForm.variables_str"
            type="textarea"
            :rows="3"
            :placeholder="$t('settings.env_form_vars_placeholder')"
            :class="{ 'input-error': envVarsJsonError }"
            @input="envVarsJsonError = false"
          />
          <span v-if="envVarsJsonError" class="field-error">{{ $t('settings.env_json_error') }}</span>
          <span class="text-3" style="font-size:11px;margin-top:4px;display:inline-block">{{ $t('settings.env_form_vars_hint') }}</span>
        </el-form-item>
        <el-form-item label="鉴权模板 JSON">
          <el-input
            v-model="envForm.auth_templates_str"
            type="textarea"
            :rows="5"
            placeholder='[{"name":"登录 Token","type":"bearer","token":"{{steps.login.token}}"}]'
            :class="{ 'input-error': envAuthTemplatesJsonError }"
            @input="envAuthTemplatesJsonError = false"
          />
          <span v-if="envAuthTemplatesJsonError" class="field-error">{{ $t('settings.env_json_error') }}</span>
          <span class="text-3" style="font-size:11px;margin-top:4px;display:inline-block">支持 bearer/basic/apikey，与步骤鉴权结构一致。</span>
        </el-form-item>
        <el-form-item :label="$t('settings.env_form_description')">
          <el-input v-model="envForm.description" :placeholder="$t('settings.env_form_description_placeholder')" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showEnvDialog = false; resetEnvForm()">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" @click="saveEnv" :disabled="savingEnv || !envForm.name" :loading="savingEnv">
          {{ editingEnvId ? $t('common.update') : $t('common.create') }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="showPromptDialog"
      :title="$t('settings.prompt_new')"
      width="720px"
      :close-on-click-modal="false"
    >
      <el-form label-position="top" :model="promptForm">
        <div class="form-grid">
          <el-form-item :label="$t('settings.prompt_task_type')" required>
            <el-select v-model="promptForm.task_type" style="width:100%">
              <el-option v-for="opt in promptTaskOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
            </el-select>
          </el-form-item>
          <el-form-item :label="$t('settings.prompt_name')" required>
            <el-input v-model="promptForm.name" placeholder="v2-strict" />
          </el-form-item>
        </div>
        <el-form-item :label="$t('settings.prompt_description')">
          <el-input v-model="promptForm.description" :placeholder="$t('settings.prompt_description_placeholder')" />
        </el-form-item>
        <el-form-item :label="$t('settings.prompt_content')" required>
          <el-input v-model="promptForm.content" type="textarea" :rows="14" class="mono" />
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="promptForm.activate" :label="$t('settings.prompt_activate_now')" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showPromptDialog = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" @click="savePrompt" :loading="promptSaving" :disabled="!promptForm.task_type || !promptForm.content.trim()">
          {{ $t('common.save') }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="showPromptPreview"
      :title="promptPreviewTitle"
      width="760px"
    >
      <pre class="code-block prompt-preview">{{ promptPreviewContent }}</pre>
      <template #footer>
        <el-button @click="showPromptPreview = false">{{ $t('common.close') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="showPromptDiff"
      :title="promptDiffTitle"
      width="960px"
    >
      <div class="prompt-diff-grid">
        <div>
          <div class="text-2" style="font-size:12px;margin-bottom:6px">{{ $t('settings.prompt_active_version') }}</div>
          <pre class="code-block prompt-preview">{{ promptDiffActive }}</pre>
        </div>
        <div>
          <div class="text-2" style="font-size:12px;margin-bottom:6px">{{ $t('settings.prompt_selected_version') }}</div>
          <pre class="code-block prompt-preview">{{ promptDiffSelected }}</pre>
        </div>
      </div>
      <template #footer>
        <el-button @click="showPromptDiff = false">{{ $t('common.close') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="showDbDialog"
      :title="dbEditingId ? $t('settings.db_edit_dialog') : $t('settings.db_new_dialog')"
      width="640px"
      :close-on-click-modal="false"
    >
      <el-form label-position="top">
        <el-alert type="info" :closable="false" show-icon class="db-hint">
          <template #title>{{ $t('settings.db_security_hint') }}</template>
        </el-alert>
        <el-form-item :label="$t('common.name')"><el-input v-model="dbForm.name" /></el-form-item>
        <div class="form-grid">
          <el-form-item :label="$t('common.type')">
            <el-select v-model="dbForm.type" style="width:100%">
              <el-option label="PostgreSQL" value="postgresql" />
              <el-option label="MySQL" value="mysql" />
              <el-option label="SQLite" value="sqlite" />
            </el-select>
          </el-form-item>
          <el-form-item :label="$t('common.enabled')"><el-switch v-model="dbForm.enabled" /></el-form-item>
        </div>
        <div class="form-grid">
          <el-form-item :label="$t('settings.db_host')"><el-input v-model="dbForm.host" /></el-form-item>
          <el-form-item :label="$t('settings.db_port')"><el-input-number v-model="dbForm.port" :min="0" style="width:100%" /></el-form-item>
        </div>
        <el-form-item :label="$t('settings.db_database')"><el-input v-model="dbForm.database" class="mono" /></el-form-item>
        <div class="form-grid">
          <el-form-item :label="$t('settings.db_username')"><el-input v-model="dbForm.username" /></el-form-item>
          <el-form-item :label="$t('settings.db_password')"><el-input v-model="dbForm.password" type="password" show-password /></el-form-item>
        </div>
        <div class="form-grid">
          <el-form-item :label="$t('settings.db_timeout_ms')"><el-input-number v-model="dbForm.timeout_ms" :min="500" style="width:100%" /></el-form-item>
          <el-form-item :label="$t('settings.db_max_rows')"><el-input-number v-model="dbForm.max_rows" :min="1" :max="1000" style="width:100%" /></el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="showDbDialog = false">{{ $t('common.cancel') }}</el-button>
        <el-button :disabled="!dbEditingId" @click="testDbService">{{ $t('settings.db_test') }}</el-button>
        <el-button type="primary" @click="saveDbService">{{ $t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="showSnippetDialog"
      :title="snippetEditingId ? $t('settings.sql_edit_dialog') : $t('settings.sql_new_dialog')"
      width="640px"
      :close-on-click-modal="false"
    >
      <el-form label-position="top">
        <el-form-item :label="$t('common.name')"><el-input v-model="snippetForm.name" /></el-form-item>
        <el-form-item :label="$t('settings.database_service')">
          <el-select v-model="snippetForm.db_service_id" style="width:100%">
            <el-option v-for="svc in dbServices" :key="svc.id" :label="svc.name" :value="svc.id" />
          </el-select>
        </el-form-item>
        <el-form-item :label="$t('settings.sql_text')"><el-input v-model="snippetForm.sql" type="textarea" :rows="7" class="mono" /></el-form-item>
        <div class="form-grid">
          <el-form-item :label="$t('settings.sql_params')"><el-input v-model="snippetRunParams" class="mono" /></el-form-item>
          <el-form-item :label="$t('common.enabled')"><el-switch v-model="snippetForm.enabled" /></el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="showSnippetDialog = false">{{ $t('common.cancel') }}</el-button>
        <el-button @click="validateSnippet">校验</el-button>
        <el-button :disabled="!snippetEditingId" @click="runSnippet">{{ $t('settings.sql_run') }}</el-button>
        <el-button type="primary" @click="saveSnippet">{{ $t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- 新建/编辑项目 Dialog -->
    <el-dialog
      v-model="showProjectDialog"
      :title="editingProjectId ? $t('common.editProject') : $t('common.newProject')"
      width="560px"
      :close-on-click-modal="false"
    >
      <el-form label-position="top" :model="projectForm">
        <el-form-item :label="$t('common.projectName')" required>
          <el-input v-model="projectForm.name" :placeholder="$t('common.projectNamePlaceholder')" @keyup.enter="saveProject" />
        </el-form-item>
        <el-form-item :label="$t('common.projectSlug')" required>
          <el-input v-model="projectForm.slug" :placeholder="$t('common.projectSlugPlaceholder')" class="mono" @keyup.enter="saveProject" />
        </el-form-item>
        <el-form-item :label="$t('common.description')">
          <el-input v-model="projectForm.description" :placeholder="$t('common.descriptionPlaceholder')" />
        </el-form-item>
        <!-- 需求1: 域名过滤字段，从 App.vue 迁移至此 -->
        <el-divider content-position="left">
          <span style="font-size:12px;font-weight:600;color:var(--text-2)">{{ $t('common.domainFilter') }}</span>
        </el-divider>
        <p class="text-2" style="font-size:11px;line-height:1.6;margin-bottom:12px">
          {{ $t('common.domainFilterHint') }}
        </p>
        <el-form-item :label="$t('common.domainAllowlist')">
          <el-input
            v-model="projectForm.domain_allowlist_str"
            placeholder="api.example.com, *.myapp.com, *.github.com"
            class="mono"
          />
          <div v-if="parsedProjectAllowlist.length" class="domain-tags">
            <el-tag v-for="d in parsedProjectAllowlist" :key="d" size="small" type="success">{{ d }}</el-tag>
          </div>
        </el-form-item>
        <el-form-item :label="$t('common.domainBlocklist')">
          <el-input
            v-model="projectForm.domain_blocklist_str"
            placeholder="ads.example.com, tracker.io, *.spam.net"
            class="mono"
          />
          <div v-if="parsedProjectBlocklist.length" class="domain-tags">
            <el-tag v-for="d in parsedProjectBlocklist" :key="d" size="small" type="danger">{{ d }}</el-tag>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showProjectDialog = false; resetProjectForm()">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" @click="saveProject" :disabled="savingProject || !projectForm.name || !projectForm.slug" :loading="savingProject">
          {{ editingProjectId ? $t('common.update') : $t('common.create') }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 告警渠道：创建/编辑弹窗（从 alert-channels/Index.vue 迁移至此） -->
    <el-dialog
      v-model="channelModal"
      :title="editingChannelId ? $t('alert_channels.edit_title') : $t('alert_channels.new_title')"
      width="520px"
      @close="resetForm"
    >
      <el-form label-position="top">
        <el-form-item :label="$t('alert_channels.form_name')">
          <el-input v-model="channelForm.name" :placeholder="$t('alert_channels.name_placeholder')" />
        </el-form-item>
        <el-form-item :label="$t('alert_channels.form_type')">
          <el-select v-model="channelForm.type" style="width:100%">
            <el-option :label="$t('alert_channels.type_dingtalk')" value="dingtalk" />
            <el-option :label="$t('alert_channels.type_wechat')" value="wechat" />
            <el-option :label="$t('alert_channels.type_slack')" value="slack" />
            <el-option :label="$t('alert_channels.type_custom')" value="custom" />
          </el-select>
        </el-form-item>
        <el-form-item :label="$t('alert_channels.form_url')">
          <el-input v-model="channelForm.url" :placeholder="$t('alert_channels.url_placeholder')" />
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="channelForm.enabled" :label="$t('alert_channels.form_enabled')" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="channelModal = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" @click="submit" :disabled="!channelForm.name || !channelForm.url">
          {{ $t('common.save') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'
import {
  settingsApi,
  environmentApi,
  dlqApi,
  aiJobApi,
  promptApi,
  systemApi,
  projectApi,
  alertChannelApi,
  databaseServiceApi,
  sqlApi,
  sqlSnippetApi,
} from '@/api'
import { useProjectStore, useToastStore } from '@/stores'
import { fmt } from '@/utils'

const { t } = useI18n()
const route = useRoute()
const toast = useToastStore()
const projectStore = useProjectStore()

// ── Tabs ──────────────────────────────────────────────────
const activeTab = ref('llm')

// ── LLM 配置 ──────────────────────────────────────────────
const presets = ref([])
const configured = ref(false)
const showKey = ref(false)
const saving = ref(false)
const testing = ref(false)
const discovering = ref(false)
const testResult = ref(null)
const loading = ref(true)
const activeNames = ref([''])

const form = ref({
  provider: 'openai',
  api_key: '',
  base_url: '',
  model: 'gpt-4o-mini',
  temperature: 0.1,
  max_tokens: 4096,
  stream: true,
})
const llmTaskTypes = ref(['doc', 'asserts', 'scenario', 'data_template', 'monitor', 'diff', 'diagnose', 'chat', 'alert'])
const taskRoutes = ref({})

const handleChange = (_val) => {
  // handler placeholder
}

function emptyTaskRoute(effective = {}) {
  return {
    enabled: false,
    provider: '',
    base_url: '',
    model: '',
    temperature: null,
    max_tokens: null,
    stream: false,
    effective,
  }
}

function ensureTaskRoutes(routes = {}) {
  const next = {}
  for (const task of llmTaskTypes.value) {
    const raw = routes[task] || {}
    next[task] = {
      ...emptyTaskRoute(raw.effective || {}),
      ...raw,
      // 任务模型路由默认收起，不保留服务端的 enabled 状态
      enabled: false,
      temperature: raw.temperature === '' ? null : raw.temperature,
      max_tokens: raw.max_tokens === '' ? null : raw.max_tokens,
      stream: raw.stream === '' ? false : Boolean(raw.stream),
    }
  }
  taskRoutes.value = next
}

ensureTaskRoutes()

const currentPresetModels = computed(() => {
  const p = presets.value.find(x => x.id === form.value.provider)
  return p ? p.models : []
})

const isLocalPreset = computed(() => {
  const localIds = ['ollama', 'lmstudio', 'llamacpp']
  return localIds.includes(form.value.provider)
})

function onProviderChange() {
  const p = presets.value.find(x => x.id === form.value.provider)
  if (p) {
    form.value.base_url = p.base_url
    form.value.model = p.models[0] || ''
  } else {
    form.value.base_url = ''
    form.value.model = ''
  }
}

async function loadConfig() {
  try {
    const res = await settingsApi.getLlm()
    presets.value = res.presets || []
    configured.value = res.configured
    const c = res.config
    form.value.provider = c.provider || 'openai'
    form.value.base_url = c.base_url || ''
    form.value.model = c.model || 'gpt-4o-mini'
    form.value.temperature = c.temperature ?? 0.1
    form.value.max_tokens = c.max_tokens ?? 4096
    form.value.stream = c.stream ?? true
    form.value.api_key = ''
    if (Array.isArray(res.task_types) && res.task_types.length) llmTaskTypes.value = res.task_types
    ensureTaskRoutes(c.task_routes || {})
  } catch (e) {
    toast.error(e.message || t('settings.toast_config_load_failed'))
  } finally { loading.value = false }
}

async function saveConfig() {
  if (!isLocalPreset.value && !form.value.api_key.trim()) {
    toast.error(t('settings.llm_empty_key_hint'))
    return
  }
  saving.value = true
  testResult.value = null
  try {
    let baseUrl = form.value.base_url
    if (form.value.provider !== 'custom') {
      const p = presets.value.find(x => x.id === form.value.provider)
      if (p) baseUrl = p.base_url
    }
    await settingsApi.saveLlm({
      provider: form.value.provider,
      api_key: form.value.api_key.trim(),
      base_url: baseUrl,
      model: form.value.model,
      temperature: form.value.temperature,
      max_tokens: form.value.max_tokens,
      stream: form.value.stream,
      task_routes: buildTaskRoutesPayload(),
    })
    toast.success(t('settings.llm_saved'))
    configured.value = true
    form.value.api_key = ''
  } catch (e) {
    toast.error(e.message)
  } finally {
    saving.value = false
  }
}

function buildTaskRoutesPayload() {
  const payload = {}
  for (const task of llmTaskTypes.value) {
    const route = taskRoutes.value[task]
    if (!route?.enabled) continue
    payload[task] = {
      enabled: true,
      provider: route.provider || '',
      base_url: route.base_url || '',
      model: route.model || '',
      temperature: route.temperature ?? '',
      max_tokens: route.max_tokens ?? '',
      stream: route.stream,
    }
  }
  return payload
}

function onTaskProviderChange(task) {
  const route = taskRoutes.value[task]
  const p = presets.value.find(x => x.id === route.provider)
  if (p) {
    route.base_url = p.base_url
    route.model = p.models[0] || route.model || ''
  }
}

async function testConnection() {
  if (!isLocalPreset.value && !form.value.api_key.trim()) {
    toast.error(t('settings.llm_empty_key_test_hint'))
    return
  }
  testing.value = true
  testResult.value = null
  try {
    let baseUrl = form.value.base_url
    if (form.value.provider !== 'custom') {
      const p = presets.value.find(x => x.id === form.value.provider)
      if (p) baseUrl = p.base_url
    }
    const res = await settingsApi.testLlm({
      api_key: form.value.api_key.trim(),
      base_url: baseUrl,
      model: form.value.model,
      provider: form.value.provider,
    })
    testResult.value = res
    if (res.success) {
      toast.success(t('settings.llm_test_success'))
    } else {
      toast.error(t('settings.llm_test_failed') + ': ' + res.message)
    }
  } catch (e) {
    toast.error(e.message)
  } finally {
    testing.value = false
  }
}

async function discoverModels() {
  discovering.value = true
  try {
    let baseUrl = form.value.base_url
    if (!baseUrl) {
      const p = presets.value.find(x => x.id === form.value.provider)
      if (p) baseUrl = p.base_url
    }
    const res = await settingsApi.discoverModels({
      base_url: baseUrl,
      api_key: form.value.api_key.trim(),
    })
    if (res.success && res.models?.length) {
      const p = presets.value.find(x => x.id === form.value.provider)
      if (p) {
        p.models = res.models
        if (!form.value.model || !res.models.includes(form.value.model)) {
          form.value.model = res.models[0]
        }
      }
      toast.success(t('settings.llm_discover_success', { n: res.models.length }))
    } else {
      // 避免硬编码中文回退文本，使用 i18n key
      toast.error(res.message ? t('settings.llm_discover_failed_msg', { msg: res.message }) : t('settings.llm_discover_failed'))
    }
  } catch (e) {
    toast.error(e.message || t('settings.llm_discover_failed'))
  } finally {
    discovering.value = false
  }
}

// ── 执行环境管理 ──────────────────────────────────────────
const environments = ref([])
const envLoading = ref(false)
const showEnvDialog = ref(false)
const editingEnvId = ref(null)
const savingEnv = ref(false)
const envHeadersJsonError = ref(false)
const envVarsJsonError = ref(false)
const envAuthTemplatesJsonError = ref(false)
const envForm = ref({
  name: '',
  base_url: '',
  headers_str: '{}',
  variables_str: '{}',
  auth_templates_str: '[]',
  description: '',
})

function resetEnvForm() {
  envForm.value = { name: '', base_url: '', headers_str: '{}', variables_str: '{}', auth_templates_str: '[]', description: '' }
  editingEnvId.value = null
  envHeadersJsonError.value = false
  envVarsJsonError.value = false
  envAuthTemplatesJsonError.value = false
}

function openEnvDialog(env) {
  if (env) {
    // 编辑模式：填充已有数据
    editingEnvId.value = env.id
    envForm.value = {
      name: env.name,
      base_url: env.base_url || '',
      headers_str: JSON.stringify(env.headers || {}, null, 2),
      variables_str: JSON.stringify(env.variables || {}, null, 2),
      auth_templates_str: JSON.stringify(env.auth_templates || [], null, 2),
      description: env.description || '',
    }
  } else {
    resetEnvForm()
  }
  showEnvDialog.value = true
}

async function loadEnvironments() {
  envLoading.value = true
  try {
    environments.value = await environmentApi.list(projectStore.current)  } catch (e) {
    toast.error(e.message || t('settings.env_toast_load_failed'))
  } finally {
    envLoading.value = false
  }
}

async function saveEnv() {
  if (!envForm.value.name) return
  let headers = {}
  let variables = {}
  let authTemplates = []
  envHeadersJsonError.value = false
  envVarsJsonError.value = false
  envAuthTemplatesJsonError.value = false
  if (envForm.value.headers_str.trim()) {
    try { headers = JSON.parse(envForm.value.headers_str) }
    catch { envHeadersJsonError.value = true; toast.error(t('settings.env_toast_headers_error')); return }
  }
  if (envForm.value.variables_str.trim()) {
    try { variables = JSON.parse(envForm.value.variables_str) }
    catch { envVarsJsonError.value = true; toast.error(t('settings.env_toast_vars_error')); return }
  }
  if (envForm.value.auth_templates_str.trim()) {
    try {
      authTemplates = JSON.parse(envForm.value.auth_templates_str)
      if (!Array.isArray(authTemplates)) throw new Error('auth_templates must be array')
    } catch {
      envAuthTemplatesJsonError.value = true
      toast.error('鉴权模板 JSON 格式错误')
      return
    }
  }
  savingEnv.value = true
  try {
    const payload = {
      name: envForm.value.name,
      base_url: envForm.value.base_url,
      headers,
      variables,
      auth_templates: authTemplates,
      description: envForm.value.description,
      project_id: projectStore.current,    }
    if (editingEnvId.value) {
      await environmentApi.update(editingEnvId.value, payload)
      toast.success(t('settings.env_toast_updated'))
    } else {
      await environmentApi.create(payload)
      toast.success(t('settings.env_toast_created'))
    }
    showEnvDialog.value = false
    resetEnvForm()
    await loadEnvironments()
  } catch (e) {
    toast.error(e.message)
  } finally {
    savingEnv.value = false
  }
}

async function deleteEnv(id) {
  try {
    await environmentApi.remove(id)
    toast.success(t('settings.env_toast_deleted'))
    await loadEnvironments()
  } catch (e) {
    toast.error(e.message)
  }
}

// ── AI 任务 / 队列 / Prompt 管理 ───────────────────────────
const queueDefinitions = ref([
  { id: 'ai_analyze', label: 'AI Analysis', queue_key: 'queue:ai_analyze' },
  { id: 'ai_analyze_doc', label: 'Doc Generation', queue_key: 'queue:ai_analyze_doc' },
  { id: 'ai_analyze_asserts', label: 'Assert Generation', queue_key: 'queue:ai_analyze_asserts' },
  { id: 'ai_scenario', label: 'Scenario Generation', queue_key: 'queue:ai_scenario' },
  { id: 'data_template', label: 'Data Template', queue_key: 'queue:data_template' },
  { id: 'ai_monitor', label: 'Monitor Generation', queue_key: 'queue:ai_monitor' },
  { id: 'diff_evaluate', label: 'Diff Evaluation', queue_key: 'queue:diff_evaluate' },
  { id: 'diagnose_failure', label: 'Failure Diagnosis', queue_key: 'queue:diagnose_failure' },
  { id: 'alert_analyze', label: 'Alert Analysis', queue_key: 'queue:alert_analyze' },
])
const queueStatus = ref({ queues: {}, dlq: {} })
const aiJobs = ref([])
const aiOpsLoading = ref(false)
const aiJobRetrying = ref('')
const aiJobStatusFilter = ref('')
const aiJobStatusOptions = ['queued', 'running', 'retry', 'pending_review', 'done', 'dlq', 'failed']

const queueNameI18n = {
  ai_analyze: 'dashboard.queue_ai_analyze',
  doc: 'dashboard.queue_ai_doc',
  ai_analyze_doc: 'dashboard.queue_ai_doc',
  asserts: 'dashboard.queue_ai_asserts',
  ai_analyze_asserts: 'dashboard.queue_ai_asserts',
  scenario: 'dashboard.queue_ai_scenario',
  ai_scenario: 'dashboard.queue_ai_scenario',
  data_template: 'dashboard.queue_data_template',
  monitor: 'dashboard.queue_ai_monitor',
  ai_monitor: 'dashboard.queue_ai_monitor',
  diff: 'dashboard.queue_diff_evaluate',
  diff_evaluate: 'dashboard.queue_diff_evaluate',
  diagnose: 'dashboard.queue_diagnose_failure',
  diagnose_failure: 'dashboard.queue_diagnose_failure',
  alert: 'dashboard.queue_alert_analyze',
  alert_analyze: 'dashboard.queue_alert_analyze',
}

const aiQueueRows = computed(() => {
  const queues = queueStatus.value.queues || {}
  return queueDefinitions.value.map(def => {
    const row = queues[def.queue_key] || {}
    return {
      ...def,
      pending: row.pending ?? 0,
      dlq: row.dlq ?? 0,
      recent_dlq: row.recent_dlq || [],
      error: row.error || '',
    }
  })
})

const aiJobStatusBadges = computed(() => {
  const counts = {}
  for (const job of aiJobs.value) {
    const status = job.status || 'unknown'
    counts[status] = (counts[status] || 0) + 1
  }
  return Object.entries(counts).map(([status, count]) => ({ status, count }))
})

const dlqQueueOptions = computed(() => queueDefinitions.value)

function queueDisplayName(row) {
  const id = row?.id || row?.queue
  const key = queueNameI18n[id]
  return key ? t(key) : (row?.label || id || '—')
}

function queueRecentError(row) {
  if (row.error) return row.error
  const first = (row.recent_dlq || [])[0]
  if (!first) return '—'
  return first.error || first.raw || first.job_id || '—'
}

// 将原始 job_status 值映射到对应的 i18n key 并翻译
function jobStatusLabel(status) {
  const key = `settings.job_status_${status}`
  return t(key) !== key ? t(key) : (status || '—')
}

function statusTagType(status) {
  const map = {
    queued: 'info',
    running: 'warning',
    retry: 'warning',
    pending_review: 'primary',
    done: 'success',
    accepted: 'success',
    applied: 'success',
    dlq: 'danger',
    failed: 'danger',
  }
  return map[status] || 'info'
}

async function loadAiOps() {
  aiOpsLoading.value = true
  try {
    const [queueRes, jobRes] = await Promise.all([
      systemApi.queues(),
      aiJobApi.list({
        project_id: projectStore.current,
        limit: 100,
        status: aiJobStatusFilter.value || undefined,
      }),
    ])
    queueStatus.value = queueRes || { queues: {}, dlq: {} }
    if (queueRes?.definitions?.length) queueDefinitions.value = queueRes.definitions
    if (jobRes?.definitions?.length) queueDefinitions.value = jobRes.definitions
    aiJobs.value = jobRes.items || []
  } catch (e) {
    toast.error(e.message || t('settings.job_load_failed'))
  } finally {
    aiOpsLoading.value = false
  }
}

function viewQueueDlq(row) {
  selectedDlqQueue.value = row.id
  activeTab.value = 'dlq'
  loadDlq()
}

async function retryAiJob(row) {
  if (!row?.job_id) return
  aiJobRetrying.value = row.job_id
  try {
    await aiJobApi.retry(row.job_id)
    toast.success(t('settings.dlq_toast_retried'))
    await loadAiOps()
  } catch (e) {
    toast.error(e.message || t('settings.dlq_toast_retry_failed'))
  } finally {
    aiJobRetrying.value = ''
  }
}

// prompt 任务类型选项：label 为 i18n key，通过 promptTaskLabel() 翻译
const promptTaskOptions = [
  { value: 'doc', label: 'settings.prompt_task_doc' },
  { value: 'asserts', label: 'settings.prompt_task_asserts' },
  { value: 'scenario', label: 'settings.prompt_task_scenario' },
  { value: 'data_template', label: 'settings.prompt_task_data_template' },
  { value: 'monitor', label: 'settings.prompt_task_monitor' },
  { value: 'diff_eval', label: 'settings.prompt_task_diff_eval' },
  { value: 'diagnose', label: 'settings.prompt_task_diagnose' },
  { value: 'extract', label: 'settings.prompt_task_extract' },
  { value: 'alert', label: 'settings.prompt_task_alert' },
]
const promptTaskType = ref('')
const promptItems = ref([])
const promptLoading = ref(false)
const promptSaving = ref(false)
const showPromptDialog = ref(false)
const showPromptPreview = ref(false)
const showPromptDiff = ref(false)
const promptPreviewTitle = ref('')
const promptPreviewContent = ref('')
const promptDiffTitle = ref('')
const promptDiffActive = ref('')
const promptDiffSelected = ref('')
const promptForm = ref(defaultPromptForm())

function defaultPromptForm() {
  return { task_type: promptTaskType.value || 'doc', name: '', description: '', content: '', activate: false }
}

// 根据 task_type 返回翻译后的标签名
function promptTaskLabel(taskType) {
  const opt = promptTaskOptions.find(o => o.value === taskType)
  return opt ? t(opt.label) : (taskType || '—')
}

async function loadPrompts() {
  promptLoading.value = true
  try {
    const res = await promptApi.list({ task_type: promptTaskType.value || undefined, limit: 200 })
    promptItems.value = res.items || []
  } catch (e) {
    toast.error(e.message || t('settings.prompt_load_failed'))
  } finally {
    promptLoading.value = false
  }
}

function openPromptDialog() {
  promptForm.value = defaultPromptForm()
  showPromptDialog.value = true
}

async function savePrompt() {
  promptSaving.value = true
  try {
    await promptApi.create({
      task_type: promptForm.value.task_type,
      name: promptForm.value.name || 'custom',
      description: promptForm.value.description,
      content: promptForm.value.content,
      activate: !!promptForm.value.activate,
    })
    toast.success(t('settings.prompt_saved'))
    showPromptDialog.value = false
    promptTaskType.value = promptForm.value.task_type
    await loadPrompts()
  } catch (e) {
    toast.error(e.message)
  } finally {
    promptSaving.value = false
  }
}

async function activatePrompt(row) {
  try {
    await promptApi.activate(row.id)
    toast.success(t('settings.prompt_activated'))
    await loadPrompts()
  } catch (e) {
    toast.error(e.message)
  }
}

async function resetPromptTask() {
  if (!promptTaskType.value) return
  try {
    await ElMessageBox.confirm(
      t('settings.prompt_confirm_reset', { task: promptTaskType.value }),
      t('common.confirm'),
      { type: 'warning' },
    )
    await promptApi.reset(promptTaskType.value)
    toast.success(t('settings.prompt_reset_done'))
    await loadPrompts()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close' && e.message) toast.error(e.message)
  }
}

async function viewPrompt(row) {
  try {
    const detail = await promptApi.get(row.id)
    promptPreviewTitle.value = `${promptTaskLabel(detail.task_type)} v${detail.version || row.version}`
    promptPreviewContent.value = detail.content || ''
    showPromptPreview.value = true
  } catch (e) {
    toast.error(e.message || t('settings.prompt_load_failed'))
  }
}

async function viewPromptDiff(row) {
  try {
    const selected = await promptApi.get(row.id)
    const activeRes = await promptApi.list({ task_type: row.task_type, active_only: true, limit: 1 })
    const active = activeRes.items?.[0]
    let activeContent = ''
    if (active?.id) {
      activeContent = (await promptApi.get(active.id)).content || ''
    }
    promptDiffTitle.value = `${promptTaskLabel(row.task_type)} v${selected.version || row.version}`
    promptDiffActive.value = activeContent || t('settings.prompt_no_active')
    promptDiffSelected.value = selected.content || ''
    showPromptDiff.value = true
  } catch (e) {
    toast.error(e.message || t('settings.prompt_load_failed'))
  }
}

// ── 死信队列管理 ──────────────────────────────────────────
const dlqItems = ref([])
const dlqLoading = ref(false)
const dlqRetrying = ref(null)
const dlqRemoving = ref(null)
const selectedDlqQueue = ref('')  // 默认为空，展示全部死信队列

async function loadDlq() {
  dlqLoading.value = true
  try {
    if (selectedDlqQueue.value) {
      // 指定队列：直接请求
      const res = await dlqApi.list(selectedDlqQueue.value)
      if (res.definitions?.length) queueDefinitions.value = res.definitions
      dlqItems.value = res.items || []
    } else {
      // 全部队列：并行请求所有队列 DLQ 后合并
      const results = await Promise.all(
        dlqQueueOptions.value.map(q =>
          dlqApi.list(q.id).catch(() => ({ items: [], definitions: [] }))
        )
      )
      dlqItems.value = results.flatMap(r => r.items || [])
      const defs = results.flatMap(r => r.definitions || [])
      if (defs.length) queueDefinitions.value = defs
    }
  } catch (e) {
    toast.error(e.message || t('settings.dlq_toast_load_failed'))
  } finally {
    dlqLoading.value = false
  }
}

// retry/remove 直接使用条目自带的 queue + index，避免合并模式下标错乱
function _dlqKey(row) {
  return row.queue + ':' + row.index
}

async function retryDlq(row) {
  const key = _dlqKey(row)
  dlqRetrying.value = key
  try {
    await dlqApi.retry(row.index, row.queue)
    toast.success(t('settings.dlq_toast_retried'))
    await loadDlq()
  } catch (e) {
    toast.error(e.message || t('settings.dlq_toast_retry_failed'))
  } finally {
    dlqRetrying.value = null
  }
}

async function removeDlq(row) {
  const key = _dlqKey(row)
  dlqRemoving.value = key
  try {
    await dlqApi.remove(row.index, row.queue)
    toast.success(t('settings.dlq_toast_removed'))
    await loadDlq()
  } catch (e) {
    toast.error(e.message || t('settings.dlq_toast_remove_failed'))
  } finally {
    dlqRemoving.value = null
  }
}

function dlqTarget(row) {
  return row.job_id || row.api_id || row.template_id || row.diff_id || row.execution_id || row.alert_id || row.scenario_id || '—'
}

// 切换到 DLQ/Projects/告警渠道 tab 时自动加载对应数据
function onTabChange(tab) {
  if (tab === 'ai-jobs') loadAiOps()
  if (tab === 'prompts') loadPrompts()
  if (tab === 'dlq') loadDlq()
  if (tab === 'database') loadDatabaseTab()
  if (tab === 'projects') loadProjects()
  if (tab === 'alert-channels') loadChannels()
  if (tab === 'general') loadGeneralSettings()
}

// ── 数据库服务与 SQL 片段 ────────────────────────────────
const dbServices = ref([])
const sqlSnippets = ref([])
const dbLoading = ref(false)
const snippetLoading = ref(false)
const showDbDialog = ref(false)
const showSnippetDialog = ref(false)
const dbEditingId = ref('')
const snippetEditingId = ref('')
const sqlRunResult = ref(null)
const snippetRunParams = ref('{}')
const dbTestState = ref({})
const snippetSearch = ref('')
const snippetServiceFilter = ref('')
const dbForm = ref(defaultDbForm())
const snippetForm = ref(defaultSnippetForm())

const filteredSqlSnippets = computed(() => {
  const keyword = snippetSearch.value.trim().toLowerCase()
  return sqlSnippets.value.filter(item => {
    const matchService = !snippetServiceFilter.value || item.db_service_id === snippetServiceFilter.value
    const text = `${item.name || ''} ${item.sql || ''} ${(item.tags || []).join(' ')}`.toLowerCase()
    return matchService && (!keyword || text.includes(keyword))
  })
})

function defaultDbForm() {
  return { name: '', type: 'postgresql', host: 'localhost', port: 5432, database: '', username: '', password: '', enabled: true, read_only: true, timeout_ms: 5000, max_rows: 100 }
}
function defaultSnippetForm() {
  return { name: '', db_service_id: '', sql: 'SELECT 1 AS ok', enabled: true, timeout_ms: 5000, max_rows: 100, params_schema: {}, tags: [], description: '' }
}
function resetDbForm() {
  dbEditingId.value = ''
  dbForm.value = defaultDbForm()
}
function resetSnippetForm() {
  snippetEditingId.value = ''
  snippetForm.value = { ...defaultSnippetForm(), db_service_id: dbServices.value[0]?.id || '' }
  sqlRunResult.value = null
}
function openDbDialog(row = null) {
  if (row) {
    dbEditingId.value = row.id
    dbForm.value = { ...defaultDbForm(), ...row, password: '' }
  } else {
    resetDbForm()
  }
  showDbDialog.value = true
}
function openSnippetDialog(row = null) {
  if (row) {
    snippetEditingId.value = row.id
    snippetForm.value = { ...defaultSnippetForm(), ...row }
    sqlRunResult.value = null
  } else {
    resetSnippetForm()
  }
  showSnippetDialog.value = true
}
function serviceName(id) {
  return dbServices.value.find(s => s.id === id)?.name || id || '—'
}
async function loadDatabaseTab() {
  dbLoading.value = true
  snippetLoading.value = true
  try {
    const [svcRes, snipRes] = await Promise.all([
      databaseServiceApi.list(projectStore.current),
      sqlSnippetApi.list({ project_id: projectStore.current }),
    ])
    dbServices.value = svcRes.items || []
    sqlSnippets.value = snipRes.items || []
    if (!snippetForm.value.db_service_id && dbServices.value[0]) snippetForm.value.db_service_id = dbServices.value[0].id
  } catch (e) {
    toast.error(e.message || t('settings.db_load_failed'))
  } finally {
    dbLoading.value = false
    snippetLoading.value = false
  }
}
function editDbService(row) {
  dbEditingId.value = row.id
  dbForm.value = { ...defaultDbForm(), ...row, password: '' }
}
async function saveDbService() {
  const payload = { ...dbForm.value, project_id: projectStore.current }
  try {
    if (dbEditingId.value) await databaseServiceApi.update(dbEditingId.value, payload)
    else await databaseServiceApi.create(payload)
    toast.success(t('settings.db_saved'))
    showDbDialog.value = false
    resetDbForm()
    await loadDatabaseTab()
  } catch (e) {
    toast.error(e.message)
  }
}
async function runDbTest(row) {
  try {
    const result = await databaseServiceApi.test(row.id)
    sqlRunResult.value = result
    dbTestState.value[row.id] = {
      ok: result.ok !== false && !result.error,
      duration_ms: result.duration_ms || 0,
      error: result.error || result.message || '',
    }
    if (result.ok === false || result.error) toast.error(result.message || t('settings.db_test_failed'))
    else toast.success(t('settings.db_test_success'))
  } catch (e) {
    dbTestState.value[row.id] = { ok: false, error: e.message || t('settings.db_test_failed') }
    toast.error(e.message || t('settings.db_test_failed'))
  }
}
async function testDbService() {
  if (!dbEditingId.value) return
  await runDbTest({ id: dbEditingId.value })
}
async function deleteDbService(row) {
  await ElMessageBox.confirm(t('settings.db_confirm_delete', { name: row.name }), t('common.confirm'), { type: 'warning' })
  await databaseServiceApi.remove(row.id)
  toast.success(t('settings.db_deleted'))
  await loadDatabaseTab()
}
function editSnippet(row) {
  snippetEditingId.value = row.id
  snippetForm.value = { ...defaultSnippetForm(), ...row }
  sqlRunResult.value = null
}
async function saveSnippet() {
  try {
    const payload = { ...snippetForm.value, project_id: projectStore.current }
    if (snippetEditingId.value) await sqlSnippetApi.update(snippetEditingId.value, payload)
    else await sqlSnippetApi.create(payload)
    toast.success(t('settings.sql_saved'))
    showSnippetDialog.value = false
    resetSnippetForm()
    await loadDatabaseTab()
  } catch (e) {
    toast.error(e.message)
  }
}
async function validateSnippet() {
  try {
    const params = snippetRunParams.value.trim() ? JSON.parse(snippetRunParams.value) : {}
    sqlRunResult.value = await sqlApi.validate({
      project_id: projectStore.current,
      db_service_id: snippetForm.value.db_service_id,
      sql_text: snippetForm.value.sql,
      params,
    })
    toast.success('SQL 校验通过')
  } catch (e) {
    toast.error(e.message || 'SQL 校验失败')
  }
}
async function runSnippet() {
  if (!snippetEditingId.value) return
  try {
    const params = snippetRunParams.value.trim() ? JSON.parse(snippetRunParams.value) : {}
    sqlRunResult.value = await sqlSnippetApi.run(snippetEditingId.value, { params })
  } catch (e) {
    toast.error(e.message || t('settings.sql_run_failed'))
  }
}
async function copySnippetId(row) {
  try {
    await navigator.clipboard?.writeText(row.id)
    toast.success('已复制片段 ID')
  } catch {
    toast.info(row.id)
  }
}
async function deleteSnippet(row) {
  await ElMessageBox.confirm(t('settings.sql_confirm_delete', { name: row.name }), t('common.confirm'), { type: 'warning' })
  await sqlSnippetApi.remove(row.id)
  toast.success(t('settings.sql_deleted'))
  await loadDatabaseTab()
}

// ── 语言切换（通用 tab） ──────────────────────────────
const { locale } = useI18n()

function onLangChange(val) {
  locale.value = val
  localStorage.setItem('apipulse-lang', val)
}

// ── 通用设置开关（auto_trigger_ai / auto_review_flow / nav_auto_collapse） ──
const autoTriggerAi = ref(true)
const autoReviewFlow = ref(true)
// 导航栏自动收起：纯客户端偏好，存 localStorage，默认开启
const navAutoCollapse = ref(localStorage.getItem('apipulse-nav-auto-collapse') !== 'false')

async function loadGeneralSettings() {
  try {
    const res = await settingsApi.getGeneral()
    autoTriggerAi.value = res.auto_trigger_ai ?? true
    autoReviewFlow.value = res.auto_review_flow ?? true
  } catch { /* 加载失败保持默认值 */ }
}

async function onAutoTriggerChange(val) {
  try {
    await settingsApi.saveGeneral({ auto_trigger_ai: val, auto_review_flow: autoReviewFlow.value })
  } catch (e) {
    toast.error(e.message || t('common.saveFailed'))
    // 保存失败回滚开关状态
    autoTriggerAi.value = !val
  }
}

async function onAutoReviewFlowChange(val) {
  try {
    await settingsApi.saveGeneral({ auto_review_flow: val, auto_trigger_ai: autoTriggerAi.value })
  } catch (e) {
    toast.error(e.message || t('common.saveFailed'))
    // 保存失败回滚开关状态
    autoReviewFlow.value = !val
  }
}

// 导航栏自动收起：写入 localStorage，App.vue route watcher 中读取
function onNavAutoCollapseChange(val) {
  localStorage.setItem('apipulse-nav-auto-collapse', val ? 'true' : 'false')
}

// ── 项目管理 ──────────────────────────────────────────
const projects = ref([])
const projectLoading = ref(false)
const showProjectDialog = ref(false)
const editingProjectId = ref(null)
const savingProject = ref(false)
// 需求1: 域名字段从 App.vue 迁移至此，含 tag 预览
const parsedProjectAllowlist = computed(() => {
  if (!projectForm.value.domain_allowlist_str) return []
  return projectForm.value.domain_allowlist_str.split(',').map(s => s.trim()).filter(Boolean)
})
const parsedProjectBlocklist = computed(() => {
  if (!projectForm.value.domain_blocklist_str) return []
  return projectForm.value.domain_blocklist_str.split(',').map(s => s.trim()).filter(Boolean)
})

const projectForm = ref({
  name: '',
  slug: '',
  description: '',
  domain_allowlist_str: '',
  domain_blocklist_str: '',
})

function resetProjectForm() {
  projectForm.value = { name: '', slug: '', description: '', domain_allowlist_str: '', domain_blocklist_str: '' }
  editingProjectId.value = null
}

function openProjectDialog(project) {
  if (project) {
    editingProjectId.value = project.id
    projectForm.value = {
      name: project.name,
      slug: project.slug,
      description: project.description || '',
      // 需求1: 编辑时填充域名字段——数组转为逗号分隔字符串
      domain_allowlist_str: (project.domain_allowlist || []).join(', '),
      domain_blocklist_str: (project.domain_blocklist || []).join(', '),
    }
  } else {
    resetProjectForm()
  }
  showProjectDialog.value = true
}

async function loadProjects() {
  projectLoading.value = true
  try {
    await projectStore.load()
    projects.value = projectStore.projects || []
  } catch (e) {
    toast.error(e.message || t('common.loadProjectFailed'))
  } finally {
    projectLoading.value = false
  }
}

async function saveProject() {
  if (!projectForm.value.name || !projectForm.value.slug) return
  savingProject.value = true
  try {
    const payload = {
      name: projectForm.value.name,
      slug: projectForm.value.slug,
      description: projectForm.value.description,
      // 需求1: 域名字段——逗号分隔字符串转数组，支持白名单/黑名单过滤
      domain_allowlist: projectForm.value.domain_allowlist_str
        ? projectForm.value.domain_allowlist_str.split(',').map(s => s.trim()).filter(Boolean)
        : [],
      domain_blocklist: projectForm.value.domain_blocklist_str
        ? projectForm.value.domain_blocklist_str.split(',').map(s => s.trim()).filter(Boolean)
        : [],
    }
    if (editingProjectId.value) {
      await projectApi.update(editingProjectId.value, payload)
      toast.success(t('common.projectUpdated'))
    } else {
      await projectStore.create(payload)
      toast.success(t('common.projectCreated'))
    }
    showProjectDialog.value = false
    resetProjectForm()
    await loadProjects()
  } catch (e) {
    toast.error(e.message || t('common.saveProjectFailed'))
  } finally {
    savingProject.value = false
  }
}

// ── 告警渠道管理（从 alert-channels/Index.vue 迁移至此） ──
const channels = ref([])
const channelsLoading = ref(false)
const channelModal = ref(false)
const editingChannelId = ref(null)

const defaultChannelForm = () => ({ name: '', type: 'dingtalk', url: '', enabled: true })
const channelForm = ref(defaultChannelForm())

async function loadChannels() {
  channelsLoading.value = true
  try {
    const res = await alertChannelApi.list({ project_id: projectStore.current })
    channels.value = res.items || []
  } catch (e) {
    toast.error(e.message || t('alert_channels.load_failed'))
    channels.value = []
  } finally {
    channelsLoading.value = false
  }
}

function openCreate() {
  editingChannelId.value = null
  channelForm.value = defaultChannelForm()
  channelModal.value = true
}

function editChannel(row) {
  editingChannelId.value = row.id
  channelForm.value = { name: row.name, type: row.type, url: row.url, enabled: row.enabled }
  channelModal.value = true
}

function resetForm() {
  channelForm.value = defaultChannelForm()
  editingChannelId.value = null
}

async function submit() {
  try {
    const payload = { ...channelForm.value, project_id: projectStore.current }
    if (editingChannelId.value) {
      await alertChannelApi.update(editingChannelId.value, payload)
      toast.success(t('alert_channels.toast_updated'))
    } else {
      await alertChannelApi.create(payload)
      toast.success(t('alert_channels.toast_created'))
    }
    channelModal.value = false
    await loadChannels()
  } catch (e) {
    toast.error(e.message)
  }
}

async function toggleEnabled(row) {
  try {
    await alertChannelApi.update(row.id, { ...row, enabled: !row.enabled })
    toast.success(row.enabled ? t('alert_channels.toast_disabled') : t('alert_channels.toast_enabled'))
    await loadChannels()
  } catch (e) { toast.error(e.message) }
}

async function deleteChannel(row) {
  try {
    await ElMessageBox.confirm(t('alert_channels.confirm_delete', { name: row.name }), t('common.confirm'), { type: 'warning' })
    await alertChannelApi.remove(row.id)
    toast.success(t('alert_channels.toast_deleted'))
    await loadChannels()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close' && e.message) toast.error(e.message)
  }
}

// 渠道类型展示辅助
function typeLabel(type) {
  const map = { dingtalk: '钉钉', wechat: '企微', slack: 'Slack', custom: t('alert_channels.type_custom') }
  return map[type] || type
}
function typeTagType(type) {
  const map = { dingtalk: 'primary', wechat: 'success', slack: 'warning', custom: 'info' }
  return map[type] || 'info'
}

async function deleteProject(id) {
  try {
    await projectApi.remove(id)
    toast.success(t('common.projectDeleted'))
    await loadProjects()
    // 如果删除的是当前选中项目，重新选择
    if (projectStore.current === id) {
      await projectStore.load()
      if (projectStore.projects.length) {
        projectStore.select(projectStore.projects[0].id)
      }
    }
  } catch (e) {
    toast.error(e.message || t('common.saveProjectFailed'))
  }
}

onMounted(() => {
  loadConfig()
  loadEnvironments()
  // 需求1: 支持通过 URL query ?tab=projects 直接跳转到项目管理标签页
  if (route.query.tab === 'projects') {
    activeTab.value = 'projects'
    loadProjects()
  }
  // 需求: 支持通过 ?tab=alert-channels 深度链接告警渠道管理
  if (route.query.tab === 'alert-channels') {
    activeTab.value = 'alert-channels'
    loadChannels()
  }
})
</script>

<style scoped>
.settings-layout {
  display: grid;
  grid-template-columns: 1fr 320px;
  gap: 12px;
}
.settings-stack {
  display: grid;
  gap: 12px;
}
.prompt-preview {
  max-height: 520px;
  overflow: auto;
  white-space: pre-wrap;
}
.prompt-diff-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.task-route-list {
  display: grid;
  gap: 8px;
}
.task-route-row {
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-soft);
}
.task-route-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 28px;
}
.task-route-grid {
  display: grid;
  grid-template-columns: 120px 1.2fr 1fr 110px 120px 120px;
  gap: 8px;
  margin-top: 8px;
}

.provider-item {
  padding: 12px 0;
  border-bottom: 1px solid var(--border);
}
.provider-item:last-child { border-bottom: none; }
.provider-name {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 2px;
}

/* JSON 输入错误高亮 */
.input-error :deep(textarea),
.input-error :deep(input) {
  border-color: var(--red) !important;
}
.field-error {
  color: var(--red);
  font-size: 11px;
  margin-top: 4px;
  display: inline-block;
}
.db-hint { margin-bottom: 12px; }

/* 需求1: 域名 tag 预览样式，从 App.vue 迁移 */
.domain-tags { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }

@media (max-width: 900px) {
  .settings-layout {
    grid-template-columns: 1fr;
  }
  .form-grid {
    grid-template-columns: 1fr;
  }
  .prompt-diff-grid {
    grid-template-columns: 1fr;
  }
  .task-route-grid {
    grid-template-columns: 1fr;
  }
}

/* 告警渠道样式（从 alert-channels/Index.vue 迁移至此） */
.page-container { padding: 20px; max-width: 1200px; margin: 0 auto; }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.page-header h2 { margin: 0; font-size: 18px; }
</style>
