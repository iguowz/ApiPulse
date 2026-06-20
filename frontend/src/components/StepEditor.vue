<template>
  <el-dialog
    :model-value="visible"
    @update:model-value="emit('update:visible', $event)"
    :title="step.name || $t('step_editor.title')"
    width="720px"
    :close-on-click-modal="false"
    destroy-on-close
    class="step-editor-dialog"
  >
    <!-- 基本信息区 -->
    <div class="se-basic">
      <div class="se-basic-row">
        <div class="se-field">
          <div class="form-label">{{ $t('scenario_detail.step_id_label') }}</div>
          <el-input v-model="local.step_id" size="small" />
        </div>
        <div class="se-field">
          <div class="form-label">{{ $t('scenario_detail.step_name_label') }}</div>
          <el-input v-model="local.name" :placeholder="$t('scenario_detail.step_name_placeholder')" size="small" />
        </div>
      </div>
      <div class="se-basic-row">
        <div class="se-field" style="flex:1">
          <div class="form-label">{{ $t('scenario_detail.api_id_label') }}</div>
          <el-select
            v-model="local.api_id"
            filterable
            clearable
            size="small"
            :placeholder="$t('step_editor.api_id_placeholder')"
            @change="onApiChange"
          >
            <el-option
              v-for="api in props.apiList || []"
              :key="api.id"
              :label="apiOptionLabel(api)"
              :value="api.id"
            >
              <div class="api-option">
                <span class="api-method">{{ api.request?.method || 'GET' }}</span>
                <span class="api-path">{{ api.request?.path || api.name || api.id }}</span>
              </div>
            </el-option>
          </el-select>
          <span v-if="apiName" class="text-3" style="font-size:10px;margin-top:2px">{{ apiName }}</span>
        </div>
        <div class="se-field" style="flex:2">
          <div class="form-label">{{ $t('scenario_detail.depends_on_label') }}</div>
          <!-- el-select 多选下拉：支持从已有步骤中选择 + 手动输入新 ID -->
          <el-select
            v-model="local.depends_on"
            multiple
            filterable
            allow-create
            default-first-option
            :placeholder="$t('step_editor.depends_placeholder')"
            size="small"
          >
            <el-option
              v-for="sid in (props.availableSteps || []).filter(s => s !== local.step_id)"
              :key="sid"
              :label="sid"
              :value="sid"
            />
          </el-select>
        </div>
      </div>
      <div class="se-basic-row">
        <div class="se-field" style="flex:1">
          <div class="form-label">{{ $t('scenario_detail.retry_label') }}</div>
          <el-input v-model.number="local.retry" type="number" :min="0" :max="5" size="small" />
        </div>
        <div class="se-field" style="flex:1">
          <div class="form-label">{{ $t('scenario_detail.timeout_label') }}</div>
          <el-input v-model.number="local.timeout_s" type="number" :min="1" :max="120" size="small" />
        </div>
      </div>
      <!-- 等待时间：步骤执行前等待 -->
      <div class="se-basic-row">
        <div class="se-field" style="flex:1;max-width:280px">
          <div class="form-label">{{ $t('step_editor.wait_ms_label') }}</div>
          <el-input-number v-model.number="local.wait_ms" :min="0" :step="100" size="small" :placeholder="$t('step_editor.wait_ms_hint')" />
        </div>
      </div>
    </div>

    <!-- 8 Tab 编辑器 -->
    <el-tabs v-model="activeTab" class="se-tabs">
      <!-- 1. Params -->
      <el-tab-pane :label="$t('step_editor.tab_params')" name="params">
        <div class="se-tab-body">
          <div v-if="normalizedVariableOptions.length" class="var-toolbar">
            <span class="var-toolbar-label">变量</span>
            <el-button
              v-for="item in normalizedVariableOptions"
              :key="item.value"
              size="small"
              link
              type="primary"
              @click="copyVariable(item.value)"
            >{{ item.label }}</el-button>
          </div>
          <div class="param-layout">
            <div class="param-group">
              <div class="param-group-head">
                <span>Query</span>
                <el-tag size="small">{{ Object.keys(paramEditor.query).length }}</el-tag>
              </div>
              <KeyValueEditor
                v-model="paramEditor.query"
                key-placeholder="name"
                :value-placeholder="$t('kv_editor.value_placeholder')"
              />
            </div>
            <div class="param-group">
              <div class="param-group-head">
                <span>Path</span>
                <el-tag size="small" :type="missingPathParams.length ? 'warning' : 'info'">
                  {{ missingPathParams.length ? `缺 ${missingPathParams.length}` : Object.keys(paramEditor.path).length }}
                </el-tag>
              </div>
              <KeyValueEditor
                v-model="paramEditor.path"
                key-placeholder="id"
                :value-placeholder="$t('kv_editor.value_placeholder')"
              />
              <div v-if="missingPathParams.length" class="param-warning">
                URL 缺少路径参数：{{ missingPathParams.join(', ') }}
              </div>
            </div>
            <div class="param-group">
              <div class="param-group-head">
                <span>Body 字段</span>
                <el-tag size="small">{{ Object.keys(paramEditor.body).length }}</el-tag>
              </div>
              <KeyValueEditor
                v-model="paramEditor.body"
                key-placeholder="field"
                :value-placeholder="$t('kv_editor.value_placeholder')"
              />
            </div>
            <div class="param-group">
              <div class="param-group-head">
                <span>局部变量</span>
                <el-tag size="small">{{ Object.keys(paramEditor.vars).length }}</el-tag>
              </div>
              <KeyValueEditor
                v-model="paramEditor.vars"
                key-placeholder="var"
                value-placeholder="{{env.TOKEN}}"
              />
            </div>
            <div class="param-group param-group-wide">
              <div class="param-group-head">
                <span>其他兼容参数</span>
                <el-tag size="small">{{ Object.keys(paramEditor.legacy).length }}</el-tag>
              </div>
              <KeyValueEditor
                v-model="paramEditor.legacy"
                :key-placeholder="$t('kv_editor.key_placeholder')"
                :value-placeholder="$t('kv_editor.value_placeholder')"
              />
            </div>
          </div>
        </div>
      </el-tab-pane>

      <!-- 2. Authorization -->
      <el-tab-pane :label="$t('step_editor.tab_auth')" name="auth">
        <div class="se-tab-body">
          <div v-if="normalizedVariableOptions.length" class="var-toolbar">
            <span class="var-toolbar-label">鉴权变量</span>
            <el-button
              v-for="item in normalizedVariableOptions"
              :key="item.value"
              size="small"
              link
              type="primary"
              @click="insertAuthVariable(item.value)"
            >{{ item.label }}</el-button>
          </div>
          <div v-if="authTemplateOptions.length" class="auth-template-row">
            <el-select v-model="selectedAuthTemplateKey" size="small" placeholder="选择环境鉴权模板" style="min-width:220px">
              <el-option v-for="item in authTemplateOptions" :key="item.key" :label="item.label" :value="item.key" />
            </el-select>
            <el-button size="small" type="primary" plain @click="applySelectedAuthTemplate">应用模板</el-button>
          </div>
          <div class="se-field" style="margin-bottom:12px">
            <div class="form-label">{{ $t('step_editor.auth_type') }}</div>
            <el-select v-model="local.auth.type" size="small" style="width:200px">
              <el-option :label="$t('step_editor.auth_none')" value="" />
              <el-option :label="$t('step_editor.auth_bearer')" value="bearer" />
              <el-option :label="$t('step_editor.auth_basic')" value="basic" />
              <el-option :label="$t('step_editor.auth_apikey')" value="apikey" />
            </el-select>
          </div>
          <!-- Bearer Token -->
          <template v-if="local.auth.type === 'bearer'">
            <div class="se-field">
              <div class="form-label">{{ $t('step_editor.auth_token') }}</div>
              <div class="auth-token-row">
                <el-select v-model="authTokenSource" size="small" style="width:150px" @change="applyAuthTokenSource">
                  <el-option label="手填" value="manual" />
                  <el-option label="环境变量" value="env" />
                  <el-option label="上一步变量" value="extracted" />
                  <el-option label="登录结果" value="login" />
                </el-select>
                <el-input v-model="authTokenVar" size="small" placeholder="TOKEN / token / login.token" @change="applyAuthTokenSource" />
              </div>
              <el-input v-model="local.auth.token" size="small" placeholder="{{env.API_TOKEN}}" />
            </div>
          </template>
          <!-- Basic Auth -->
          <template v-if="local.auth.type === 'basic'">
            <div class="se-field" style="margin-bottom:8px">
              <div class="form-label">{{ $t('step_editor.auth_username') }}</div>
              <el-input v-model="local.auth.username" size="small" />
            </div>
            <div class="se-field">
              <div class="form-label">{{ $t('step_editor.auth_password') }}</div>
              <el-input v-model="local.auth.password" size="small" type="password" show-password />
            </div>
          </template>
          <!-- API Key -->
          <template v-if="local.auth.type === 'apikey'">
            <div class="se-field" style="margin-bottom:8px">
              <div class="form-label">{{ $t('step_editor.auth_key') }}</div>
              <el-input v-model="local.auth.key" size="small" />
            </div>
            <div class="se-field" style="margin-bottom:8px">
              <div class="form-label">{{ $t('step_editor.auth_value') }}</div>
              <div class="auth-token-row">
                <el-select v-model="authTokenSource" size="small" style="width:150px" @change="applyAuthTokenSource">
                  <el-option label="手填" value="manual" />
                  <el-option label="环境变量" value="env" />
                  <el-option label="上一步变量" value="extracted" />
                  <el-option label="登录结果" value="login" />
                </el-select>
                <el-input v-model="authTokenVar" size="small" placeholder="API_KEY / token / login.token" @change="applyAuthTokenSource" />
              </div>
              <el-input v-model="local.auth.value" size="small" placeholder="{{env.API_KEY}}" />
            </div>
            <div class="se-field">
              <div class="form-label">{{ $t('step_editor.auth_add_to') }}</div>
              <el-select v-model="local.auth.in" size="small" style="width:150px">
                <el-option :label="$t('step_editor.auth_add_header')" value="header" />
                <el-option :label="$t('step_editor.auth_add_query')" value="query" />
              </el-select>
            </div>
          </template>
        </div>
      </el-tab-pane>

      <!-- 3. Headers -->
      <el-tab-pane :label="tabLabel('headers', Object.keys(local.override_headers || {}).length)" name="headers">
        <div class="se-tab-body">
          <div v-if="normalizedVariableOptions.length" class="var-toolbar">
            <span class="var-toolbar-label">变量</span>
            <el-button
              v-for="item in normalizedVariableOptions"
              :key="item.value"
              size="small"
              link
              type="primary"
              @click="copyVariable(item.value)"
            >{{ item.label }}</el-button>
          </div>
          <KeyValueEditor
            v-model="local.override_headers"
            :key-placeholder="$t('step_editor.header_name')"
            :value-placeholder="$t('step_editor.header_value')"
          />
        </div>
      </el-tab-pane>

      <!-- 4. Body -->
      <el-tab-pane :label="$t('step_editor.tab_body')" name="body">
        <div class="se-tab-body">
          <div class="se-field" style="margin-bottom:12px">
            <div class="form-label">{{ $t('step_editor.body_type') }}</div>
            <el-select v-model="bodyType" size="small" style="width:150px">
              <el-option :label="$t('step_editor.body_none')" value="none" />
              <el-option :label="$t('step_editor.body_json')" value="json" />
              <el-option :label="$t('step_editor.body_raw')" value="raw" />
            </el-select>
          </div>
          <div v-if="bodyType === 'json'" class="se-field">
            <div class="form-label body-label">
              <span>{{ $t('step_editor.body_content') }}</span>
              <span class="body-actions">
                <el-button size="small" link type="primary" @click="formatBodyJson">格式化</el-button>
                <el-button size="small" link type="primary" @click="restoreBodyFromApi">恢复示例</el-button>
                <el-button size="small" link type="primary" :disabled="mockLoading || !local.api_id" @click="fillBodyFromMock">使用 Mock</el-button>
              </span>
            </div>
            <div v-if="normalizedVariableOptions.length" class="var-toolbar">
              <span class="var-toolbar-label">插入变量</span>
              <el-button
                v-for="item in normalizedVariableOptions"
                :key="item.value"
                size="small"
                link
                type="primary"
                @click="insertBodyVariable(item.value)"
              >{{ item.label }}</el-button>
            </div>
            <el-input
              v-model="bodyContent"
              type="textarea"
              :rows="8"
              :class="{ 'input-error': bodyError }"
            />
            <span v-if="bodyError" class="field-error">{{ $t('scenario_detail.json_error') }}</span>
          </div>
          <div v-else-if="bodyType === 'raw'" class="se-field">
            <div class="form-label body-label">
              <span>{{ $t('step_editor.body_content') }}</span>
              <span class="body-actions">
                <el-button size="small" link type="primary" @click="restoreBodyFromApi">恢复示例</el-button>
                <el-button size="small" link type="primary" :disabled="mockLoading || !local.api_id" @click="fillBodyFromMock">使用 Mock</el-button>
              </span>
            </div>
            <div v-if="normalizedVariableOptions.length" class="var-toolbar">
              <span class="var-toolbar-label">插入变量</span>
              <el-button
                v-for="item in normalizedVariableOptions"
                :key="item.value"
                size="small"
                link
                type="primary"
                @click="insertBodyVariable(item.value)"
              >{{ item.label }}</el-button>
            </div>
            <el-input
              v-model="bodyContent"
              type="textarea"
              :rows="8"
            />
          </div>
        </div>
      </el-tab-pane>

      <!-- 5. Assertions -->
      <el-tab-pane :label="tabLabel('assertions', local.assertions?.length || 0)" name="assertions">
        <div class="se-tab-body">
          <div v-if="normalizedVariableOptions.length" class="var-toolbar">
            <span class="var-toolbar-label">变量</span>
            <el-button
              v-for="item in normalizedVariableOptions"
              :key="item.value"
              size="small"
              link
              type="primary"
              @click="copyVariable(item.value)"
            >{{ item.label }}</el-button>
          </div>
          <div v-if="!local.assertions.length" class="text-3 se-empty">
            {{ $t('step_editor.no_assertions') }}
          </div>
          <div v-for="(a, i) in local.assertions" :key="i" class="assert-row">
            <el-select v-model="a.source" size="small" style="width:90px" @change="onAssertionSourceChange(a)">
              <el-option :label="$t('step_editor.assert_source_response')" value="response" />
              <el-option :label="$t('step_editor.assert_source_status')" value="status" />
              <el-option :label="$t('step_editor.assert_source_header')" value="header" />
              <el-option label="耗时" value="performance" />
              <el-option label="SQL" value="sql" />
            </el-select>
            <el-input v-model="a.path" size="small" :placeholder="$t('step_editor.assert_path')" style="flex:1" />
            <el-select v-model="a.operator" size="small" style="width:150px" filterable @change="onAssertionOperatorChange(a)">
              <!-- P0-2：operator 从后端单一来源加载，修复原 value="neq" bug（后端实际为 "ne"） -->
              <el-option
                v-for="item in stepAssertOps"
                :key="item.op"
                :label="$t('assert.operator_' + item.op)"
                :value="item.op"
                :title="item.help_zh"
              />
            </el-select>
            <!-- expected 按 expected_type 动态渲染：none 类禁用，number 用数字框，其余文本 -->
            <el-input v-if="assertionExpectedType(a) === 'none'" model-value="—" disabled size="small" style="width:130px" />
            <el-input-number v-else-if="assertionExpectedType(a) === 'number'" v-model="a.expected" :controls="false" size="small" style="width:130px" />
            <el-select v-else-if="assertionExpectedType(a) === 'select_type'" v-model="a.expected" size="small" style="width:130px">
              <el-option v-for="typeName in assertTypeOptions" :key="typeName" :label="typeName" :value="typeName" />
            </el-select>
            <el-select v-else-if="assertionExpectedType(a) === 'multi'" v-model="a.expected" multiple filterable allow-create default-first-option size="small" style="width:170px" />
            <el-input v-else-if="assertionExpectedType(a) === 'json'" v-model="a.expectedText" size="small" :placeholder="$t('step_editor.assert_expected')" style="width:170px" />
            <el-input v-else v-model="a.expected" size="small" :placeholder="$t('step_editor.assert_expected')" style="width:130px" />
            <el-button size="small" :icon="Delete" circle @click="local.assertions.splice(i, 1)" />
            <div v-if="a.source === 'sql'" class="sql-assert-panel">
              <el-input v-model="a.sql_query.name" size="small" placeholder="SQL 名称" />
              <el-select v-model="a.sql_query.sql_ref" size="small" filterable clearable placeholder="选择 SQL 片段">
                <el-option v-for="snip in sqlSnippets" :key="snip.id" :label="snip.name" :value="snip.id" />
              </el-select>
              <el-select v-if="!a.sql_query.sql_ref" v-model="a.sql_query.db_service_id" size="small" filterable clearable placeholder="数据库服务">
                <el-option v-for="svc in dbServices" :key="svc.id" :label="svc.name" :value="svc.id" />
              </el-select>
              <el-input
                v-if="!a.sql_query.sql_ref"
                v-model="a.sql_query.sql_text"
                type="textarea"
                :rows="3"
                class="se-code sql-assert-text"
                placeholder="SELECT ..."
              />
              <el-input v-model="a.sqlParamsText" size="small" class="mono" placeholder='参数 JSON，如 {"id":"{{user_id}}"}' />
            </div>
          </div>
          <el-button size="small" :icon="Plus" @click="addAssertion" style="margin-top:8px">
            {{ $t('step_editor.add_assertion') }}
          </el-button>
          <!-- P1-4: AI 推荐断言（基于 API 响应样本） -->
          <el-button size="small" @click="aiRecommendAsserts" :disabled="aiRecommending || !local.api_id" style="margin-top:8px;margin-left:8px">
            {{ aiRecommending ? $t('step_editor.ai_recommending') : '✨ ' + $t('step_editor.ai_recommend') }}
          </el-button>
          <el-button size="small" @click="mockSuggestAsserts" :disabled="mockLoading || !local.api_id" style="margin-top:8px;margin-left:8px">
            {{ mockLoading ? 'Mock 生成中' : '从 Mock 生成断言' }}
          </el-button>
          <el-button size="small" type="primary" plain @click="dryRunAssertions" :loading="assertDryRunLoading" :disabled="!local.assertions.length" style="margin-top:8px;margin-left:8px">
            试算断言
          </el-button>
          <el-button size="small" @click="fillAssertSampleFromMock" :disabled="mockLoading || !local.api_id" style="margin-top:8px;margin-left:8px">
            使用 Mock 样本
          </el-button>
          <el-button size="small" @click="fillAssertSampleFromRecent" :loading="recentSampleLoading" :disabled="!local.api_id" style="margin-top:8px;margin-left:8px">
            使用最近响应
          </el-button>
          <div class="mock-case-toolbar">
            <el-select v-model="selectedMockCaseId" size="small" clearable placeholder="Mock Case" style="width:220px" @change="clearMockCache">
              <el-option v-for="item in mockCases" :key="item.id" :label="item.name || item.id" :value="item.id" />
            </el-select>
            <el-button size="small" :loading="mockCaseLoading" :disabled="!local.api_id" @click="loadMockCases">刷新 Case</el-button>
            <el-button size="small" type="primary" plain :disabled="!local.api_id" @click="saveAssertSampleAsMockCase">保存样本为 Case</el-button>
            <el-button size="small" :disabled="!local.api_id" @click="createMockCaseFromDoc">从文档生成 Case</el-button>
          </div>
          <el-collapse class="assert-dryrun-box">
            <el-collapse-item title="响应样本 / 断言试算" name="assert-dryrun">
              <el-input v-model="assertSampleText" type="textarea" :rows="6" class="se-code" placeholder='{"status_code":200,"headers":{},"body":{},"latency_ms":100}' />
              <div v-if="assertSampleFields.length" class="assert-field-panel">
                <div class="sql-result-title">响应字段</div>
                <el-table :data="assertSampleFields" size="small" max-height="180">
                  <el-table-column prop="path" label="字段" min-width="160" />
                  <el-table-column label="值" min-width="160">
                    <template #default="{ row }"><span class="mono">{{ compactJsonText(row.value, 48) }}</span></template>
                  </el-table-column>
                  <el-table-column label="操作" width="92">
                    <template #default="{ row }">
                      <el-button size="small" link type="primary" @click="applySampleAssert(row)">添加断言</el-button>
                    </template>
                  </el-table-column>
                </el-table>
              </div>
              <div v-if="assertDryRunResult" class="assert-result-panel">
                <div class="sql-result-title">
                  试算结果：{{ assertDryRunResult.passed || 0 }}/{{ assertDryRunResult.total || 0 }}
                </div>
                <el-table :data="assertDryRunResult.results || []" size="small" max-height="220">
                  <el-table-column prop="field" label="字段" min-width="140" />
                  <el-table-column prop="operator" label="Operator" width="110" />
                  <el-table-column label="Actual" min-width="140">
                    <template #default="{ row }"><span class="mono">{{ compactJsonText(row.actual, 60) }}</span></template>
                  </el-table-column>
                  <el-table-column label="Expected" min-width="140">
                    <template #default="{ row }"><span class="mono">{{ compactJsonText(row.expected, 60) }}</span></template>
                  </el-table-column>
                  <el-table-column label="结果" width="80">
                    <template #default="{ row }">
                      <el-tag size="small" :type="row.passed ? 'success' : 'danger'">{{ row.passed ? 'Pass' : 'Fail' }}</el-tag>
                    </template>
                  </el-table-column>
                </el-table>
              </div>
            </el-collapse-item>
          </el-collapse>
          <div v-if="aiPreview.asserts.length" class="ai-preview">
            <div class="ai-preview-title">AI 推荐断言预览</div>
            <div v-for="(a, i) in aiPreview.asserts" :key="`${a.field}-${i}`" class="ai-preview-row">
              <span class="mono">{{ a.field }}</span>
              <span>{{ a.operator }}</span>
              <span class="mono">{{ String(a.expected ?? '') }}</span>
              <el-button size="small" link type="primary" @click="applyRecommendAssert(a)">应用</el-button>
            </div>
          </div>
          <div v-if="mockPreview.asserts.length" class="ai-preview">
            <div class="ai-preview-title">Mock 断言预览</div>
            <div v-for="(a, i) in mockPreview.asserts" :key="`${a.path}-${i}`" class="ai-preview-row">
              <span class="mono">{{ a.path }}</span>
              <span>{{ a.operator }}</span>
              <span class="mono">{{ truncateText(String(a.expected ?? ''), 32) }}</span>
              <el-button size="small" link type="primary" @click="applyMockAssert(a)">应用</el-button>
            </div>
            <el-button size="small" link type="primary" @click="applyAllMockAsserts">全部应用</el-button>
          </div>
        </div>
      </el-tab-pane>

      <!-- 6. Pre-request Script -->
      <el-tab-pane :label="$t('step_editor.tab_pre_script')" name="pre_script">
        <div class="se-tab-body">
          <div class="script-toolbar">
            <el-select v-model="scriptTemplateKey" size="small" placeholder="脚本模板" style="width:180px">
              <el-option v-for="tpl in scriptTemplates" :key="tpl.key" :label="tpl.label" :value="tpl.key" />
            </el-select>
            <el-button size="small" @click="applyScriptTemplate('pre')">插入模板</el-button>
            <el-button size="small" type="primary" :loading="scriptDryRunLoading" @click="dryRunScript('pre')">试运行</el-button>
          </div>
          <el-input
            v-model="local.pre_script"
            type="textarea"
            :rows="10"
            :placeholder="$t('step_editor.pre_script_placeholder')"
            class="se-code"
          />
          <div class="script-samples">
            <el-input v-model="scriptContextText" type="textarea" :rows="4" class="se-code" placeholder='Context JSON，如 {"user_id":1}' />
          </div>
          <div v-if="scriptDryRunResult" class="script-result-panel">
            <div class="sql-result-title">脚本试运行结果</div>
            <pre class="code-block preview-json">{{ JSON.stringify(scriptDryRunResult, null, 2) }}</pre>
          </div>
        </div>
      </el-tab-pane>

      <!-- 7. Post-response Script -->
      <el-tab-pane :label="$t('step_editor.tab_post_script')" name="post_script">
        <div class="se-tab-body">
          <div class="script-toolbar">
            <el-select v-model="scriptTemplateKey" size="small" placeholder="脚本模板" style="width:180px">
              <el-option v-for="tpl in scriptTemplates" :key="tpl.key" :label="tpl.label" :value="tpl.key" />
            </el-select>
            <el-button size="small" @click="applyScriptTemplate('post')">插入模板</el-button>
            <el-button size="small" type="primary" :loading="scriptDryRunLoading" @click="dryRunScript('post')">试运行</el-button>
          </div>
          <el-input
            v-model="local.post_script"
            type="textarea"
            :rows="10"
            :placeholder="$t('step_editor.post_script_placeholder')"
            class="se-code"
          />
          <div class="script-samples">
            <el-input v-model="scriptContextText" type="textarea" :rows="4" class="se-code" placeholder='Context JSON，如 {"page":1}' />
            <el-input v-model="scriptResponseText" type="textarea" :rows="4" class="se-code" placeholder='Response JSON，如 {"data":{"token":"abc"}}' />
          </div>
          <div v-if="scriptDryRunResult" class="script-result-panel">
            <div class="sql-result-title">脚本试运行结果</div>
            <pre class="code-block preview-json">{{ JSON.stringify(scriptDryRunResult, null, 2) }}</pre>
          </div>
        </div>
      </el-tab-pane>

      <!-- 8. SQL -->
      <el-tab-pane :label="tabLabel('sql', sqlQueries.length)" name="sql">
        <div class="se-tab-body">
          <el-alert type="info" :closable="false" show-icon class="sql-help">
            <template #title>{{ $t('step_editor.sql_help') }}</template>
          </el-alert>
          <div class="sql-toolbar">
            <el-button size="small" :icon="Plus" @click="addSqlQuery('pre')">请求前 SQL</el-button>
            <el-button size="small" :icon="Plus" @click="addSqlQuery('post')">响应后 SQL</el-button>
          </div>
          <div v-if="!sqlQueries.length" class="text-3 se-empty">尚未配置 SQL 查询</div>
          <div v-for="(q, i) in sqlQueries" :key="q._key || i" class="sql-query-card">
            <div class="sql-query-head">
              <el-select v-model="q.phase" size="small" style="width:112px">
                <el-option label="请求前" value="pre" />
                <el-option label="响应后" value="post" />
              </el-select>
              <el-input v-model="q.name" size="small" placeholder="结果名，如 user" />
              <el-switch v-model="q.fail_on_error" size="small" active-text="失败阻断" />
              <el-button size="small" @click="validateSqlQuery(q)">校验</el-button>
              <el-button size="small" type="primary" @click="runSqlQuery(q)">试运行</el-button>
              <el-button size="small" :icon="Delete" circle @click="removeSqlQuery(i)" />
            </div>
            <div class="sql-query-grid">
              <el-select v-model="q.sql_ref" size="small" filterable clearable placeholder="选择 SQL 片段">
                <el-option v-for="snip in sqlSnippets" :key="snip.id" :label="snip.name" :value="snip.id" />
              </el-select>
              <el-select v-if="!q.sql_ref" v-model="q.db_service_id" size="small" filterable clearable placeholder="数据库服务">
                <el-option v-for="svc in dbServices" :key="svc.id" :label="svc.name" :value="svc.id" />
              </el-select>
              <el-input v-model="q.target_var" size="small" placeholder="上下文变量名，默认同结果名" />
              <el-input-number v-model.number="q.max_rows" :min="1" :max="1000" size="small" placeholder="行数" style="width:100%" />
            </div>
            <el-input
              v-if="!q.sql_ref"
              v-model="q.sql_text"
              type="textarea"
              :rows="4"
              class="se-code"
              placeholder="SELECT ..."
            />
            <div class="sql-query-grid">
              <el-input v-model="q.field" size="small" placeholder="默认字段路径，如 sql.user.scalar" />
              <el-input-number v-model.number="q.timeout_ms" :min="500" :max="30000" :step="500" size="small" placeholder="超时 ms" style="width:100%" />
            </div>
            <el-input
              v-model="q.paramsText"
              type="textarea"
              :rows="2"
              class="se-code"
              placeholder='参数 JSON，如 {"id":"{{user_id}}"}'
            />
          </div>
          <div v-if="sqlRunResult" class="sql-result-panel">
            <div class="sql-result-title">SQL 结果</div>
            <el-table v-if="sqlRunResult.rows?.length" :data="sqlRunResult.rows" size="small" max-height="180">
              <el-table-column v-for="col in sqlRunResult.columns || []" :key="col" :prop="col" :label="col" min-width="120" />
            </el-table>
            <pre class="code-block sql-result-json">{{ JSON.stringify(sqlRunResult, null, 2) }}</pre>
          </div>
        </div>
      </el-tab-pane>

      <!-- 9. Extract -->
      <el-tab-pane :label="tabLabel('extract', Object.keys(local.extract || {}).length)" name="extract">
        <div class="se-tab-body">
          <div v-if="normalizedVariableOptions.length" class="var-toolbar">
            <span class="var-toolbar-label">变量</span>
            <el-button
              v-for="item in normalizedVariableOptions"
              :key="item.value"
              size="small"
              link
              type="primary"
              @click="copyVariable(item.value)"
            >{{ item.label }}</el-button>
          </div>
          <KeyValueEditor
            v-model="local.extract"
            :key-placeholder="$t('step_editor.extract_var')"
            :value-placeholder="$t('step_editor.extract_jsonpath')"
          />
          <el-collapse class="advanced-extract">
            <el-collapse-item :title="$t('step_editor.extract_advanced')" name="advanced">
              <div class="sql-extract-toolbar">
                <el-input v-model="sqlExtractVar" size="small" placeholder="变量名，如 user_id" />
                <el-select v-model="sqlExtractQuery" size="small" filterable clearable placeholder="选择 SQL 查询">
                  <el-option v-for="q in sqlQueries" :key="q._key" :label="q.name || q.target_var || q._key" :value="q.name || q.target_var || q._key" />
                </el-select>
                <el-input v-model="sqlExtractField" size="small" placeholder="字段路径，如 sql.user.first.id" />
                <el-button size="small" @click="addSqlExtract">添加 SQL 提取</el-button>
              </div>
              <el-input
                v-model="extractText"
                type="textarea"
                :rows="6"
                class="se-code"
                :placeholder="$t('step_editor.extract_advanced_placeholder')"
              />
            </el-collapse-item>
          </el-collapse>
          <!-- P1-4: AI 推荐 extract 规则（基于 API 响应样本） -->
          <el-button size="small" @click="aiRecommendExtract" :disabled="aiRecommending || !local.api_id" style="margin-top:8px">
            {{ aiRecommending ? $t('step_editor.ai_recommending') : '✨ ' + $t('step_editor.ai_recommend_extract') }}
          </el-button>
          <el-button size="small" @click="mockSuggestExtract" :disabled="mockLoading || !local.api_id" style="margin-top:8px;margin-left:8px">
            {{ mockLoading ? 'Mock 生成中' : '从 Mock 生成提取' }}
          </el-button>
          <div v-if="Object.keys(aiPreview.extract).length" class="ai-preview">
            <div class="ai-preview-title">AI 推荐提取预览</div>
            <div v-for="(jpath, varName) in aiPreview.extract" :key="varName" class="ai-preview-row">
              <span class="mono">{{ varName }}</span>
              <span class="mono">{{ jpath }}</span>
              <el-button size="small" link type="primary" @click="applyRecommendExtract(varName, jpath)">应用</el-button>
            </div>
          </div>
          <div v-if="Object.keys(mockPreview.extract).length" class="ai-preview">
            <div class="ai-preview-title">Mock 提取预览</div>
            <div v-for="(jpath, varName) in mockPreview.extract" :key="varName" class="ai-preview-row">
              <span class="mono">{{ varName }}</span>
              <span class="mono">{{ jpath }}</span>
              <el-button size="small" link type="primary" @click="applyMockExtract(varName, jpath)">应用</el-button>
            </div>
            <el-button size="small" link type="primary" @click="applyAllMockExtract">全部应用</el-button>
          </div>
          <!-- 变量交叉引用：展示每个 extract 变量被哪些步骤引用 -->
          <div v-if="Object.keys(local.extract).length && extractUsage" class="extract-usage">
            <div v-for="(jsonpath, varName) in local.extract" :key="varName" class="extract-usage-item">
              <span class="extract-usage-var mono">{{ varName }}</span>
              <template v-if="extractUsage[varName]?.length">
                <span class="extract-usage-label">{{ $t('step_editor.extract_referenced_by') }}</span>
                <el-button
                  v-for="sid in extractUsage[varName]"
                  :key="sid"
                  link
                  type="primary"
                  size="small"
                  class="mono"
                  @click="emit('navigateStep', sid)"
                >{{ sid }}</el-button>
              </template>
              <span v-else class="extract-usage-label">{{ $t('step_editor.extract_not_referenced') }}</span>
            </div>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>

    <template #footer>
      <div class="preview-footer">
        <el-button size="small" :loading="previewLoading" :disabled="!props.scenarioId || !local.api_id" @click="previewRequest">预览请求</el-button>
        <el-tag v-if="requestPreview?.unresolved_refs?.length" size="small" type="warning">未解析 {{ requestPreview.unresolved_refs.length }}</el-tag>
        <el-tag v-else-if="requestPreview" size="small" type="success">可执行</el-tag>
      </div>
      <el-button @click="emit('update:visible', false)">{{ $t('common.cancel') }}</el-button>
      <el-button type="primary" @click="confirm">{{ $t('common.confirm') }}</el-button>
    </template>
    <div v-if="requestPreview" class="request-preview-panel">
      <div class="request-preview-head">
        <span>请求预览</span>
        <span class="mono text-3">{{ requestPreview.request?.method }} {{ requestPreview.request?.url }}</span>
      </div>
      <div v-if="authPreviewSummary.length" class="auth-preview-summary">
        <el-tag v-for="item in authPreviewSummary" :key="item" size="small" effect="plain">{{ item }}</el-tag>
      </div>
      <el-alert
        v-if="requestPreview.unresolved_refs?.length"
        type="warning"
        :closable="false"
        show-icon
        class="preview-alert"
      >
        <template #title>未解析变量：{{ requestPreview.unresolved_refs.join(', ') }}</template>
      </el-alert>
      <el-tabs model-value="request" class="preview-tabs">
        <el-tab-pane label="请求" name="request">
          <pre class="code-block preview-json">{{ JSON.stringify(requestPreview.request, null, 2) }}</pre>
        </el-tab-pane>
        <el-tab-pane label="脚本变量" name="scripts">
          <pre class="code-block preview-json">{{ JSON.stringify(requestPreview.script_results || [], null, 2) }}</pre>
        </el-tab-pane>
        <el-tab-pane label="鉴权来源" name="auth">
          <pre class="code-block preview-json">{{ JSON.stringify({ auth_sources: requestPreview.auth_sources, header_sources: requestPreview.header_sources }, null, 2) }}</pre>
        </el-tab-pane>
        <el-tab-pane label="上下文" name="context">
          <pre class="code-block preview-json">{{ JSON.stringify(requestPreview.context || {}, null, 2) }}</pre>
        </el-tab-pane>
      </el-tabs>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch, onMounted, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { Delete, Plus } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { apiApi, databaseServiceApi, environmentApi, executionApi, scenarioApi, sqlApi, sqlSnippetApi } from '@/api'
import KeyValueEditor from '@/components/KeyValueEditor.vue'
import { useProjectStore } from '@/stores'
import type { ScenarioStep } from '@/types'

const { t } = useI18n()
const projectStore = useProjectStore()

const props = defineProps<{
  visible: boolean
  step: ScenarioStep
  apiList?: any[]
  availableSteps?: string[]  // 可选的步骤 ID 列表，用于 depends_on 下拉选择器
  extractUsage?: Record<string, string[]>  // var_name → 引用该变量的 step_id 列表，供 Extract 标签页展示交叉引用
  variableOptions?: Array<{ label: string; value: string }>
  scenarioId?: string
}>()

const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  (e: 'confirm', step: ScenarioStep): void
  (e: 'navigateStep', stepId: string): void  // 交叉引用点击：跳转到引用步骤的编辑
}>()

const activeTab = ref('params')

// P2-1: Tab label 带计数徽章 —— 让用户一眼看到该 Tab 配置了多少项
function tabLabel(tabKey, count) {
  const base = t('step_editor.tab_' + tabKey)
  return count > 0 ? `${base} (${count})` : base
}

// 深拷贝 step 到本地对象，避免直接修改 props
const local = reactive<ScenarioStep>({
  step_id: '',
  api_id: '',
  name: '',
  type: 'api',
  parent_id: '',
  depends_on: [],
  extract: {},
  override_params: {},
  override_headers: {},
  retry: 0,
  retry_delay_s: 1,
  timeout_s: 30,
  condition: null,
  loop: null,
  loop_var: null,
  loop_count: null,
  wait_ms: 0,
  data_template_id: '',
  auth: {},
  pre_script: '',
  post_script: '',
  pre_sql: [],
  post_sql: [],
  assertions: [],
})

// Body 编辑（通过 override_params._body 持久化）
const bodyType = ref<'none' | 'json' | 'raw'>('none')
const bodyContent = ref('')
const bodyError = ref(false)
const extractText = ref('{}')
const dbServices = ref<any[]>([])
const sqlSnippets = ref<any[]>([])
const environments = ref<any[]>([])
const sqlQueries = ref<any[]>([])
const sqlRunResult = ref<any | null>(null)
const sqlExtractVar = ref('')
const sqlExtractQuery = ref('')
const sqlExtractField = ref('sql.<name>.scalar')
const previewLoading = ref(false)
const requestPreview = ref<any | null>(null)
const authTokenSource = ref('manual')
const authTokenVar = ref('')
const selectedAuthTemplateKey = ref('')
const scriptTemplateKey = ref('extract_token')
const scriptDryRunLoading = ref(false)
const scriptDryRunResult = ref<any | null>(null)
const scriptContextText = ref('{\n  "user_id": 1,\n  "secret": "demo-secret"\n}')
const scriptResponseText = ref('{\n  "data": {\n    "token": "demo-token",\n    "next_cursor": "cursor-1"\n  }\n}')
const assertDryRunLoading = ref(false)
const assertDryRunResult = ref<any | null>(null)
const recentSampleLoading = ref(false)
const assertSampleText = ref(JSON.stringify({
  status_code: 200,
  latency_ms: 120,
  headers: { 'content-type': 'application/json' },
  body: { code: 0, data: { id: 1, token: 'demo-token' } },
}, null, 2))
const PARAM_META_KEYS = new Set(['_query', '_path', '_body_params', '_vars', '_body', '_body_type'])
const paramEditor = reactive<Record<string, Record<string, any>>>({
  query: {},
  path: {},
  body: {},
  vars: {},
  legacy: {},
})
const scriptTemplates = [
  {
    key: 'extract_token',
    label: '提取 token',
    script: JSON.stringify([{ op: 'set', var: 'token', from: 'response', path: '$.data.token' }], null, 2),
  },
  {
    key: 'timestamp_nonce',
    label: '时间戳 + nonce',
    script: JSON.stringify([
      { op: 'timestamp', var: 'timestamp', unit: 'ms' },
      { op: 'random', var: 'nonce', kind: 'string', length: 16 },
    ], null, 2),
  },
  {
    key: 'signature',
    label: '生成签名',
    script: JSON.stringify([
      { op: 'timestamp', var: 'timestamp', unit: 'ms' },
      { op: 'random', var: 'nonce', kind: 'string', length: 12 },
      { op: 'hash', var: 'signature', value: '{{timestamp}}:{{nonce}}', algorithm: 'sha256', secret: '{{secret}}' },
    ], null, 2),
  },
  {
    key: 'next_cursor',
    label: '分页游标',
    script: JSON.stringify([{ op: 'set', var: 'next_cursor', from: 'response', path: '$.data.next_cursor' }], null, 2),
  },
]

const assertSampleFields = computed(() => {
  try {
    const sample = JSON.parse(assertSampleText.value || '{}')
    const body = sample?.body ?? {}
    return flattenJson(body)
      .filter(item => item.path !== '$' && isAssertLeafValue(item.value))
      .slice(0, 40)
  } catch {
    return []
  }
})

const authPreviewSummary = computed(() => {
  const auth = requestPreview.value?.auth_sources || requestPreview.value?.request?.auth_sources || {}
  const headerSources = requestPreview.value?.header_sources || requestPreview.value?.request?.header_sources || {}
  const items: string[] = []
  if (auth.type) items.push(`Auth: ${auth.type}`)
  if (auth.applied?.length) items.push(`鉴权注入 ${auth.applied.length}`)
  if (auth.skipped?.length) items.push(`鉴权跳过 ${auth.skipped.length}`)
  const manualHeaders = Object.values(headerSources).filter(v => v === 'manual').length
  const envHeaders = Object.values(headerSources).filter(v => v === 'environment').length
  if (manualHeaders) items.push(`手动 Header ${manualHeaders}`)
  if (envHeaders) items.push(`环境 Header ${envHeaders}`)
  return items
})

const authTemplateOptions = computed(() => {
  const rows: Array<{ key: string; label: string; template: any }> = []
  for (const env of environments.value || []) {
    for (const [idx, tpl] of (env.auth_templates || []).entries()) {
      rows.push({
        key: `${env.id}:${idx}`,
        label: `${env.name || env.id} / ${tpl.name || tpl.type || 'Auth'}`,
        template: tpl,
      })
    }
  }
  return rows
})

watch(() => local.extract, (value) => {
  extractText.value = JSON.stringify(value || {}, null, 2)
}, { deep: true })

// 将 step 数据同步到 local 响应式状态
// 提取为独立函数以便 visible 和 step 变化时复用
function syncStepToLocal() {
  if (!props.visible) return
  const s = props.step
  if (!s.step_id) return
  Object.assign(local, {
    step_id: s.step_id || '',
    api_id: s.api_id || '',
    name: s.name || '',
    type: s.type || 'api',
    parent_id: (s as any).parent_id || '',
    depends_on: [...(s.depends_on || [])],
    extract: { ...(s.extract || {}) },
    override_params: { ...(s.override_params || {}) },
    override_headers: { ...(s.override_headers || {}) },
    retry: s.retry ?? 0,
    retry_delay_s: s.retry_delay_s ?? 1,
    timeout_s: s.timeout_s ?? 30,
    condition: s.condition ? { ...s.condition } : null,
    loop: (s as any).loop ? { ...(s as any).loop } : null,
    loop_var: s.loop_var ?? null,
    loop_count: s.loop_count ?? null,
    wait_ms: s.wait_ms ?? 0,
    data_template_id: s.data_template_id || '',
    auth: { ...(s.auth || {}) },
    pre_script: s.pre_script || '',
    post_script: s.post_script || '',
    pre_sql: (s as any).pre_sql || [],
    post_sql: (s as any).post_sql || [],
    assertions: (s.assertions || []).map(a => {
      const item: any = { ...a, sql_query: (a as any).sql_query ? { ...(a as any).sql_query } : undefined }
      if (item.source === 'sql') ensureSqlAssertion(item)
      if (item.operator === 'json_schema') item.expectedText = JSON.stringify(item.expected || {}, null, 2)
      if (assertionExpectedType(item) === 'multi' && !Array.isArray(item.expected)) item.expected = item.expected ? [item.expected] : []
      return item
    }),
  })
  syncSqlQueriesFromStep(s)
  extractText.value = JSON.stringify(s.extract || {}, null, 2)
  requestPreview.value = null
  inferAuthTokenSource()
  mockPreview.body = null
  mockPreview.asserts = []
  mockPreview.extract = {}
  selectedMockCaseId.value = ''
  mockCases.value = []
  syncParamEditorFromLocal()
  const currentApi = (props.apiList || []).find(a => a.id === local.api_id)
  seedParamEditorFromApi(currentApi, false)
  loadSqlAssets()
  loadMockCases()
  syncBodyFromLocal()
}

// 修复：同时监听 visible 和 step.step_id，解决首次打开编辑器时 step 尚未解析导致内容为空的问题。
// visible 从 false→true 时触发，step 从 {}→真实数据时也会触发。
// 使用 nextTick 确保父组件（Detail.vue）所有响应式更新已稳定后再同步数据，避免 TDZ 等错误中断 Object.assign。
watch(() => [props.visible, (props.step || {}).step_id], async () => {
  await nextTick()
  syncStepToLocal()
}, { immediate: true })

async function loadSqlAssets() {
  try {
    const [svcRes, snipRes, envRes] = await Promise.all([
      databaseServiceApi.list(projectStore.current),
      sqlSnippetApi.list({ project_id: projectStore.current }),
      environmentApi.list(projectStore.current),
    ])
    dbServices.value = svcRes.items || []
    sqlSnippets.value = snipRes.items || []
    environments.value = envRes.items || envRes || []
  } catch (e) {
    console.warn('load sql assets failed:', e)
    ElMessage.error('加载 SQL 资源失败: ' + (e.message || e))
  }
}

function newSqlKey() {
  return `${Date.now()}_${Math.random().toString(16).slice(2)}`
}

function normalizeSqlQuery(q: any = {}, phase: 'pre' | 'post' = 'pre') {
  const name = q.target_var || q.name || ''
  return {
    _key: q._key || newSqlKey(),
    phase: q.phase || phase,
    name,
    target_var: q.target_var || '',
    db_service_id: q.db_service_id || '',
    sql_ref: q.sql_ref || '',
    sql_text: q.sql_text || '',
    params: q.params || {},
    paramsText: JSON.stringify(q.params || {}, null, 2),
    field: q.field || q.path || '',
    timeout_ms: q.timeout_ms || 5000,
    max_rows: q.max_rows || 100,
    fail_on_error: q.fail_on_error !== false,
  }
}

function syncSqlQueriesFromStep(s: any) {
  const pre = (s.pre_sql || []).map((q: any) => normalizeSqlQuery(q, 'pre'))
  const post = (s.post_sql || []).map((q: any) => normalizeSqlQuery(q, 'post'))
  sqlQueries.value = [...pre, ...post]
  sqlRunResult.value = null
}

function addSqlQuery(phase: 'pre' | 'post' = 'pre') {
  sqlQueries.value.push(normalizeSqlQuery({ phase, db_service_id: dbServices.value[0]?.id || '' }, phase))
}

function removeSqlQuery(index: number) {
  sqlQueries.value.splice(index, 1)
}

function parseJsonText(text: string, fallback: any = {}) {
  const raw = (text || '').trim()
  if (!raw) return fallback
  return JSON.parse(raw)
}

function serializeSqlQuery(q: any) {
  const params = parseJsonText(q.paramsText, {})
  const item: Record<string, any> = {
    name: q.name || q.target_var || '',
    target_var: q.target_var || q.name || '',
    params,
    field: q.field || '',
    timeout_ms: q.timeout_ms || 5000,
    max_rows: q.max_rows || 100,
    fail_on_error: q.fail_on_error !== false,
  }
  if (q.sql_ref) item.sql_ref = q.sql_ref
  else {
    item.db_service_id = q.db_service_id || ''
    item.sql_text = q.sql_text || ''
  }
  return item
}

function splitSqlQueries() {
  const preSql: any[] = []
  const postSql: any[] = []
  for (const q of sqlQueries.value) {
    const item = serializeSqlQuery(q)
    if (q.phase === 'post') postSql.push(item)
    else preSql.push(item)
  }
  return { preSql, postSql }
}

function sqlQueryPayload(q: any) {
  const params = parseJsonText(q.paramsText, {})
  const base: Record<string, any> = {
    project_id: projectStore.current,
    params,
    max_rows: q.max_rows,
    timeout_ms: q.timeout_ms,
  }
  if (q.sql_ref) return { ...base, query: { sql_ref: q.sql_ref, params } }
  return { ...base, db_service_id: q.db_service_id, sql_text: q.sql_text }
}

async function validateSqlQuery(q: any) {
  try {
    sqlRunResult.value = await sqlApi.validate(sqlQueryPayload(q))
    ElMessage.success('SQL 校验通过')
  } catch (e: any) {
    ElMessage.error(e.message || 'SQL 校验失败')
  }
}

async function runSqlQuery(q: any) {
  try {
    const params = parseJsonText(q.paramsText, {})
    sqlRunResult.value = q.sql_ref
      ? await sqlSnippetApi.run(q.sql_ref, { params })
      : await sqlApi.run({
        project_id: projectStore.current,
        db_service_id: q.db_service_id,
        sql_text: q.sql_text,
        params,
        max_rows: q.max_rows,
        timeout_ms: q.timeout_ms,
      })
    if (sqlRunResult.value?.ok === false) ElMessage.error(sqlRunResult.value.message || 'SQL 执行失败')
    else ElMessage.success('SQL 试运行完成')
  } catch (e: any) {
    ElMessage.error(e.message || 'SQL 试运行失败')
  }
}

function ensureSqlAssertion(a: any) {
  a.sql_query = a.sql_query || {
    name: a.sqlName || 'assert_sql',
    sql_ref: a.sqlRef || '',
    sql_text: a.sqlText || '',
    db_service_id: a.dbServiceId || dbServices.value[0]?.id || '',
    params: a.params || {},
  }
  a.sqlParamsText = a.sqlParamsText || JSON.stringify(a.sql_query.params || {}, null, 2)
}

function onAssertionSourceChange(a: any) {
  if (a.source === 'sql') ensureSqlAssertion(a)
  if (a.source === 'performance') {
    a.path = '$response_time_ms'
    if (!a.operator || a.operator === 'eq') a.operator = 'response_time_lt'
    normalizeExpectedForOperator(a)
  }
  if (a.source === 'status' && !a.path) a.path = 'status_code'
}

function addSqlExtract() {
  const varName = sqlExtractVar.value.trim()
  const queryName = sqlExtractQuery.value.trim()
  if (!varName || !queryName) {
    ElMessage.warning('请填写变量名并选择 SQL 查询')
    return
  }
  const source = sqlQueries.value.find(q => (q.name || q.target_var || q._key) === queryName)
  if (!source) return
  const field = (sqlExtractField.value || '').replace('<name>', queryName) || `sql.${queryName}.scalar`
  const query = serializeSqlQuery(source)
  local.extract[varName] = { source: 'sql', sql_query: query, field, fail_on_error: false }
  extractText.value = JSON.stringify(local.extract || {}, null, 2)
}

function assignObject(target: Record<string, any>, value: Record<string, any> = {}) {
  for (const key of Object.keys(target)) delete target[key]
  for (const [key, val] of Object.entries(value || {})) target[key] = val
}

function currentApiFromProps() {
  return (props.apiList || []).find(a => a.id === local.api_id) || null
}

function pathParamNames(api: any = null) {
  const targetApi = api || currentApiFromProps()
  const url = targetApi?.request?.url || targetApi?.request?.path || ''
  const names = new Set<string>()
  for (const match of String(url).matchAll(/(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})|:([A-Za-z_][A-Za-z0-9_]*)/g)) {
    names.add(match[1] || match[2])
  }
  return [...names]
}

function docParamsByLocation(location: string, api: any = null) {
  const targetApi = api || currentApiFromProps()
  return (targetApi?.doc?.params || []).filter((p: any) => String(p.location || '').toLowerCase() === location)
}

function simpleBodyExample(api: any) {
  const body = api?.request?.body
  return body && typeof body === 'object' && !Array.isArray(body) ? body : {}
}

function splitOverrideParams(params: Record<string, any> = {}) {
  const source = params || {}
  const query = { ...(source._query && typeof source._query === 'object' ? source._query : {}) }
  const path = { ...(source._path && typeof source._path === 'object' ? source._path : {}) }
  const body = { ...(source._body_params && typeof source._body_params === 'object' ? source._body_params : {}) }
  const vars = { ...(source._vars && typeof source._vars === 'object' ? source._vars : {}) }
  const legacy: Record<string, any> = {}
  for (const [key, value] of Object.entries(source)) {
    if (!PARAM_META_KEYS.has(key)) legacy[key] = value
  }
  return { query, path, body, vars, legacy }
}

function classifyLegacyParams(split: any, api: any) {
  const queryNames = new Set([
    ...Object.keys(api?.request?.query_params || {}),
    ...((api?.doc?.params || []).filter((p: any) => String(p.location || '').toLowerCase() === 'query').map((p: any) => p.name)),
  ])
  const pathNames = new Set([
    ...pathParamNames(api),
    ...((api?.doc?.params || []).filter((p: any) => String(p.location || '').toLowerCase() === 'path').map((p: any) => p.name)),
  ])
  const bodyNames = new Set([
    ...Object.keys(simpleBodyExample(api)),
    ...((api?.doc?.params || []).filter((p: any) => String(p.location || '').toLowerCase() === 'body').map((p: any) => p.name)),
  ])
  for (const [key, value] of Object.entries({ ...(split.legacy || {}) })) {
    if (pathNames.has(key)) {
      split.path[key] = value
      delete split.legacy[key]
    } else if (queryNames.has(key)) {
      split.query[key] = value
      delete split.legacy[key]
    } else if (bodyNames.has(key)) {
      split.body[key] = value
      delete split.legacy[key]
    }
  }
  return split
}

function syncParamEditorFromLocal() {
  const split = classifyLegacyParams(splitOverrideParams(local.override_params || {}), currentApiFromProps())
  assignObject(paramEditor.query, split.query)
  assignObject(paramEditor.path, split.path)
  assignObject(paramEditor.body, split.body)
  assignObject(paramEditor.vars, split.vars)
  assignObject(paramEditor.legacy, split.legacy)
}

function seedParamEditorFromApi(api: any, force = false) {
  if (!api) {
    // 即使 API 数据不可用，也应尝试将 legacy 中的参数按名称模式推断到各分组，
    // 避免所有参数都堆在"其他兼容参数"区域，导致 Query/Path/Body 区域为空。
    seedFromLegacy()
    return
  }
  if (force || !Object.keys(paramEditor.query).length) {
    const querySeed: Record<string, any> = { ...(api.request?.query_params || {}) }
    for (const p of docParamsByLocation('query', api)) querySeed[p.name] = querySeed[p.name] ?? p.example ?? ''
    assignObject(paramEditor.query, querySeed)
    // 将 legacy 中与 query 同名的参数合并到 query，保留用户已填写的值
    mergeLegacyToGroup('query')
  }
  if (force || !Object.keys(paramEditor.path).length) {
    const pathSeed: Record<string, any> = {}
    for (const name of pathParamNames(api)) pathSeed[name] = ''
    for (const p of docParamsByLocation('path', api)) pathSeed[p.name] = pathSeed[p.name] ?? p.example ?? ''
    assignObject(paramEditor.path, pathSeed)
    // 将 legacy 中与 path 同名的参数合并到 path，保留用户已填写的值
    mergeLegacyToGroup('path')
  }
  if (force || !Object.keys(paramEditor.body).length) {
    const bodySeed: Record<string, any> = { ...simpleBodyExample(api) }
    for (const p of docParamsByLocation('body', api)) bodySeed[p.name] = bodySeed[p.name] ?? p.example ?? ''
    assignObject(paramEditor.body, bodySeed)
    // 将 legacy 中与 body 同名的参数合并到 body，保留用户已填写的值
    mergeLegacyToGroup('body')
  }
  // 剩余未匹配的 legacy 参数保留在"其他兼容参数"区域
}

// 将 legacy 中与目标分组同名的参数迁移到对应分组，保留用户实际填写值
function mergeLegacyToGroup(group: string) {
  for (const key of Object.keys(paramEditor.legacy || {})) {
    if (key in (paramEditor as any)[group]) {
      (paramEditor as any)[group][key] = paramEditor.legacy[key]
      delete paramEditor.legacy[key]
    }
  }
}

// 当 API 数据不可用时，尝试将 legacy 参数按名称模式推断到各分组
function seedFromLegacy() {
  for (const key of Object.keys(paramEditor.legacy || {})) {
    const val = paramEditor.legacy[key]
    // 路径参数通常以单数名词出现（如 id, user_id），长度较短
    // 这里简单地将值中包含 {{ 的变量移动到 vars，其余保留在 legacy
    if (typeof val === 'string' && /\{\{.*?\}\}/.test(val)) {
      // 可能是环境变量引用，保留在 legacy
    }
  }
}

function syncParamEditorToLocal() {
  const next: Record<string, any> = { ...(paramEditor.legacy || {}) }
  if (Object.keys(paramEditor.query || {}).length) next._query = { ...paramEditor.query }
  if (Object.keys(paramEditor.path || {}).length) next._path = { ...paramEditor.path }
  if (Object.keys(paramEditor.body || {}).length) next._body_params = { ...paramEditor.body }
  if (Object.keys(paramEditor.vars || {}).length) next._vars = { ...paramEditor.vars }
  const bp = local.override_params as any
  if (bp._body !== undefined) next._body = bp._body
  if (bp._body_type !== undefined) next._body_type = bp._body_type
  local.override_params = next
}

const missingPathParams = computed(() => {
  return pathParamNames().filter(name => paramEditor.path[name] === undefined || paramEditor.path[name] === '')
})

// 从 local.override_params._body 恢复 body 编辑状态
// _body 存在 → 根据 _body_type 切换 json/raw 模式；_body 不存在 → none 模式
function syncBodyFromLocal() {
  const bp = local.override_params as any
  if (bp._body) {
    // 对象类型 body 序列化为 JSON 字符串显示
    bodyContent.value = typeof bp._body === 'string' ? bp._body : JSON.stringify(bp._body, null, 2)
    bodyType.value = bp._body_type === 'raw' ? 'raw' : 'json'
    bodyError.value = false
  } else {
    // 无 _body 键 → 空白状态，body 类型设为 none
    bodyContent.value = ''
    bodyType.value = 'none'
    bodyError.value = false
  }
}

// 将 body 内容写回 local.override_params，三路分支处理不同 body 类型
function syncBodyToLocal() {
  const bp = local.override_params as any
  // none：清除 _body/_body_type 键，表示不覆盖请求体
  if (bodyType.value === 'none') {
    delete bp._body
    delete bp._body_type
    bodyError.value = false
  } else if (bodyType.value === 'json') {
    // json：先校验 JSON 格式 → 通过则写入，失败则设置 bodyError 阻止保存
    try {
      JSON.parse(bodyContent.value)
      bp._body = bodyContent.value
      bp._body_type = 'json'
      bodyError.value = false
    } catch {
      bodyError.value = true
    }
  } else {
    // raw：不做格式校验，原样写入文本内容
    bp._body = bodyContent.value
    bp._body_type = 'raw'
    bodyError.value = false
  }
}

// API 名称查找
const apiName = computed(() => {
  const list = props.apiList || []
  const api = list.find(a => a.id === local.api_id)
  return api ? (api.name || api.request?.path || '') : ''
})

const selectedApi = computed(() => {
  const list = props.apiList || []
  return list.find(a => a.id === local.api_id) || null
})

const normalizedVariableOptions = computed(() => {
  const seen = new Set<string>()
  const result: Array<{ label: string; value: string }> = []
  for (const item of props.variableOptions || []) {
    if (!item?.value || seen.has(item.value)) continue
    seen.add(item.value)
    result.push({ label: item.label || item.value, value: item.value })
  }
  return result
})

function apiOptionLabel(api: any) {
  const method = api.request?.method || 'GET'
  const path = api.request?.path || api.name || api.id
  return `${method} ${path}`
}

async function copyVariable(value: string) {
  try {
    await navigator.clipboard?.writeText(value)
    ElMessage.success(`已复制 ${value}`)
  } catch {
    ElMessage.info(value)
  }
}

function insertBodyVariable(value: string) {
  bodyContent.value = bodyContent.value ? `${bodyContent.value}${value}` : value
}

function insertAuthVariable(value: string) {
  if (local.auth.type === 'apikey') {
    local.auth.value = local.auth.value ? `${local.auth.value}${value}` : value
  } else if (local.auth.type === 'basic') {
    local.auth.password = local.auth.password ? `${local.auth.password}${value}` : value
  } else {
    local.auth.token = local.auth.token ? `${local.auth.token}${value}` : value
  }
}

function currentAuthTokenValue() {
  return local.auth.type === 'apikey' ? (local.auth.value || '') : (local.auth.token || '')
}

function setCurrentAuthTokenValue(value: string) {
  if (local.auth.type === 'apikey') local.auth.value = value
  else local.auth.token = value
}

function inferAuthTokenSource() {
  const value = currentAuthTokenValue()
  const match = String(value || '').match(/^\{\{(.+)\}\}$/)
  if (!match) {
    authTokenSource.value = 'manual'
    authTokenVar.value = ''
    return
  }
  const ref = match[1]
  if (ref.startsWith('env.')) {
    authTokenSource.value = 'env'
    authTokenVar.value = ref.slice(4)
  } else if (ref.startsWith('steps.login.')) {
    authTokenSource.value = 'login'
    authTokenVar.value = ref.slice('steps.login.'.length)
  } else if (ref.startsWith('extracted.')) {
    authTokenSource.value = 'extracted'
    authTokenVar.value = ref.slice('extracted.'.length)
  } else {
    authTokenSource.value = 'extracted'
    authTokenVar.value = ref
  }
}

function applyAuthTokenSource() {
  const name = String(authTokenVar.value || '').trim()
  if (authTokenSource.value === 'manual' || !name) return
  if (authTokenSource.value === 'env') setCurrentAuthTokenValue(`{{env.${name}}}`)
  else if (authTokenSource.value === 'login') setCurrentAuthTokenValue(`{{steps.login.${name}}}`)
  else setCurrentAuthTokenValue(name.startsWith('{{') ? name : `{{${name}}}`)
}

function applySelectedAuthTemplate() {
  const selected = authTemplateOptions.value.find(item => item.key === selectedAuthTemplateKey.value)
  if (!selected?.template) {
    ElMessage.warning('请选择鉴权模板')
    return
  }
  const { name, ...auth } = selected.template
  local.auth = { ...auth }
  inferAuthTokenSource()
  ElMessage.success(`已应用鉴权模板：${name || auth.type || 'Auth'}`)
}

function apiBodyPayload(api: any) {
  if (!api?.request?.body) return ''
  return typeof api.request.body === 'string' ? api.request.body : JSON.stringify(api.request.body, null, 2)
}

function restoreBodyFromApi() {
  const payload = apiBodyPayload(selectedApi.value)
  if (!payload) return
  bodyType.value = selectedApi.value?.request?.body_type === 'json' ? 'json' : 'raw'
  bodyContent.value = payload
  syncBodyToLocal()
}

function formatBodyJson() {
  try {
    bodyContent.value = JSON.stringify(JSON.parse(bodyContent.value || '{}'), null, 2)
    bodyError.value = false
  } catch {
    bodyError.value = true
  }
}

async function fillBodyFromMock() {
  if (!local.api_id) return
  mockLoading.value = true
  try {
    const body = await loadMockBody()
    bodyType.value = typeof body === 'string' ? 'raw' : 'json'
    bodyContent.value = typeof body === 'string' ? body : JSON.stringify(body, null, 2)
    syncBodyToLocal()
    ElMessage.success('已填充 Mock 数据')
  } catch (e: any) {
    ElMessage.error(e.message || 'Mock 数据填充失败')
  } finally {
    mockLoading.value = false
  }
}

// api_id 变更时自动填充
async function onApiChange() {
  if (!local.api_id) return
  mockPreview.body = null
  mockPreview.asserts = []
  mockPreview.extract = {}
  const list = props.apiList || []
  const api = list.find(a => a.id === local.api_id)
  if (!api) {
    // apiList 中未找到 → 尝试从服务器按 ID 加载（可能是其他项目的 API 或尚未加载的 API）
    try {
      const res = await apiApi.get(local.api_id)
      if (res) {
        if (!local.name) local.name = res.name || ''
        seedParamEditorFromApi(res, true)
        syncParamEditorToLocal()
        if (!Object.keys(local.override_headers).length) {
          local.override_headers = { ...(res.request?.headers || {}) }
        }
        // 断言为空时自动从 API 填充（仅首次自动填充，用户清空后不再填充）
        if (!local.assertions.length) {
          try {
            const asserts = await apiApi.getAsserts(local.api_id)
            if (asserts && Array.isArray(asserts)) {
              local.assertions = asserts.map((a: any) => ({
                source: a.field?.startsWith('$') ? 'performance' : 'response',
                path: a.field || '',
                operator: a.operator || 'eq',
                expected: a.expected ?? '',
              }))
            }
          } catch { /* 忽略断言加载失败 */ }
        }
      }
    } catch { /* 服务器加载失败，静默忽略：api_id 可能指向已删除或不存在的 API */ }
    return
  }
  if (!local.name) local.name = api.name || ''
  seedParamEditorFromApi(api, true)
  syncParamEditorToLocal()
  if (!Object.keys(local.override_headers).length) {
    local.override_headers = { ...(api.request?.headers || {}) }
  }
  if (api.request?.body && bodyType.value === 'none') {
    bodyType.value = api.request.body_type === 'json' ? 'json' : 'raw'
    bodyContent.value = apiBodyPayload(api)
    syncBodyToLocal()
  }
  // 断言为空时自动从 API 填充（仅首次自动填充，用户清空后不再填充）
  if (!local.assertions.length) {
    try {
      const asserts = await apiApi.getAsserts(local.api_id)
      if (asserts && Array.isArray(asserts)) {
        local.assertions = asserts.map((a: any) => ({
          source: a.field?.startsWith('$') ? 'performance' : 'response',
          path: a.field || '',
          operator: a.operator || 'eq',
          expected: a.expected ?? '',
        }))
      }
    } catch { /* 忽略断言加载失败 */ }
  }
}

function addAssertion() {
  local.assertions.push({ source: 'response', path: '', operator: 'eq', expected: '' })
}

function normalizeAssertion(a: any) {
  const item = { ...a }
  if (assertionExpectedType(item) === 'json') {
    try {
      item.expected = item.expectedText ? JSON.parse(item.expectedText) : {}
    } catch {
      throw new Error('assert expected json invalid')
    }
  }
  if (assertionExpectedType(item) === 'none') {
    item.expected = null
  }
  delete item.expectedText
  if (item.source === 'sql') {
    ensureSqlAssertion(item)
    item.sql_query.params = parseJsonText(item.sqlParamsText, {})
    delete item.sqlText
    delete item.sqlName
    delete item.sqlRef
    delete item.dbServiceId
    delete item.sqlParamsText
  }
  return item
}

// P1-4: AI 推荐状态 + 推荐断言/extract 函数（内联辅助，基于 API 响应样本）
const aiRecommending = ref(false)
const aiPreview = reactive<{ asserts: any[]; extract: Record<string, any>; summary: string }>({
  asserts: [],
  extract: {},
  summary: '',
})
const mockLoading = ref(false)
const mockCaseLoading = ref(false)
const mockCases = ref<any[]>([])
const selectedMockCaseId = ref('')
const mockPreview = reactive<{ body: any; asserts: any[]; extract: Record<string, any> }>({
  body: null,
  asserts: [],
  extract: {},
})
// P2-2: AI 推荐结果缓存 —— 断言/extract 共享同一次请求（后端一个接口返回两者）
// 按 api_id 缓存，切换步骤时自动失效；api_id 变化时在 watch 中清除
const aiRecommendCache = ref({})  // { [api_id]: { asserts, extract, summary } }

// P2-2: 获取推荐（带缓存），断言和 extract 共用
async function fetchAiRecommend() {
  if (!local.api_id) return null
  // 命中缓存直接返回
  if (aiRecommendCache.value[local.api_id]) {
    return aiRecommendCache.value[local.api_id]
  }
  const res = await scenarioApi.aiRecommend(local.api_id)
  if (res.error) return null
  const cached = { asserts: res.asserts || [], extract: res.extract || {}, summary: res.summary || '' }
  aiRecommendCache.value[local.api_id] = cached
  return cached
}

// AI 推荐断言：调后端获取建议，追加到 local.assertions（不覆盖已有）
async function aiRecommendAsserts() {
  if (!local.api_id) return
  aiRecommending.value = true
  try {
    const res = await fetchAiRecommend()
    if (!res) return
    aiPreview.asserts = res.asserts || []
    aiPreview.summary = res.summary || ''
  } catch (e) {
    console.warn('AI recommend asserts failed:', e)
    ElMessage.error('AI 推荐断言失败: ' + (e.message || e))
  } finally {
    aiRecommending.value = false
  }
}

// AI 推荐 extract：调后端获取建议，合并到 local.extract（已有 key 不覆盖）
async function aiRecommendExtract() {
  if (!local.api_id) return
  aiRecommending.value = true
  try {
    const res = await fetchAiRecommend()
    if (!res) return
    aiPreview.extract = { ...(res.extract || {}) }
    aiPreview.summary = res.summary || ''
  } catch (e) {
    console.warn('AI recommend extract failed:', e)
    ElMessage.error('AI 推荐提取字段失败: ' + (e.message || e))
  } finally {
    aiRecommending.value = false
  }
}

function applyRecommendAssert(a: any) {
  local.assertions.push({
    source: 'response',
    path: a.field || a.path || '',
    operator: a.operator || 'eq',
    expected: a.expected ?? '',
  })
}

function applyRecommendExtract(varName: string, jpath: any) {
  if (!varName) return
  local.extract[varName] = String(jpath || '')
}

function truncateText(value: string, max = 40) {
  return value.length > max ? `${value.slice(0, max)}...` : value
}

function compactJsonText(value: any, max = 40) {
  const text = value === undefined ? 'undefined' : JSON.stringify(value)
  return truncateText(text ?? 'null', max)
}

function isAssertLeafValue(value: any) {
  return value === null || ['string', 'number', 'boolean'].includes(typeof value)
}

function pathToVarName(path: string) {
  const cleaned = path
    .replace(/^\$\./, '')
    .replace(/\[0\]/g, '')
    .replace(/[^A-Za-z0-9_]+/g, '_')
    .replace(/^_+|_+$/g, '')
  return cleaned || 'value'
}

function flattenJson(value: any, prefix = '$'): Array<{ path: string; value: any }> {
  if (Array.isArray(value)) {
    if (!value.length) return [{ path: prefix, value }]
    return flattenJson(value[0], `${prefix}[0]`)
  }
  if (value && typeof value === 'object') {
    const rows: Array<{ path: string; value: any }> = []
    for (const [key, child] of Object.entries(value)) {
      rows.push(...flattenJson(child, prefix === '$' ? `$.${key}` : `${prefix}.${key}`))
    }
    return rows.length ? rows : [{ path: prefix, value }]
  }
  return [{ path: prefix, value }]
}

function makeAssertSampleFromStep(step: any) {
  const response = step?.response_received || {}
  if (!response || !Object.keys(response).length) return null
  return {
    status_code: response.status_code || 0,
    headers: response.headers || {},
    body: response.body,
    latency_ms: response.latency_ms ?? step.latency_ms ?? 0,
  }
}

async function loadRecentAssertSample() {
  const res = await executionApi.list({ api_id: local.api_id, limit: 10 })
  for (const exec of (res.items || [])) {
    const matched = (exec.steps || []).find((step: any) => step.api_id === local.api_id && step.response_received)
    const sample = makeAssertSampleFromStep(matched)
    if (sample) return sample
  }
  return null
}

async function loadMockBody() {
  if (mockPreview.body !== null) return mockPreview.body
  const res = await apiApi.mock(local.api_id, undefined, selectedMockCaseId.value || undefined)
  mockPreview.body = res.body
  return mockPreview.body
}

function clearMockCache() {
  mockPreview.body = null
  mockPreview.asserts = []
  mockPreview.extract = {}
}

async function loadMockCases() {
  if (!local.api_id) return
  mockCaseLoading.value = true
  try {
    const res = await apiApi.listMockCases(local.api_id)
    mockCases.value = res.items || []
  } catch (e) {
    console.warn('load mock cases failed:', e)
    ElMessage.error('加载 Mock Case 失败: ' + (e.message || e))
    mockCases.value = []
  } finally {
    mockCaseLoading.value = false
  }
}

async function saveAssertSampleAsMockCase() {
  if (!local.api_id) return
  try {
    const sample = parseScriptJsonSample(assertSampleText.value, '响应样本')
    const created = await apiApi.createMockCase(local.api_id, {
      name: `Case ${new Date().toLocaleString()}`,
      response: {
        status_code: sample.status_code || 200,
        headers: sample.headers || {},
        body: sample.body || {},
        latency_ms: sample.latency_ms || 0,
      },
    })
    await loadMockCases()
    selectedMockCaseId.value = created.id
    clearMockCache()
    ElMessage.success('已保存 Mock Case')
  } catch (e: any) {
    ElMessage.error(e.message || '保存 Mock Case 失败')
  }
}

async function createMockCaseFromDoc() {
  if (!local.api_id) return
  mockCaseLoading.value = true
  try {
    const created = await apiApi.createMockCase(local.api_id, { name: '文档默认 Case', from_doc: true })
    await loadMockCases()
    selectedMockCaseId.value = created.id
    clearMockCache()
    ElMessage.success('已从文档生成 Mock Case')
  } catch (e: any) {
    ElMessage.error(e.message || '生成 Mock Case 失败，请先完成 API 文档分析')
  } finally {
    mockCaseLoading.value = false
  }
}

async function mockSuggestAsserts() {
  if (!local.api_id) return
  mockLoading.value = true
  try {
    const body = await loadMockBody()
    mockPreview.asserts = flattenJson(body)
      .filter(item => item.path !== '$')
      .slice(0, 20)
      .map(item => ({
        source: 'response',
        path: item.path,
        operator: 'eq',
        expected: item.value,
      }))
    ElMessage.success(`已生成 ${mockPreview.asserts.length} 条 Mock 断言`)
  } catch (e: any) {
    ElMessage.error(e.message || 'Mock 断言生成失败，请先完成 API 文档分析')
  } finally {
    mockLoading.value = false
  }
}

async function fillAssertSampleFromMock() {
  if (!local.api_id) return
  mockLoading.value = true
  try {
    const res = await apiApi.mock(local.api_id, undefined, selectedMockCaseId.value || undefined)
    mockPreview.body = res.body
    assertSampleText.value = JSON.stringify({
      status_code: res.status_code || 200,
      headers: res.headers || { 'content-type': 'application/json' },
      body: res.body,
      latency_ms: 120,
    }, null, 2)
    ElMessage.success('已填充 Mock 响应样本')
  } catch (e: any) {
    ElMessage.error(e.message || 'Mock 样本加载失败')
  } finally {
    mockLoading.value = false
  }
}

async function fillAssertSampleFromRecent() {
  if (!local.api_id) return
  recentSampleLoading.value = true
  try {
    const sample = await loadRecentAssertSample()
    if (!sample) {
      ElMessage.warning('未找到最近响应，请先运行一次接口或场景')
      return
    }
    assertSampleText.value = JSON.stringify(sample, null, 2)
    ElMessage.success('已填充最近响应样本')
  } catch (e: any) {
    ElMessage.error(e.message || '最近响应加载失败')
  } finally {
    recentSampleLoading.value = false
  }
}

function applySampleAssert(row: { path: string; value: any }) {
  local.assertions.push({
    source: 'response',
    path: row.path,
    operator: 'eq',
    expected: row.value,
  })
  ElMessage.success(`已添加断言 ${row.path}`)
}

async function dryRunAssertions() {
  if (!local.assertions.length) return
  assertDryRunLoading.value = true
  try {
    const sample = parseScriptJsonSample(assertSampleText.value, '响应样本')
    const assertions = local.assertions.map(a => normalizeAssertion(a))
    assertDryRunResult.value = await apiApi.dryRunAsserts({ assertions, sample })
    if (assertDryRunResult.value?.ok) ElMessage.success('断言试算全部通过')
    else ElMessage.warning(`断言试算失败 ${assertDryRunResult.value?.failed || 0} 条`)
  } catch (e: any) {
    ElMessage.error(e.message || '断言试算失败')
  } finally {
    assertDryRunLoading.value = false
  }
}

async function mockSuggestExtract() {
  if (!local.api_id) return
  mockLoading.value = true
  try {
    const body = await loadMockBody()
    const rows = flattenJson(body).filter(item => item.path !== '$').slice(0, 20)
    mockPreview.extract = {}
    for (const item of rows) {
      mockPreview.extract[pathToVarName(item.path)] = item.path
    }
    ElMessage.success(`已生成 ${Object.keys(mockPreview.extract).length} 条 Mock 提取`)
  } catch (e: any) {
    ElMessage.error(e.message || 'Mock 提取生成失败，请先完成 API 文档分析')
  } finally {
    mockLoading.value = false
  }
}

function applyMockAssert(a: any) {
  local.assertions.push({
    source: a.source || 'response',
    path: a.path || '',
    operator: a.operator || 'eq',
    expected: a.expected,
  })
}

function applyAllMockAsserts() {
  mockPreview.asserts.forEach(applyMockAssert)
}

function applyMockExtract(varName: string, jpath: any) {
  if (!varName) return
  local.extract[varName] = String(jpath || '')
  extractText.value = JSON.stringify(local.extract || {}, null, 2)
}

function applyAllMockExtract() {
  for (const [varName, jpath] of Object.entries(mockPreview.extract)) {
    applyMockExtract(varName, jpath)
  }
}

// P0-2：步骤级断言操作符从后端单一来源加载（与 Detail.vue 一致），修复原 neq bug。
// 场景步骤断言主要用于快速校验，这里只暴露常用子集，避免下拉过长。
const stepAssertOps = ref([])
const assertTypeOptions = ['int', 'float', 'str', 'bool', 'list', 'dict', 'null']
function stepExpectedType(op) {
  const item = stepAssertOps.value.find(o => o.op === op)
  return item ? item.expected_type : 'text'
}
function assertionExpectedType(a: any) {
  if (a.operator === 'json_schema') return 'json'
  if (a.operator === 'type_match') return 'select_type'
  if (a.operator === 'in' || a.operator === 'not_in') return 'multi'
  if (['exists', 'not_exists', 'empty', 'not_empty'].includes(a.operator)) return 'none'
  if (['gt', 'gte', 'lt', 'lte', 'length', 'response_time_lt'].includes(a.operator)) return 'number'
  return stepExpectedType(a.operator)
}
function normalizeExpectedForOperator(a: any) {
  const type = assertionExpectedType(a)
  if (type === 'none') a.expected = null
  if (type === 'multi' && !Array.isArray(a.expected)) a.expected = a.expected ? [a.expected] : []
  if (type === 'select_type' && !a.expected) a.expected = 'str'
  if (type === 'json') a.expectedText = typeof a.expected === 'string' ? a.expected : JSON.stringify(a.expected || {}, null, 2)
}
function onAssertionOperatorChange(a: any) {
  if (a.operator === 'response_time_lt') {
    a.source = 'performance'
    a.path = '$response_time_ms'
  }
  normalizeExpectedForOperator(a)
}
async function loadStepAssertOps() {
  try {
    const res = await apiApi.assertOperators()
    stepAssertOps.value = res.operators || []
  } catch (e) {
    // 接口失败兜底：保留核心操作符，operator 值修正为后端规范。
    console.warn('load step assert ops failed, fallback', e)
    stepAssertOps.value = [
      { op: 'eq', group: 'compare', expected_type: 'text', help_zh: '' },
      { op: 'ne', group: 'compare', expected_type: 'text', help_zh: '' },
      { op: 'gt', group: 'compare', expected_type: 'number', help_zh: '' },
      { op: 'lt', group: 'compare', expected_type: 'number', help_zh: '' },
      { op: 'gte', group: 'compare', expected_type: 'number', help_zh: '' },
      { op: 'lte', group: 'compare', expected_type: 'number', help_zh: '' },
      { op: 'contains', group: 'string', expected_type: 'text', help_zh: '' },
      { op: 'exists', group: 'existence', expected_type: 'none', help_zh: '' },
      { op: 'response_time_lt', group: 'performance', expected_type: 'number', help_zh: '' },
      { op: 'type_match', group: 'type', expected_type: 'select_type', help_zh: '' },
    ]
  }
}
loadStepAssertOps()

function validateScriptText(script: string, phase: string) {
  const raw = (script || '').trim()
  if (!raw) return true
  if (!(raw.startsWith('[') || raw.startsWith('{'))) return true
  try {
    JSON.parse(raw)
    return true
  } catch {
    ElMessage.error(`${phase} 脚本 JSON 格式错误`)
    return false
  }
}

function validateStepConfig() {
  if (!local.api_id) {
    ElMessage.error('请选择 API')
    activeTab.value = 'params'
    return false
  }
  if (missingPathParams.value.length) {
    ElMessage.error(`Path 参数未填写：${missingPathParams.value.join(', ')}`)
    activeTab.value = 'params'
    return false
  }
  for (const [idx, assertion] of (local.assertions || []).entries()) {
    if (!assertion.operator) {
      ElMessage.error(`第 ${idx + 1} 条断言缺少 operator`)
      activeTab.value = 'assertions'
      return false
    }
    const expectedType = assertionExpectedType(assertion)
    const requiresPath = !['status', 'performance'].includes(assertion.source || '') && assertion.operator !== 'response_time_lt'
    if (requiresPath && !assertion.path) {
      ElMessage.error(`第 ${idx + 1} 条断言缺少字段路径`)
      activeTab.value = 'assertions'
      return false
    }
    if (expectedType !== 'none' && assertion.expected === '' && !assertion.expectedText) {
      ElMessage.warning(`第 ${idx + 1} 条断言期望值为空`)
    }
  }
  if (!validateScriptText(local.pre_script, 'pre_script')) {
    activeTab.value = 'pre_script'
    return false
  }
  if (!validateScriptText(local.post_script, 'post_script')) {
    activeTab.value = 'post_script'
    return false
  }
  return true
}

function selectedScriptTemplate() {
  return scriptTemplates.find(tpl => tpl.key === scriptTemplateKey.value) || scriptTemplates[0]
}

function applyScriptTemplate(phase: 'pre' | 'post') {
  const tpl = selectedScriptTemplate()
  if (!tpl) return
  if (phase === 'pre') local.pre_script = tpl.script
  else local.post_script = tpl.script
  scriptDryRunResult.value = null
}

function parseScriptJsonSample(text: string, label: string) {
  const raw = (text || '').trim()
  if (!raw) return {}
  try {
    return JSON.parse(raw)
  } catch {
    throw new Error(`${label} JSON 格式错误`)
  }
}

async function dryRunScript(phase: 'pre' | 'post') {
  const script = phase === 'pre' ? local.pre_script : local.post_script
  if (!script?.trim()) {
    ElMessage.warning('请先填写脚本')
    return
  }
  scriptDryRunLoading.value = true
  try {
    const context = parseScriptJsonSample(scriptContextText.value, 'Context')
    const response = phase === 'post' ? parseScriptJsonSample(scriptResponseText.value, 'Response') : null
    scriptDryRunResult.value = await scenarioApi.dryRunScript({ script, phase, context, response })
    if (scriptDryRunResult.value?.ok) ElMessage.success('脚本试运行通过')
    else ElMessage.warning('脚本试运行存在错误')
  } catch (e: any) {
    ElMessage.error(e.message || '脚本试运行失败')
  } finally {
    scriptDryRunLoading.value = false
  }
}

function buildStepPayload(): ScenarioStep | null {
  if (!validateStepConfig()) return null
  // 保存前同步 body 到 local
  syncBodyToLocal()
  if (bodyError.value) return null // JSON 错误时阻止确认
  syncParamEditorToLocal()
  let preSql: any[] = []
  let postSql: any[] = []
  try {
    const split = splitSqlQueries()
    preSql = split.preSql
    postSql = split.postSql
  } catch {
    ElMessage.error(t('step_editor.sql_json_error'))
    return null
  }
  let extractConfig: Record<string, any> = {}
  try {
    extractConfig = extractText.value.trim() ? JSON.parse(extractText.value) : {}
    if (!extractConfig || Array.isArray(extractConfig) || typeof extractConfig !== 'object') throw new Error('extract must be object')
  } catch {
    ElMessage.error(t('step_editor.extract_json_error'))
    return null
  }
  let assertions: Record<string, any>[] = []
  try {
    assertions = local.assertions.map(a => normalizeAssertion(a))
  } catch {
    ElMessage.error('断言期望值 JSON 格式错误')
    return null
  }

  // 构建干净的回传对象
  return {
    step_id: local.step_id,
    api_id: local.api_id,
    name: local.name,
    type: 'api',
    parent_id: (local as any).parent_id || '',
    depends_on: [...local.depends_on],
    extract: extractConfig,
    override_params: { ...local.override_params },
    override_headers: { ...local.override_headers },
    retry: local.retry,
    retry_delay_s: local.retry_delay_s,
    timeout_s: local.timeout_s,
    condition: null,
    loop: null,
    loop_var: null,
    loop_count: null,
    wait_ms: local.wait_ms,
    data_template_id: local.data_template_id,
    auth: { ...local.auth },
    pre_script: local.pre_script,
    post_script: local.post_script,
    pre_sql: preSql,
    post_sql: postSql,
    assertions,
  }
}

async function previewRequest() {
  if (!props.scenarioId) return
  const stepPayload = buildStepPayload()
  if (!stepPayload) return
  previewLoading.value = true
  try {
    requestPreview.value = await scenarioApi.previewStep(props.scenarioId, { step: stepPayload })
    if (requestPreview.value?.unresolved_refs?.length) {
      ElMessage.warning(`仍有未解析变量：${requestPreview.value.unresolved_refs.join(', ')}`)
    } else {
      ElMessage.success('请求预览已生成')
    }
  } catch (e: any) {
    ElMessage.error(e.message || '请求预览失败')
  } finally {
    previewLoading.value = false
  }
}

function confirm() {
  const result = buildStepPayload()
  if (!result) return
  if (requestPreview.value?.unresolved_refs?.length) {
    ElMessage.warning(`仍有未解析变量：${requestPreview.value.unresolved_refs.join(', ')}`)
  }
  emit('confirm', result)
  emit('update:visible', false)
}
</script>

<style scoped>
.se-basic {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 12px 16px;
  margin-bottom: 12px;
  background: var(--bg-3);
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.se-basic-row { display: flex; gap: 12px; }
.se-field { display: flex; flex-direction: column; gap: 4px; }
.se-tabs { margin-top: 4px; }
.se-tab-body { min-height: 200px; padding: 4px 0; }
.preview-footer { display: inline-flex; align-items: center; gap: 8px; margin-right: auto; float: left; }
.request-preview-panel { border-top: 1px solid var(--border); margin-top: 8px; padding-top: 10px; text-align: left; }
.request-preview-head { display: flex; align-items: center; gap: 10px; justify-content: space-between; font-size: 12px; font-weight: 600; color: var(--text); margin-bottom: 6px; }
.auth-preview-summary { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
.preview-alert { margin-bottom: 8px; }
.preview-tabs { margin-top: 4px; }
.preview-json { max-height: 220px; overflow: auto; font-size: 11px; }
.se-code :deep(textarea) {
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 12px;
}
.se-empty { padding: 24px 0; text-align: center; }
.assert-row { display: flex; gap: 6px; align-items: center; margin-bottom: 6px; }
.api-option { display: flex; align-items: center; gap: 8px; }
.api-method { min-width: 48px; font-size: 11px; font-weight: 600; color: var(--text); }
.api-path { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.var-toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; padding: 6px 8px; background: var(--bg-3); border: 1px solid var(--border); border-radius: 6px; }
.var-toolbar-label { font-size: 11px; color: var(--text-3); }
.param-layout { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.param-group { border: 1px solid var(--border); border-radius: 6px; padding: 10px; background: var(--bg-2); min-width: 0; }
.param-group-wide { grid-column: 1 / -1; }
.param-group-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; font-size: 12px; font-weight: 600; color: var(--text); margin-bottom: 8px; }
.param-warning { margin-top: 6px; font-size: 11px; color: var(--orange); line-height: 1.5; }
.body-label { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.body-actions { display: inline-flex; align-items: center; gap: 6px; }
.auth-template-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.auth-token-row { display: grid; grid-template-columns: 150px minmax(0, 1fr); gap: 8px; margin-bottom: 6px; }
.mock-case-toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
.ai-preview { margin-top: 10px; border-top: 1px solid var(--border); padding-top: 8px; display: flex; flex-direction: column; gap: 6px; }
.ai-preview-title { font-size: 12px; font-weight: 600; color: var(--text); }
.ai-preview-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 12px; padding: 4px 0; }
/* 变量交叉引用：Extract 标签页下方展示每个变量的引用关系 */
.extract-usage { margin-top: 12px; border-top: 1px solid var(--border); padding-top: 10px; }
.extract-usage-item { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; padding: 4px 0; font-size: 12px; }
.extract-usage-var { color: var(--text); font-weight: 600; font-size: 11px; background: var(--bg-3); padding: 1px 6px; border-radius: 3px; }
.extract-usage-label { color: var(--text-3); font-size: 11px; }
.sql-help { margin-bottom: 12px; }
.sql-toolbar { display: flex; gap: 8px; margin-bottom: 10px; }
.sql-query-card { border: 1px solid var(--border); border-radius: 6px; padding: 10px; margin-bottom: 10px; background: var(--bg-2); display: flex; flex-direction: column; gap: 8px; }
.sql-query-head { display: grid; grid-template-columns: 112px minmax(120px, 1fr) auto auto auto 30px; gap: 8px; align-items: center; }
.sql-query-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
.sql-result-panel { margin-top: 12px; border-top: 1px solid var(--border); padding-top: 10px; }
.sql-result-title { font-size: 12px; font-weight: 600; color: var(--text); margin-bottom: 6px; }
.sql-result-json { max-height: 220px; overflow: auto; margin-top: 8px; }
.script-toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.script-samples { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: 8px; }
.script-result-panel { margin-top: 10px; border-top: 1px solid var(--border); padding-top: 10px; }
.sql-assert-panel { flex-basis: 100%; margin-left: 96px; margin-top: 6px; display: grid; grid-template-columns: 120px minmax(160px, 1fr) minmax(160px, 1fr); gap: 6px; }
.sql-assert-text { grid-column: 1 / -1; }
.sql-extract-toolbar { display: grid; grid-template-columns: 120px minmax(160px, 1fr) minmax(180px, 1fr) auto; gap: 8px; margin-bottom: 8px; }
.advanced-extract { margin-top: 10px; }
.input-error :deep(.el-textarea__inner) { border-color: var(--red) !important; }
@media (max-width: 760px) {
  .param-layout, .script-samples, .sql-query-head, .sql-query-grid, .sql-assert-panel, .sql-extract-toolbar, .auth-token-row { grid-template-columns: 1fr; }
  .param-group-wide { grid-column: auto; }
  .sql-assert-panel { margin-left: 0; }
}
.field-error { color: var(--red); font-size: 11px; margin-top: 2px; }
.form-label { font-size: 11px; color: var(--text-3); margin-bottom: 2px; }
</style>
