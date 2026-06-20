<template>
  <div class="page">
    <div class="page-header">
      <div>
        <div class="page-title">{{ $t('monitor.title') }}</div>
        <div class="page-subtitle">{{ $t('monitor.subtitle', { active: activeCount }) }}</div>
      </div>
      <div class="flex items-center gap-8">
        <el-button size="small" @click="showAiGenerateModal = true">{{ $t('monitor.ai_generate_btn') }}</el-button>
        <el-button type="primary" size="small" @click="showCreateModal = true">{{ $t('monitor.new_btn') }}</el-button>
      </div>
    </div>

    <div class="page-body">
      <el-tabs v-model="activeTab">
        <el-tab-pane :label="$t('monitor.tab_tasks')" name="tasks" />
        <el-tab-pane name="alerts">
          <template #label>
            {{ $t('monitor.tab_alerts') }}
            <el-tag v-if="alertTotal" type="danger" size="small" style="margin-left:6px">{{ alertTotal }}</el-tag>
          </template>
        </el-tab-pane>
      </el-tabs>

      <!-- 监控任务列表 -->
      <div v-if="activeTab==='tasks'">
        <el-card style="padding:0">
        <el-table
          :data="monitors"
          v-loading="loadingInit"
          :empty-text="$t('monitor.no_monitors')"
        >
          <el-table-column :label="$t('common.status')" width="70">
            <template #default="{ row }">
              <div class="flex items-center gap-4">
                <span :class="row.enabled ? 'dot dot-green' : 'dot dot-gray'" :title="row.enabled ? $t('common.enabled') : $t('common.disabled')"></span>
                <!-- P1: 静默期徽标（用户知道该监控正在静默窗口内） -->
                <el-tag v-if="isSilenced(row)" type="info" size="small" effect="plain" :title="$t('monitor.silenced_until', { time: fmt.time(row.silence_until) })">
                  🔇
                </el-tag>
              </div>
            </template>
          </el-table-column>
          <el-table-column :label="$t('common.name')" min-width="140">
            <template #default="{ row }">
              <span style="font-weight:500">{{ row.name }}</span>
              <el-tag v-if="row.source === 'ai_generated'" size="small" type="success" style="margin-left:6px">AI</el-tag>
              <!-- P1: fail_streak 展示（从 statsMap 读取） -->
              <div v-if="statsMap[row.id]?.fail_streak > 0" class="text-3" style="font-size:11px;color:var(--red)">
                {{ $t('monitor.fail_streak', { n: statsMap[row.id]?.fail_streak }) }}
              </div>
            </template>
          </el-table-column>
          <!-- 项目列：展示巡检所属项目，支持跨项目审计 -->
          <el-table-column :label="$t('common.project')" width="100">
            <template #default="{ row }">
              <span class="text-2" style="font-size:12px">{{ projectStore.getName(row.project_id) || row.project_id || '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('monitor.col_target')" min-width="150">
            <template #default="{ row }">
              <!-- P1: 目标名称解析（此前只显示截断 UUID），可点击跳转 -->
              <div class="flex items-center gap-4">
                <el-tag size="small" :type="row.target_type === 'scenario' ? 'warning' : row.target_type === 'data_factory' ? 'success' : 'info'">{{ row.target_type || 'api' }}</el-tag>
                <el-button link type="primary" size="small" class="mono" style="font-size:11px;padding:0" @click="goTarget(row)">
                  {{ targetLabel(row) }}
                </el-button>
              </div>
            </template>
          </el-table-column>
          <el-table-column :label="$t('monitor.col_interval')" width="80">
            <template #default="{ row }">
              <span class="mono text-2" style="font-size:11px">{{ row.cron || row.interval }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('monitor.col_risk')" width="90">
            <template #default="{ row }">
              <el-tag :type="riskTagType(row.risk_level)" size="small">{{ row.risk_level }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="$t('monitor.col_alert_channels')" width="90">
            <template #default="{ row }">
              <span class="text-2" style="font-size:11px">{{ $t('monitor.channels_count', { n: row.alert_channels?.length || 0 }) }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('monitor.col_consecutive_fail')" width="80">
            <template #default="{ row }">
              <el-tag v-if="statsMap[row.id]?.fail_streak > 0" type="danger" size="small">
                {{ statsMap[row.id].fail_streak }}
              </el-tag>
              <span v-else class="text-3">—</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('common.actions')" width="300">
            <template #default="{ row }">
              <div class="flex items-center gap-8">
                <el-tag v-if="runJobs[row.id]" size="small" :type="runJobs[row.id].status === 'passed' ? 'success' : runJobs[row.id].status === 'failed' ? 'danger' : 'warning'">
                  {{ runJobs[row.id].status }}
                </el-tag>
                <el-button
                  :type="row.enabled ? 'danger' : 'success'"
                  size="small"
                  @click="toggleMonitor(row)"
                >
                  {{ row.enabled ? $t('monitor.disable') : $t('monitor.enable') }}
                </el-button>
                <el-button size="small" @click="editMonitor(row)">{{ $t('monitor.edit') }}</el-button>
                <el-button size="small" @click="validateExisting(row)">{{ $t('monitor.validate_btn') }}</el-button>
                <!-- P0-3: 手动立即试跑，验证监控配置正确性（无需等调度） -->
                <el-button size="small" :loading="runningId === row.id" @click="runMonitorNow(row)">{{ $t('monitor.run_now') }}</el-button>
                <el-button type="danger" size="small" :icon="Close" @click="deleteMonitor(row)" />
              </div>
            </template>
          </el-table-column>
        </el-table>
        </el-card>
      </div>

      <!-- 告警历史 -->
      <div v-if="activeTab==='alerts'">
        <div class="alert-filters">
          <el-select v-model="alertFilter.risk_level" @change="loadAlerts" :placeholder="$t('monitor.filter_all_levels')" size="small" style="width:130px">
            <el-option :label="$t('monitor.filter_all_levels')" value="" />
            <el-option v-for="r in ['critical','high','medium','low']" :key="r" :label="$t('monitor.risk_level_'+r)" :value="r" />
          </el-select>
          <el-select v-model="alertFilter.is_recovery" @change="loadAlerts" :placeholder="$t('monitor.filter_all_types')" size="small" style="width:130px">
            <el-option :label="$t('monitor.filter_all_types')" value="" />
            <el-option :label="$t('monitor.type_alert')" value="false" />
            <el-option :label="$t('monitor.type_recovery')" value="true" />
          </el-select>
          <!-- P0-2: AI 评估筛选（按 ai_severity 过滤，noise 可一键过滤误报） -->
          <el-select v-model="alertFilter.ai_severity" @change="loadAlerts" :placeholder="$t('monitor.filter_all_ai', '全部评估')" size="small" style="width:130px" clearable>
            <el-option :label="$t('monitor.ai_noise', '误报')" value="noise" />
            <el-option :label="$t('monitor.ai_low', '低')" value="low" />
            <el-option :label="$t('monitor.ai_medium', '中')" value="medium" />
            <el-option :label="$t('monitor.ai_high', '高')" value="high" />
            <el-option :label="$t('monitor.ai_critical', '严重')" value="critical" />
          </el-select>
        </div>

        <el-card style="padding:0">
        <el-table
          :data="alerts"
          :empty-text="$t('monitor.no_alerts')"
        >
          <el-table-column :label="$t('common.type')" width="80">
            <template #default="{ row }">
              <el-tag :type="row.is_recovery ? 'success' : 'danger'" size="small">
                {{ row.is_recovery ? $t('monitor.type_recovery') : $t('monitor.type_alert') }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="$t('monitor.col_level')" width="90">
            <template #default="{ row }">
              <!-- P2: 风险等级用 i18n（此前直接显示英文原值） -->
              <el-tag :type="riskTagType(row.risk_level)" size="small">{{ $t('monitor.risk_level_' + row.risk_level) }}</el-tag>
            </template>
          </el-table-column>
          <!-- P0-2: AI 降噪评估列 —— 展示 ai_severity/ai_root_cause（后端已回写但前端此前未展示） -->
          <el-table-column :label="$t('monitor.col_ai_severity', 'AI 评估')" width="100">
            <template #default="{ row }">
              <el-tag v-if="row.ai_severity" :type="aiSeverityTagType(row.ai_severity)" size="small" effect="plain">
                {{ aiSeverityLabel(row.ai_severity) }}
              </el-tag>
              <span v-else class="text-3" style="font-size:11px">—</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('monitor.col_alert_title')" min-width="200">
            <template #default="{ row }">
              <span style="font-weight:500" class="truncate">{{ row.title }}</span>
              <!-- P1: AI 根因摘要（若有）显示在标题下方 -->
              <div v-if="row.ai_root_cause" class="text-3" style="font-size:11px;margin-top:2px">{{ row.ai_root_cause }}</div>
            </template>
          </el-table-column>
          <el-table-column :label="$t('monitor.col_detail')" min-width="160">
            <template #default="{ row }">
              <!-- P1: 详情用 popover 展开完整 message（此前粗暴截断只取第一行） -->
              <el-popover trigger="hover" :width="360" placement="left">
                <template #reference>
                  <span class="text-2 truncate" style="font-size:12px;cursor:pointer;color:var(--brand)">{{ (row.message?.split('\n')[1]) || row.message?.split('\n')[0] || '' }}</span>
                </template>
                <pre style="font-size:11px;white-space:pre-wrap;word-break:break-word;margin:0">{{ row.message }}</pre>
              </el-popover>
              <!-- P1: 跳转执行详情（若有 execution_id） -->
              <div v-if="row.execution_id" style="margin-top:4px">
                <el-button link type="primary" size="small" @click="goExecution(row.execution_id)">
                  {{ $t('monitor.view_execution', '查看执行详情') }} →
                </el-button>
              </div>
            </template>
          </el-table-column>
          <el-table-column :label="$t('monitor.col_channels')" width="70">
            <template #default="{ row }">
              <span class="mono text-2">{{ row.channels?.length || 0 }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('common.time')" width="130">
            <template #default="{ row }">
              <span class="text-2">{{ fmt.fromNow(row.sent_at) }}</span>
            </template>
          </el-table-column>
        </el-table>

        <AppPagination v-model:page="alertPage" :page-size="alertPageSize" :total="alertTotal" @page-change="loadAlerts" />
        </el-card>
      </div>
    </div>

    <!-- 新建/编辑 dialog -->
    <el-dialog v-model="showCreateModal" :title="editingId ? $t('monitor.dialog_edit') : $t('monitor.new')" width="600px" @close="closeModal">
      <el-form label-position="top">
        <div style="display:flex;gap:12px">
          <el-form-item :label="$t('monitor.form_name_required')" style="flex:1">
            <el-input v-model="form.name" :placeholder="$t('monitor.placeholder_name')" />
          </el-form-item>
          <!-- 项目选择器：新建巡检时必须指定所属项目 -->
          <el-form-item :label="$t('common.project')" style="flex:1">
            <el-select v-model="form.project_id" style="width:100%">
              <el-option v-for="p in projectStore.projects" :key="p.id" :value="p.id" :label="p.name" />
            </el-select>
          </el-form-item>
        </div>
        <div style="display:flex;gap:12px">
          <el-form-item :label="$t('monitor.form_target_type')" style="flex:1">
            <el-select v-model="form.target_type" style="width:100%" @change="onTargetTypeChange">
              <el-option :label="$t('monitor.form_target_type_api')" value="api" />
              <el-option :label="$t('monitor.form_target_type_scenario')" value="scenario" />
              <el-option :label="$t('monitor.form_target_type_data_factory')" value="data_factory" />
            </el-select>
          </el-form-item>
        </div>
        <!-- 根据 target_type 动态显示目标输入 -->
        <el-form-item :label="$t('monitor.form_target_id')" v-if="form.target_type === 'api'">
          <el-select
            v-model="form.api_id"
            filterable
            remote
            :remote-method="searchTargetApis"
            :loading="false"
            style="width:100%"
            :placeholder="$t('monitor.form_target_id_placeholder')"
          >
            <el-option
              v-for="a in targetApiList"
              :key="a.id"
              :label="`${a.request?.method} ${a.request?.path} (${a.id.slice(0,8)}...)`"
              :value="a.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="$t('monitor.form_target_id')" v-else-if="form.target_type === 'scenario'">
          <el-select
            v-model="form.target_id"
            filterable
            style="width:100%"
            :placeholder="$t('monitor.form_target_id_placeholder')"
          >
            <el-option
              v-for="s in targetScenarioList"
              :key="s.id"
              :label="`${s.name} (${s.steps?.length || 0} steps)`"
              :value="s.id"
            />
          </el-select>
        </el-form-item>
        <!-- P0-3: data_factory 目标选择器（数据模板） -->
        <el-form-item :label="$t('monitor.form_target_id')" v-else-if="form.target_type === 'data_factory'">
          <el-select
            v-model="form.target_id"
            filterable
            style="width:100%"
            :placeholder="$t('monitor.form_target_id_placeholder')"
          >
            <el-option
              v-for="t in targetTemplateList"
              :key="t.id"
              :label="`${t.name} (${(t.fields || []).length} fields)`"
              :value="t.id"
            />
          </el-select>
        </el-form-item>
        <!-- P0-3: 执行环境绑定（api/scenario 模式生效，加载环境 base_url/headers/variables） -->
        <el-form-item :label="$t('monitor.form_environment')" v-if="form.target_type !== 'data_factory'">
          <el-select v-model="form.environment_id" clearable style="width:100%" :placeholder="$t('monitor.form_environment_placeholder')">
            <el-option v-for="e in environmentList" :key="e.id" :label="e.name || e.id" :value="e.id" />
          </el-select>
        </el-form-item>
        <!-- P0-3: 监控级断言覆盖（仅 api 模式，覆盖 API 原断言做巡检专用校验） -->
        <el-form-item :label="$t('monitor.form_asserts_override')" v-if="form.target_type === 'api'">
          <div v-for="(a, i) in form.asserts" :key="i" style="display:flex;gap:6px;margin-bottom:6px">
            <el-input v-model="a.field" :placeholder="$t('monitor.assert_field_placeholder')" size="small" style="flex:1" />
            <el-select v-model="a.operator" size="small" style="width:120px">
              <el-option v-for="op in ['eq','ne','gt','lt','contains','exists','regex']" :key="op" :label="op" :value="op" />
            </el-select>
            <el-input v-model="a.expected" :placeholder="$t('monitor.assert_expected_placeholder')" size="small" style="width:120px" />
            <el-button size="small" :icon="Close" circle @click="form.asserts.splice(i,1)" />
          </div>
          <el-button size="small" @click="form.asserts.push({ field: '', operator: 'eq', expected: '' })">{{ $t('monitor.add_assert') }}</el-button>
        </el-form-item>
        <el-form-item :label="$t('monitor.form_sql_asserts')">
          <div v-for="(a, i) in form.sql_asserts" :key="i" class="sql-assert-row">
            <el-input v-model="a.name" :placeholder="$t('monitor.sql_assert_name')" size="small" style="width:140px" />
            <el-select v-model="a.db_service_id" clearable filterable :placeholder="$t('monitor.sql_db_service_id')" size="small" style="width:180px">
              <el-option v-for="svc in dbServices" :key="svc.id" :label="svc.name" :value="svc.id" />
            </el-select>
            <el-select v-model="a.sql_ref" clearable filterable :placeholder="$t('monitor.sql_ref')" size="small" style="width:180px">
              <el-option v-for="snippet in sqlSnippets" :key="snippet.id" :label="snippet.name" :value="snippet.id" />
            </el-select>
            <el-input v-model="a.sql_text" :placeholder="$t('monitor.sql_text')" size="small" style="flex:1" />
            <el-input v-model="a.field" :placeholder="$t('monitor.sql_field')" size="small" style="width:150px" />
            <el-select v-model="a.operator" size="small" style="width:100px">
              <el-option v-for="op in ['eq','ne','gt','gte','lt','lte','contains','exists','not_empty']" :key="op" :label="op" :value="op" />
            </el-select>
            <el-input v-model="a.expected" :placeholder="$t('monitor.assert_expected_placeholder')" size="small" style="width:110px" />
            <el-button size="small" :icon="Close" circle @click="form.sql_asserts.splice(i,1)" />
          </div>
          <el-button size="small" @click="form.sql_asserts.push(defaultSqlAssert())">{{ $t('monitor.add_sql_assert') }}</el-button>
        </el-form-item>
        <!-- P0-3: 静默期（计划维护窗口内跳过巡检，避免误报） -->
        <el-form-item :label="$t('monitor.form_silence_until')">
          <div style="display:flex;gap:8px;align-items:center;width:100%">
            <el-date-picker
              v-model="form.silence_until"
              type="datetime"
              style="flex:1"
              :placeholder="$t('monitor.form_silence_placeholder')"
              format="YYYY-MM-DD HH:mm"
              value-format="YYYY-MM-DDTHH:mm:ss"
              clearable
            />
            <!-- 快捷静默按钮：1/2/4 小时，避免手输时间 -->
            <el-button size="small" @click="silenceForHours(1)">+1h</el-button>
            <el-button size="small" @click="silenceForHours(2)">+2h</el-button>
            <el-button size="small" @click="silenceForHours(4)">+4h</el-button>
          </div>
        </el-form-item>
        <div style="display:flex;gap:12px">
          <el-form-item :label="$t('monitor.form_interval')" style="flex:1">
            <el-select v-model="form.interval" style="width:100%">
              <!-- P2: interval 加长周期（6h/12h/1d），支持低频巡检 -->
              <el-option v-for="i in ['30s','1m','5m','15m','30m','1h','6h','12h','1d']" :key="i" :label="i" :value="i" />
            </el-select>
          </el-form-item>
          <el-form-item :label="$t('monitor.form_risk_level')" style="flex:1">
            <el-select v-model="form.risk_level" style="width:100%">
              <el-option v-for="r in ['low','medium','high','critical']" :key="r" :label="$t('monitor.risk_level_'+r)" :value="r" />
            </el-select>
          </el-form-item>
        </div>
        <!-- cron 表达式：切换后隐藏 interval，使用 cron 调度 -->
        <el-form-item>
          <el-checkbox v-model="useCron" :label="$t('monitor.form_cron_toggle')" @change="form.cron = useCron ? form.cron : ''" />
        </el-form-item>
        <div v-if="useCron" style="display:flex;gap:12px">
          <el-form-item :label="$t('monitor.form_cron_label')" style="flex:1">
            <!-- P0-1 修复：select 和 input 共用 form.cron 导致死循环（选 __custom__ 后打字使 v-if 失效）。
                改为：select 选预设直接写入 form.cron；选 __custom__ 切换到独立 cronCustom 输入框 -->
            <el-select v-model="form.cron" style="width:100%" :placeholder="$t('monitor.form_cron_placeholder')">
              <el-option :label="$t('monitor.form_cron_preset_hourly')" value="0 * * * *" />
              <el-option :label="$t('monitor.form_cron_preset_daily9')" value="0 9 * * *" />
              <el-option :label="$t('monitor.form_cron_preset_weekly9')" value="0 9 * * 1" />
              <el-option :label="$t('monitor.form_cron_preset_monthly9')" value="0 9 1 * *" />
              <el-option :label="$t('monitor.form_cron_preset_workday9', '工作日 9 点')" value="0 9 * * 1-5" />
              <el-option :label="$t('monitor.form_cron_preset_halfhour', '每 30 分钟')" value="*/30 * * * *" />
              <el-option :label="$t('monitor.form_cron_custom')" value="__custom__" />
            </el-select>
          </el-form-item>
          <!-- 自定义输入：独立 v-model，不再共用 form.cron，修复死循环 -->
          <el-form-item v-if="form.cron === '__custom__'" style="flex:1">
            <template #label><span class="text-3" style="font-size:10px">&nbsp;</span></template>
            <el-input v-model="cronCustom" :placeholder="$t('monitor.form_cron_placeholder')" size="small" class="mono" />
          </el-form-item>
        </div>
        <div style="display:flex;gap:12px">
          <el-form-item :label="$t('monitor.form_max_failures')" style="flex:1">
            <el-input v-model.number="form.max_consecutive_failures" type="number" :min="1" :max="10" />
          </el-form-item>
          <el-form-item :label="$t('monitor.form_diff_threshold')" style="flex:1">
            <el-input v-model.number="form.diff_threshold" type="number" :min="1" />
          </el-form-item>
        </div>
        <el-form-item :label="$t('monitor.form_alert_channels')">
          <!-- 已保存的告警渠道，点击切换选中 -->
          <div v-if="savedChannels.length" class="channel-selector">
            <span
              v-for="ch in savedChannels"
              :key="ch.id"
              :class="['channel-chip', selectedChannelIds.includes(ch.id) && 'selected']"
              @click="toggleChannel(ch.id)"
            >
              <span class="channel-type">{{ ch.type || 'webhook' }}</span>
              <span class="channel-name">{{ ch.name }}</span>
              <span v-if="selectedChannelIds.includes(ch.id)" class="channel-check">✓</span>
            </span>
          </div>
          <div v-else class="text-3" style="font-size:11px;margin-bottom:6px">{{ $t('monitor.no_saved_channels') }}</div>
          <label class="form-label" style="font-size:11px;margin-top:6px">{{ $t('monitor.form_manual_url') }}</label>
          <el-input v-model="form._channels_str" type="textarea" :placeholder="$t('monitor.placeholder_manual_url')" :rows="3" />
        </el-form-item>
        <el-form-item :label="$t('monitor.form_ignore_fields')">
          <el-input v-model="form._ignore_str" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item :label="$t('common.owner')">
          <el-select v-model="form.owner" filterable clearable style="width:100%" :placeholder="$t('common.owner_placeholder')">
            <el-option v-for="u in userList" :key="u.id" :value="u.username" :label="u.username" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="form.alert_on_recovery" :label="$t('monitor.form_alert_on_recovery')" />
        </el-form-item>
        <el-alert v-if="validationIssues.length" :type="hasValidationErrors ? 'error' : 'warning'" :closable="false" show-icon>
          <template #title>
            <span v-for="issue in validationIssues.slice(0, 4)" :key="issue.code + issue.field" class="validation-line" @click="focusValidationField(issue.field)">
              {{ issue.message }} <span class="text-3">({{ issue.field }})</span>
            </span>
          </template>
        </el-alert>
      </el-form>
      <template #footer>
        <el-button @click="closeModal">{{ $t('common.cancel') }}</el-button>
        <el-button @click="validateDraft" :loading="validating">校验</el-button>
        <el-button type="primary" @click="submitMonitor" :disabled="submitting || !form.name || !validTarget">
          {{ submitting ? $t('monitor.submitting') : editingId ? $t('common.save') : $t('common.create') }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showAiGenerateModal" :title="$t('monitor.ai_generate_title')" width="560px">
      <el-form label-position="top">
        <el-form-item :label="$t('monitor.ai_generate_target_type')">
          <el-select v-model="aiGenerateForm.target_type" clearable style="width:100%" :placeholder="$t('monitor.ai_generate_target_type_placeholder')">
            <el-option :label="$t('monitor.form_target_type_api')" value="api" />
            <el-option :label="$t('monitor.form_target_type_scenario')" value="scenario" />
            <el-option :label="$t('monitor.form_target_type_data_factory')" value="data_factory" />
          </el-select>
        </el-form-item>
        <el-form-item :label="$t('monitor.ai_generate_target_ids')">
          <el-select v-model="aiGenerateForm.target_ids" multiple filterable clearable style="width:100%" :placeholder="$t('monitor.ai_generate_target_ids_placeholder')">
            <template v-if="aiGenerateForm.target_type === 'api'">
              <el-option v-for="a in targetApiList" :key="'api-'+a.id" :label="`${a.request?.method || ''} ${a.request?.path || a.name}`" :value="a.id" />
            </template>
            <template v-else-if="aiGenerateForm.target_type === 'scenario'">
              <el-option v-for="s in targetScenarioList" :key="'scenario-'+s.id" :label="`${s.name} (${s.steps?.length || 0} steps)`" :value="s.id" />
            </template>
            <template v-else-if="aiGenerateForm.target_type === 'data_factory'">
              <el-option v-for="t in targetTemplateList" :key="'tmpl-'+t.id" :label="`${t.name} (${(t.fields || []).length} fields)`" :value="t.id" />
            </template>
          </el-select>
        </el-form-item>
        <el-form-item :label="$t('monitor.ai_generate_goal')">
          <el-input v-model="aiGenerateForm.goal" type="textarea" :rows="2" :placeholder="$t('monitor.ai_generate_goal_placeholder')" />
        </el-form-item>
        <div style="display:flex;gap:12px">
          <el-form-item :label="$t('monitor.ai_generate_risk_preference')" style="flex:1">
            <el-select v-model="aiGenerateForm.risk_preference" clearable style="width:100%">
              <el-option v-for="r in ['low','medium','high','critical']" :key="r" :label="$t('assert.risk_' + r)" :value="r" />
            </el-select>
          </el-form-item>
          <el-form-item :label="$t('monitor.ai_generate_schedule_preference')" style="flex:1">
            <el-select v-model="aiGenerateForm.schedule_preference" clearable style="width:100%">
              <el-option v-for="i in ['1m','5m','15m','30m','1h','6h','12h','1d']" :key="i" :label="i" :value="i" />
            </el-select>
          </el-form-item>
        </div>
        <el-alert v-if="aiGenerateJob?.status" type="success" :closable="false" show-icon>
          <template #title>
            {{ $t('monitor.ai_generate_job', { status: aiGenerateJob.status, jobId: aiGenerateJob.job_id }) }}
            <el-button v-if="aiGenerateJob.status === 'pending_review'" link type="primary" @click="router.push('/generations?type=monitor&status=pending_review')">{{ $t('monitor.go_review') }}</el-button>
          </template>
        </el-alert>
      </el-form>
      <template #footer>
        <el-button @click="showAiGenerateModal = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="aiGenerating" @click="submitAiGenerate">{{ $t('monitor.ai_generate_submit') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter, useRoute } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { Close } from '@element-plus/icons-vue'
import { monitorApi, alertApi, alertChannelApi, scenarioApi, apiApi, factoryApi, environmentApi, databaseServiceApi, sqlSnippetApi, openWs } from '@/api'
import { useAuthStore, useProjectStore, useToastStore } from '@/stores'
import { fmt, riskTagType } from '@/utils'
import AppPagination from '@/components/AppPagination.vue'

const { t } = useI18n()
const authStore    = useAuthStore()
const projectStore = useProjectStore()
const toast        = useToastStore()
const router       = useRouter()
const route        = useRoute()
const monitorWs   = ref(null) // WebSocket 连接，onUnmounted 时同步清理

// P0-2: AI 降噪评估展示辅助
function aiSeverityLabel(sev) {
  const map = { critical: t('monitor.ai_critical', '严重'), high: t('monitor.ai_high', '高'), medium: t('monitor.ai_medium', '中'), low: t('monitor.ai_low', '低'), noise: t('monitor.ai_noise', '误报') }
  return map[sev] || sev
}
function aiSeverityTagType(sev) {
  const map = { critical: 'danger', high: 'danger', medium: 'warning', low: 'info', noise: 'success' }
  return map[sev] || 'info'
}

// P1: 跳转执行详情页查看 AI 失败诊断
function goExecution(execId) {
  router.push(`/executions/${execId}`)
}

// P1: 判断监控是否处于静默期
function isSilenced(row) {
  if (!row.silence_until) return false
  return new Date(row.silence_until) > new Date()
}

// P1: 解析目标名称（此前只显示截断 UUID，用户不知道巡检的是哪个接口/场景）
function targetLabel(row) {
  const id = (row.target_id || row.api_id || '').slice(0, 8)
  return id + '...'
}

// P1: 跳转到目标详情（API/场景/数据工厂）
function goTarget(row) {
  const id = row.target_id || row.api_id
  if (!id) return
  const type = row.target_type || 'api'
  if (type === 'scenario') router.push(`/scenarios/${id}`)
  else if (type === 'data_factory') router.push(`/factory`)
  else router.push(`/apis/${id}`)
}

const monitors    = ref([])
const statsMap    = ref({})
const loadingInit = ref(true)  // 首次加载时的 loading 状态
const activeTab   = ref('tasks')

// Alerts
const alerts         = ref([])
const alertTotal     = ref(0)
const alertPage      = ref(1)
const alertPageSize  = 30
const alertFilter    = ref({ risk_level: '', is_recovery: '', ai_severity: '' })
const loadingAlerts  = ref(false)  // 防止并行请求

// Modal
const showCreateModal = ref(false)
const showAiGenerateModal = ref(false)
const editingId       = ref(null)
const submitting      = ref(false)
const validating      = ref(false)
const validationIssues = ref([])
const aiGenerating = ref(false)
const aiGenerateJob = ref(null)
const runJobs = ref({})
const aiGenerateForm = ref({
  target_type: '',
  target_ids: [],
  goal: '',
  risk_preference: '',
  schedule_preference: '',
})
const defaultForm = () => ({
  name: '', api_id: '', interval: '5m', risk_level: 'medium',
  max_consecutive_failures: 3, diff_threshold: 3,
  alert_on_recovery: true,
  _channels_str: '', _ignore_str: '',
  target_type: 'api', target_id: '', cron: '',
  owner: '', project_id: projectStore.current || '',
  // P0-3: 补齐此前缺失的前端配置项（后端 MonitorDSL 已支持但前端无入口）
  environment_id: '',       // 执行环境绑定
  silence_until: null,      // 静默期截止时间
  asserts: [],              // 监控级断言覆盖（target_type=api 时生效，覆盖 API 原断言）
  sql_asserts: [],          // SQL 断言：执行后查询数据库并参与巡检结果判断
})
const form = ref(defaultForm())
const useCron = ref(false)  // 切换 cron 表达式输入模式
// P0-1: 独立的自定义 cron 表达式（修复与 form.cron 共用 v-model 的死循环 bug）
const cronCustom = ref('')

// 目标选择器：根据 target_type 加载可选列表
const targetApiList = ref([])     // API 列表（target_type=api 时展示）
const targetScenarioList = ref([]) // 场景列表（target_type=scenario 时展示）
const targetTemplateList = ref([]) // P0-3: 数据模板列表（target_type=data_factory 时展示）
const environmentList = ref([])    // P0-3: 执行环境列表
const dbServices = ref([])
const sqlSnippets = ref([])

// 加载可选 API 列表（搜索时用）
async function searchTargetApis(query) {
  if (!query) { targetApiList.value = []; return }
  try {
    const res = await apiApi.list({ project_id: projectStore.current, search: query, limit: 20 })
    targetApiList.value = res.items || []
  } catch { targetApiList.value = [] }
}

// 预加载 API 列表（AI 生成弹窗用，无需搜索关键词）
async function loadTargetApis() {
  try {
    const res = await apiApi.list({ project_id: projectStore.current, limit: 50 })
    targetApiList.value = res.items || []
  } catch { targetApiList.value = [] }
}

// 加载可选场景列表
async function loadTargetScenarios() {
  try {
    const res = await scenarioApi.list({ project_id: projectStore.current, limit: 100 })
    targetScenarioList.value = res.items || []
  } catch { targetScenarioList.value = [] }
}

// P0-3: 加载可选数据模板列表
async function loadTargetTemplates() {
  try {
    const res = await factoryApi.listTemplates(projectStore.current)
    targetTemplateList.value = res.items || res || []
  } catch { targetTemplateList.value = [] }
}

// P0-3: 加载执行环境列表
async function loadEnvironments() {
  try {
    const res = await environmentApi.list(projectStore.current)
    environmentList.value = res.items || res || []
  } catch { environmentList.value = [] }
}

async function loadSqlAssets() {
  try {
    const [svcRes, snipRes] = await Promise.all([
      databaseServiceApi.list(projectStore.current),
      sqlSnippetApi.list({ project_id: projectStore.current }),
    ])
    dbServices.value = svcRes.items || []
    sqlSnippets.value = snipRes.items || []
  } catch {
    dbServices.value = []
    sqlSnippets.value = []
  }
}

// P0-3: 快捷静默 N 小时（避免手输时间，维护窗口常用 1-4 小时）
function silenceForHours(hours) {
  const d = new Date(Date.now() + hours * 3600 * 1000)
  form.value.silence_until = d.toISOString().slice(0, 19)
}

// 切换 target_type 时重置 target_id
function onTargetTypeChange() {
  form.value.target_id = ''
  form.value.api_id = ''
  // P0-3: 按目标类型懒加载对应列表，避免一次性加载全部数据
  if (form.value.target_type === 'scenario') {
    loadTargetScenarios()
  } else if (form.value.target_type === 'data_factory') {
    loadTargetTemplates()
  }
}

// 负责人用户列表 —— 从 auth 接口加载已有用户供选择
const userList = ref([])

async function loadUsers() {
  try {
    const res = await authStore.listUsers()
    userList.value = Array.isArray(res) ? res : (res.users || [])
  } catch { userList.value = [] }
}

// 告警渠道集成 —— 从 /alert-channels 加载已保存的渠道
const savedChannels = ref([])
// 使用数组替代 Set，确保 Vue 响应式系统正确追踪变化
const selectedChannelIds = ref([])

async function loadChannels() {
  try {
    const res = await alertChannelApi.list({ project_id: projectStore.current })
    savedChannels.value = res.items || []
  } catch (e) {
    // 渠道加载失败时提示用户，避免编辑监控时已保存的频道选择丢失
    toast.error(e.message || t('monitor.toast_channels_load_failed'))
    savedChannels.value = []
  }
}

// 当选中的渠道变化时，同步 URL 到 _channels_str 文本框
watch(selectedChannelIds, (ids) => {
  const urls = ids.length
    ? ids.map(id => savedChannels.value.find(c => c.id === id)?.url).filter(Boolean)
    : []
  // 保留文本框中手动输入的非渠道 URL（不在已保存渠道中的 URL）
  const manualUrls = (form.value._channels_str || '')
    .split('\n').map(s => s.trim()).filter(Boolean)
    .filter(u => !savedChannels.value.some(c => c.url === u))
  form.value._channels_str = [...urls, ...manualUrls].join('\n')
}, { deep: true })

const activeCount = computed(() => monitors.value.filter(m => m.enabled).length)
const hasValidationErrors = computed(() => validationIssues.value.some(i => i.level === 'error'))
// 提交按钮禁用条件：根据 target_type 检查对应 target_id
const validTarget = computed(() => {
  // 编辑存量 api_id 向后兼容：api_id 非空即有效
  if (editingId.value && form.value.api_id && form.value.target_type === 'api') return true
  if (form.value.target_type === 'api') return !!form.value.api_id
  // P0-3: scenario 和 data_factory 都用 target_id
  if (form.value.target_type === 'scenario') return !!form.value.target_id
  if (form.value.target_type === 'data_factory') return !!form.value.target_id
  return false
})

async function loadMonitors() {
  try {
    monitors.value = await monitorApi.list({ project_id: projectStore.current })
  } catch (e) {
    toast.error(e.message || t('monitor.toast_load_failed'))
    monitors.value = []
    return
  }
  // 分批请求统计数据，控制并发数避免冲击后端；批量合并更新减少对象分配
  const batchSize = 5
  for (let i = 0; i < monitors.value.length; i += batchSize) {
    const batch = monitors.value.slice(i, i + batchSize)
    const results = await Promise.allSettled(batch.map(m => monitorApi.stats(m.id)))
    // 合并本轮成功的统计结果，每批次只触发一次响应式更新
    const patch = {}
    results.forEach((r, idx) => {
      if (r.status === 'fulfilled') patch[batch[idx].id] = r.value
    })
    statsMap.value = { ...statsMap.value, ...patch }
  }
}

async function loadAlerts() {
  // 防止并行请求导致数据错乱
  if (loadingAlerts.value) return
  loadingAlerts.value = true
  try {
    const params = {
      skip: (alertPage.value - 1) * alertPageSize,
      limit: alertPageSize,
      project_id: projectStore.current,
    }
    if (alertFilter.value.risk_level)  params.risk_level  = alertFilter.value.risk_level
    if (alertFilter.value.is_recovery !== '') params.is_recovery = alertFilter.value.is_recovery === 'true'
    if (alertFilter.value.ai_severity) params.ai_severity = alertFilter.value.ai_severity
    const res = await alertApi.list(params)
    alerts.value = res.items || []
    alertTotal.value = res.total || 0
  } catch (e) {
    toast.error(e.message || t('monitor.toast_alerts_load_failed'))
  } finally {
    loadingAlerts.value = false
  }
}

async function toggleMonitor(m) {
  try {
    await monitorApi.toggle(m.id, !m.enabled)
    toast.success(m.enabled ? t('monitor.toast_disabled') : t('monitor.toast_enabled'))
    await loadMonitors()
  } catch (e) { toast.error(e.message) }
}

// P0-3: 手动试跑状态（按 monitor id 隔离 loading，避免多个按钮互相影响）
const runningId = ref('')
async function runMonitorNow(m) {
  runningId.value = m.id
  try {
    const res = await monitorApi.runNow(m.id)
    runJobs.value = { ...runJobs.value, [m.id]: res }
    toast.info(t('monitor.run_queued', { jobId: res.job_id }))
    pollRunJob(m.id, res.job_id)
  } catch (e) {
    toast.error(e.message || t('monitor.run_now_error'))
  } finally {
    runningId.value = ''
  }
}

async function pollRunJob(monitorId, jobId) {
  for (let i = 0; i < 20; i++) {
    await new Promise(resolve => setTimeout(resolve, 1500))
    try {
      const job = await monitorApi.job(jobId)
      runJobs.value = { ...runJobs.value, [monitorId]: job }
      if (['passed', 'failed'].includes(job.status)) {
        job.status === 'passed'
          ? toast.success(t('monitor.run_now_passed', { ms: job.execution?.duration_ms || 0 }))
          : toast.error(t('monitor.run_now_failed', { reason: job.execution?.failure_reason || job.error || 'unknown' }))
        await loadMonitors()
        return
      }
    } catch {
      return
    }
  }
}

async function deleteMonitor(m) {
  try {
    await ElMessageBox.confirm(t('monitor.confirm_delete_msg', { name: m.name }), t('monitor.confirm_delete_title'), { type: 'warning' })
    await monitorApi.remove(m.id)
    toast.success(t('monitor.toast_deleted'))
    await loadMonitors()
  } catch (e) {
    // ElMessageBox reject 可能返回 'cancel'(按钮) 或 'close'(X关闭)，均忽略
    if (e !== 'cancel' && e !== 'close' && e.message) toast.error(e.message)
  }
}

async function editMonitor(m) {
  editingId.value = m.id
  const allAsserts = m.asserts || []
  const sqlAsserts = allAsserts.filter(a => a.source === 'sql' || a.sql_ref || a.sql_text || a.sql_query)
  const httpAsserts = allAsserts.filter(a => !(a.source === 'sql' || a.sql_ref || a.sql_text || a.sql_query))
  form.value = {
    ...defaultForm(),
    ...m,
    asserts: httpAsserts,
    sql_asserts: sqlAsserts.map(a => ({
      ...defaultSqlAssert(),
      ...a,
      name: a.name || a.target_var || a.sql_query?.name || 'monitor_sql',
      db_service_id: a.db_service_id || a.sql_query?.db_service_id || '',
      sql_ref: a.sql_ref || a.sql_query?.sql_ref || '',
      sql_text: a.sql_text || a.sql_query?.sql_text || '',
    })),
    _channels_str: (m.alert_channels || []).join('\n'),
    _ignore_str:   (m.diff_ignore_paths || []).join('\n'),
  }
  // 恢复 cron 状态
  useCron.value = !!(m.cron && m.cron.trim())
  // P0-1: cron 回显 —— 预设值直接匹配 select；非预设值则切换到自定义模式回显 cronCustom
  if (useCron.value) {
    const presets = ['0 * * * *', '0 9 * * *', '0 9 * * 1', '0 9 1 * *', '0 9 * * 1-5', '*/30 * * * *']
    if (presets.includes(m.cron)) {
      form.value.cron = m.cron
      cronCustom.value = ''
    } else {
      form.value.cron = '__custom__'
      cronCustom.value = m.cron
    }
  }
  // P0-3: 按目标类型懒加载对应列表 + 预加载环境列表（编辑时回显用）
  if (m.target_type === 'scenario') {
    await loadTargetScenarios()
  } else if (m.target_type === 'data_factory') {
    await loadTargetTemplates()
  }
  await loadEnvironments()
  // 先加载渠道列表，再匹配已保存的渠道（避免 loadChannels 未完成时 selectedChannelIds 为空）
  await loadChannels()
  const channelUrls = m.alert_channels || []
  selectedChannelIds.value = savedChannels.value.filter(c => channelUrls.includes(c.url)).map(c => c.id)
  showCreateModal.value = true
}

// 切换已保存渠道的选中状态
function toggleChannel(id) {
  const idx = selectedChannelIds.value.indexOf(id)
  if (idx >= 0) {
    selectedChannelIds.value.splice(idx, 1)
  } else {
    selectedChannelIds.value.push(id)
  }
}

function closeModal() {
  showCreateModal.value = false
  editingId.value = null
  form.value = defaultForm()
  selectedChannelIds.value = []
  validationIssues.value = []
}

function defaultSqlAssert() {
  return { source: 'sql', name: 'monitor_sql', db_service_id: '', sql_ref: '', sql_text: '', field: 'sql.monitor_sql.scalar', operator: 'eq', expected: '' }
}

function buildMonitorPayload() {
  const channels = form.value._channels_str.split('\n').map(s => s.trim()).filter(Boolean)
  const ignore   = form.value._ignore_str.split('\n').map(s => s.trim()).filter(Boolean)
  return {
    name: form.value.name,
    api_id: form.value.target_type === 'api' ? form.value.api_id : '',
    target_type: form.value.target_type || 'api',
    target_id: form.value.target_type === 'api' ? form.value.api_id : form.value.target_id,
    interval: form.value.interval,
    cron: (() => {
      if (!useCron.value || !form.value.cron) return ''
      if (form.value.cron === '__custom__') return cronCustom.value || ''
      return form.value.cron
    })(),
    risk_level: form.value.risk_level,
    max_consecutive_failures: form.value.max_consecutive_failures,
    diff_threshold: form.value.diff_threshold,
    alert_on_recovery: form.value.alert_on_recovery,
    alert_channels: channels,
    diff_ignore_paths: ignore,
    enabled: true,
    project_id: projectStore.current,
    owner: form.value.owner || '',
    environment_id: form.value.environment_id || '',
    silence_until: form.value.silence_until || null,
    asserts: [
      ...((form.value.target_type === 'api' && form.value.asserts.length)
        ? form.value.asserts.filter(a => a.field).map(a => ({
          field: a.field, operator: a.operator, expected: a.expected,
          risk_level: a.risk_level || 'medium',
        }))
        : []),
      ...((form.value.sql_asserts || [])
        .filter(a => a.sql_ref || a.sql_text)
        .map(a => ({
          source: 'sql',
          name: a.name || 'monitor_sql',
          target_var: a.name || 'monitor_sql',
          db_service_id: a.db_service_id || '',
          sql_ref: a.sql_ref || '',
          sql_text: a.sql_text || '',
          field: a.field || `sql.${a.name || 'monitor_sql'}.scalar`,
          operator: a.operator || 'eq',
          expected: a.expected,
        }))),
    ],
  }
}

async function validateDraft() {
  validating.value = true
  try {
    const res = await monitorApi.validate({ ...buildMonitorPayload(), id: editingId.value || '' })
    validationIssues.value = res.issues || []
    if (res.valid) toast.success(t('monitor.validate_passed'))
    else toast.error(validationIssues.value[0]?.message || t('monitor.validate_failed'))
    return res.valid
  } catch (e) {
    const detail = e.response?.data?.detail
    validationIssues.value = detail?.issues || []
    toast.error(validationIssues.value[0]?.message || e.message)
    return false
  } finally {
    validating.value = false
  }
}

async function validateExisting(row) {
  try {
    const res = await monitorApi.validateExisting(row.id)
    validationIssues.value = res.issues || []
    if (res.valid) toast.success(t('monitor.validate_passed'))
    else toast.error(validationIssues.value[0]?.message || t('monitor.validate_failed'))
  } catch (e) {
    toast.error(e.message)
  }
}

function focusValidationField(field) {
  const key = String(field || '').split('.')[0]
  const selector = key === 'target_id' ? '.el-select' : 'input'
  document.querySelector(selector)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

async function submitMonitor() {
  submitting.value = true
  try {
    const payload = buildMonitorPayload()
    const ok = await validateDraft()
    if (!ok) return
    if (editingId.value) {
      await monitorApi.update(editingId.value, payload)
      toast.success(t('monitor.toast_updated'))
    } else {
      await monitorApi.create(payload)
      toast.success(t('monitor.toast_created'))
    }
    closeModal()
    await loadMonitors()
  } catch (e) { toast.error(e.message) }
  finally { submitting.value = false }
}

async function submitAiGenerate() {
  aiGenerating.value = true
  try {
    const res = await monitorApi.generate({
      ...aiGenerateForm.value,
      project_id: projectStore.current,
    })
    aiGenerateJob.value = res
    toast.success(t('monitor.ai_generate_queued'))
  } catch (e) {
    toast.error(e.message)
  } finally {
    aiGenerating.value = false
  }
}

// P0-3: WebSocket 实时告警推送 —— 订阅 /ws/monitor，新告警自动插入列表顶部
function onMonitorWs(msg) {
  if (!msg) return
  // 新告警事件：插入 alerts 列表头部 + 刷新总数
  if (msg.id && msg.monitor_id) {
    // msg 本身就是 AlertRecord
    alerts.value.unshift(msg)
    alertTotal.value++
    // 刷新监控列表的 stats（fail_streak 可能变化）
    loadMonitors()
  }
  // AI 评估完成事件：更新对应告警的 ai_severity/ai_root_cause
  if (msg.type === 'alert_assessed' && msg.alert_id) {
    const alert = alerts.value.find(a => a.id === msg.alert_id)
    if (alert) {
      alert.ai_severity = msg.severity
      alert.ai_root_cause = msg.root_cause
    }
  }
  if (msg.type === 'alert_assessment' && msg.alert_id) {
    const alert = alerts.value.find(a => a.id === msg.alert_id)
    if (alert) alert.ai_status = msg.status
  }
  if (msg.type === 'monitor_run' && msg.monitor_id) {
    runJobs.value = { ...runJobs.value, [msg.monitor_id]: msg }
    if (['passed', 'failed'].includes(msg.status)) loadMonitors()
  }
  if (msg.type === 'monitor_generation') {
    aiGenerateJob.value = { ...(aiGenerateJob.value || {}), ...msg }
  }
}

onMounted(async () => {
  // 并行加载监控和告警，任一失败不影响另一请求；全部失败时提示用户
  const [monitorResult, alertResult] = await Promise.allSettled([loadMonitors(), loadAlerts()])
  if (monitorResult.status === 'rejected' && alertResult.status === 'rejected') {
    toast.error(t('monitor.toast_load_all_failed'))
  }
  loadingInit.value = false
  // P0-3: 建立 WebSocket 连接接收实时告警推送（在 onUnmounted 中同步清理, 避免 await 后组件实例丢失）
  monitorWs.value = openWs(`/monitor?project_id=${encodeURIComponent(projectStore.current || 'default')}`, onMonitorWs)

  // 从场景页"添加至巡检"跳转：预填 target_type=scenario + target_id，自动打开创建对话框
  const scenarioId = route.query.scenario_id
  if (scenarioId) {
    await loadTargetScenarios()
    form.value.target_type = 'scenario'
    form.value.target_id = scenarioId
    showCreateModal.value = true
  }
})

// 组件卸载时清理 WebSocket 连接
onUnmounted(() => {
  if (monitorWs.value?.terminate) monitorWs.value.terminate()
})

// 项目切换时重新加载巡检任务列表，确保按项目隔离
watch(() => projectStore.current, () => {
  loadMonitors()
  loadAlerts()
  loadSqlAssets()
  runJobs.value = {}
  validationIssues.value = []
  aiGenerateJob.value = null
  aiGenerateForm.value = { target_type: '', target_ids: [], goal: '', risk_preference: '', schedule_preference: '' }
})

// 打开 modal 时加载告警渠道、执行环境、场景/模板等下拉数据
watch(showCreateModal, (open) => {
  if (open) {
    loadChannels()
    loadEnvironments()
    loadUsers()
    loadTargetScenarios()
    loadTargetTemplates()
    loadSqlAssets()
  }
})

// 打开 AI 生成弹窗时预加载场景和模板列表，避免目标 ID 下拉为空
watch(showAiGenerateModal, (open) => {
  if (open) {
    loadTargetScenarios()
    loadTargetTemplates()
  }
})

// 切换 AI 生成目标类型时清空已选目标 ID，并根据类型预加载对应列表
watch(() => aiGenerateForm.value.target_type, (val) => {
  aiGenerateForm.value.target_ids = []
  if (val === 'api') {
    loadTargetApis()
  } else if (val === 'scenario') {
    loadTargetScenarios()
  } else if (val === 'data_factory') {
    loadTargetTemplates()
  }
})
</script>

<style scoped>
.alert-filters {
  display: flex; gap: 10px; margin-bottom: 12px;
}

.channel-selector {
  display: flex; flex-wrap: wrap; gap: 6px;
  margin-bottom: 4px;
}
.channel-chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 10px; border-radius: var(--radius);
  border: 1px solid var(--border);
  background: var(--bg); font-size: 12px;
  cursor: pointer; user-select: none;
  transition: border-color .15s, background .15s;
}
.channel-chip:hover { border-color: var(--accent); }
.channel-chip.selected {
  border-color: var(--accent); background: rgba(99,102,241,.08);
}
.channel-chip .channel-type {
  color: var(--text-3); font-size: 10px; text-transform: uppercase;
}
.channel-chip .channel-name {
  color: var(--text); max-width: 120px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap;
}
.channel-chip .channel-check {
  color: var(--accent); font-weight: 600; font-size: 11px;
}
.validation-line {
  display: block;
  line-height: 1.5;
  cursor: pointer;
}
.validation-line:hover { color: var(--accent); }
.sql-assert-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
  width: 100%;
}
</style>
