<template>
  <div class="page">
    <div class="page-header">
      <div class="flex items-center gap-12">
        <el-button @click="$router.back()">← {{ $t('common.back') }}</el-button>
        <div>
          <div class="flex items-center gap-8">
            <span :class="methodClass(api.request?.method)">{{ api.request?.method }}</span>
            <span class="page-title mono" style="font-size:15px">{{ api.request?.path }}</span>
            <AnalysisStatusTag :status="api.analysis_status" :error="api.analysis_error" />
          </div>
          <div class="page-subtitle">{{ api.doc?.summary || api.name }}</div>
        </div>
      </div>
      <div class="flex items-center gap-8">
        <!-- 分析按钮：idle→三个按钮, failed→重试(force), done→重新分析(force), queued/running→spinner -->
        <template v-if="api.analysis_status === 'idle'">
          <el-button @click="analyzeDoc(false)" :disabled="analyzing">
            {{ $t('api_detail.analyze_doc_btn') }}
          </el-button>
          <el-button @click="analyzeAsserts(false)" :disabled="analyzing">
            {{ $t('api_detail.analyze_asserts_btn') }}
          </el-button>
          <el-button @click="analyzeApi(false)" :disabled="analyzing">
            {{ $t('api_detail.analyze_btn') }}
          </el-button>
        </template>
        <el-button v-else-if="api.analysis_status === 'failed' || analysisApplied || api.analysis_status === 'pending_review'" @click="analyzeApi(true)" :disabled="analyzing">
          {{ api.analysis_status === 'failed' ? $t('api_detail.retry_analyze') : $t('api_detail.reanalyze') }}
        </el-button>
        <el-button v-else-if="api.analysis_status === 'queued' || api.analysis_status === 'running'" disabled>
          <span class="spinner" style="width:12px;height:12px;border-width:2px;margin-right:4px;display:inline-block"></span>
          {{ api.analysis_status === 'running' ? $t('api_detail.analyzing') : $t('api_detail.queued') }}
        </el-button>
        <el-button v-if="analysisApplied" @click="genScenarioForApi" :disabled="genScenarioting">
          {{ genScenarioting ? $t('scenarios.generating') : $t('scenarios.ai_gen_btn') }}
        </el-button>
        <el-button type="primary" @click="openRunModal">{{ $t('api_detail.exec_btn') }}</el-button>
        <!-- P2: 次要操作（Mock/契约/工厂）收纳进"更多"下拉，减少头部按钮拥挤 -->
        <el-dropdown trigger="click" @command="handleMoreAction">
          <el-button size="small">
            {{ $t('common.more', '更多') }} ▾
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="mock" :disabled="mocking || !analysisApplied">
                🧪 {{ $t('api_detail.mock_btn') }}
              </el-dropdown-item>
              <el-dropdown-item command="contract" :disabled="contractChecking || !runResult || !analysisApplied">
                📋 {{ $t('api_detail.contract_check_btn') }}
              </el-dropdown-item>
              <el-dropdown-item command="factory" divided>
                🏭 {{ $t('api_detail.factory_btn') }}
              </el-dropdown-item>
              <el-dropdown-item command="mockService">
                🧩 {{ $t('api_detail.add_to_mock_service') }}
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </div>

    <!-- P0-4: AI 流式输出打字机区域——分析进行中时实时展示生成内容 -->
    <div v-if="streamContent" class="ai-stream-panel">
      <div class="ai-stream-header">
        <span class="spinner" style="width:10px;height:10px;border-width:2px;margin-right:6px;display:inline-block"></span>
        <span class="text-2" style="font-size:12px">
          {{ streamTaskType === 'doc' ? $t('api_detail.streaming_doc') :
             streamTaskType === 'asserts' ? $t('api_detail.streaming_asserts') :
             $t('api_detail.streaming_generating') }}
        </span>
      </div>
      <pre class="ai-stream-content">{{ streamContent }}<span class="cursor">▋</span></pre>
    </div>

    <div class="page-body" v-loading="loading">
      <!-- 最近执行结果（modal 关闭后仍可见） -->
      <div v-if="runResult" class="run-result-persist" :class="runResult.passed ? 'run-ok' : 'run-fail'" style="margin-bottom:12px;border-radius:var(--radius);border:1px solid;overflow:hidden">
        <div class="run-result-header">
          <ResultTag :passed="runResult.passed" />
          <span class="mono text-2" style="font-size:12px">{{ runResult.steps?.[0]?.response_received?.status_code }} · {{ fmt.duration(runResult.duration_ms) }}</span>
          <div class="flex items-center gap-8" style="margin-left:auto">
            <el-button size="small" @click="openRunModal">{{ $t('api_detail.view_details') }}</el-button>
            <el-button size="small" @click="runResult = null">✕</el-button>
          </div>
        </div>
        <div v-if="runResult.steps?.[0]?.assert_results?.length" style="padding:8px 16px">
          <div v-for="ar in runResult.steps[0].assert_results" :key="ar.field" class="assert-row">
            <span :class="ar.passed ? 'dot dot-green' : 'dot dot-red'"></span>
            <span class="mono text-2" style="font-size:11px">{{ ar.field }}</span>
            <span class="text-3" style="font-size:11px">{{ ar.operator }} {{ ar.expected }}</span>
            <span class="mono" style="font-size:11px" :class="ar.passed ? 'green' : 'red'">→ {{ ar.actual }}</span>
          </div>
        </div>
      </div>

      <el-card v-if="mockResult || mockCases.length" class="mock-case-card" v-loading="mockCaseLoading">
        <template #header>
          <div class="flex items-center justify-between">
            <span class="card-title">Mock Cases</span>
            <div class="flex items-center gap-8">
              <el-button size="small" @click="loadMockCases">刷新</el-button>
              <el-button size="small" type="primary" plain :loading="mocking" :disabled="!analysisApplied" @click="createMockCaseFromDoc">从文档生成 Case</el-button>
              <el-button size="small" :disabled="!mockResult" @click="saveMockPreviewAsCase">保存当前预览</el-button>
            </div>
          </div>
        </template>
        <div class="mock-case-layout">
          <div>
            <div class="section-title">Case 列表</div>
            <el-table :data="mockCases" size="small" max-height="260" :empty-text="$t('common.no_data')">
              <el-table-column label="名称" min-width="150">
                <template #default="{ row }">
                  <div style="font-weight:600">{{ row.name }}</div>
                  <div class="mono text-3" style="font-size:10px">{{ row.id }}</div>
                </template>
              </el-table-column>
              <el-table-column label="状态" width="78">
                <template #default="{ row }">{{ row.response?.status_code || 200 }}</template>
              </el-table-column>
              <el-table-column label="操作" width="150">
                <template #default="{ row }">
                  <el-button size="small" link type="primary" @click="previewMockCase(row)">预览</el-button>
                  <el-button size="small" link type="danger" @click="deleteMockCase(row)">删除</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
          <div>
            <div class="section-title">预览</div>
            <pre class="code-block mock-preview-json">{{ jsonPretty(mockResult || {}) }}</pre>
          </div>
        </div>
      </el-card>

      <el-tabs v-model="activeTab">
        <el-tab-pane :label="$t('api_detail.overview')" name="overview" />
        <el-tab-pane :label="$t('api_detail.ai_doc')" name="aidoc" />
        <el-tab-pane :label="$t('api_detail.asserts')" name="asserts" />
        <el-tab-pane :label="$t('api_detail.stats_tab')" name="stats" />
        <el-tab-pane :label="$t('api_detail.knowledge_tab')" name="knowledge" />
        <el-tab-pane :label="$t('api_detail.ai_ops_tab')" name="aiops" />
        <!-- 审核 tab：当有 pending 审核版本时显示徽章数量 -->
        <el-tab-pane name="review">
          <template #label>
            <span>{{ $t('api_detail.review_tab') }}</span>
            <el-badge v-if="pendingReviewCount > 0" :value="pendingReviewCount" class="review-badge" />
          </template>
        </el-tab-pane>
      </el-tabs>

      <!-- Overview tab -->
      <div v-if="activeTab === 'overview'">
        <el-card v-if="api.quality" class="quality-panel">
          <template #header>
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-8">
                <span class="card-title">{{ $t('quality.api_title') }}</span>
                <el-tag :type="qualityTagType(api.quality.risk_level)" size="small">
                  {{ $t('quality.risk_' + api.quality.risk_level) }}
                </el-tag>
              </div>
              <span class="quality-total mono">{{ api.quality.score }}</span>
            </div>
          </template>
          <div class="quality-content">
            <div class="quality-meter">
              <el-progress
                type="dashboard"
                :percentage="api.quality.score"
                :width="112"
                :stroke-width="8"
                :color="qualityColor(api.quality.risk_level)"
              />
              <div class="text-2" style="font-size:12px">{{ $t('quality.scenario_count', { count: api.quality.scenario_count || 0 }) }}</div>
            </div>
            <div class="quality-breakdown">
              <div v-for="item in qualityBreakdown" :key="item.key" class="quality-breakdown-row">
                <span class="text-2">{{ item.label }}</span>
                <el-progress
                  :percentage="item.percent"
                  :stroke-width="8"
                  :show-text="false"
                  :color="qualityColor(api.quality.risk_level)"
                />
                <span class="mono text-2" style="font-size:12px">{{ item.score }}/{{ item.max }}</span>
              </div>
            </div>
            <div class="quality-suggestions">
              <div class="form-label">{{ $t('quality.suggestions') }}</div>
              <!-- P1-4: 质量建议可点击 —— 每条建议对应一个操作（跳转 Tab/触发分析/生成场景） -->
              <el-tag
                v-for="s in api.quality.suggestions || []"
                :key="s"
                size="small"
                type="info"
                effect="plain"
                style="cursor:pointer"
                @click="suggestionAction(s)"
              >
                {{ $t('quality.suggestion_' + s) }} →
              </el-tag>
              <span v-if="!(api.quality.suggestions || []).length" class="text-2" style="font-size:12px">
                {{ $t('quality.no_suggestions') }}
              </span>
            </div>
          </div>
        </el-card>

        <div class="detail-grid">
          <!-- Request -->
          <el-card>
            <template #header><span class="card-title">{{ $t('api_detail.request_card') }}</span></template>
            <div class="detail-field"><span class="field-key">URL</span><span class="field-val mono">{{ api.request?.url }}</span></div>
            <div class="detail-field"><span class="field-key">Method</span><span :class="methodClass(api.request?.method)">{{ api.request?.method }}</span></div>
            <div v-if="hasQuery" class="detail-field">
              <span class="field-key">Query</span>
              <pre class="code-block flex-1">{{ jsonPretty(api.request?.query_params) }}</pre>
            </div>
            <div v-if="api.request?.body" class="detail-field">
              <span class="field-key">Body ({{ api.request?.body_type }})</span>
              <pre class="code-block flex-1">{{ jsonPretty(api.request?.body) }}</pre>
            </div>
            <div class="detail-field">
              <span class="field-key">Headers</span>
              <div class="headers-grid">
                <template v-for="(v, k) in api.request?.headers" :key="k">
                  <span class="mono text-3" style="font-size:11px">{{ k }}</span>
                  <span class="mono text-2" style="font-size:11px">{{ v }}</span>
                </template>
              </div>
            </div>
          </el-card>

          <!-- Response -->
          <el-card>
            <template #header><span class="card-title">{{ $t('api_detail.response_card') }}</span></template>
            <div class="detail-field">
              <span class="field-key">Status</span>
              <el-tag v-if="api.response?.status_code" :type="api.response.status_code < 300 ? 'success' : 'danger'">
                {{ api.response.status_code }}
              </el-tag>
              <el-tag v-else type="info">N/A</el-tag>
            </div>
            <div class="detail-field">
              <span class="field-key">Latency</span>
              <span class="mono text-2">{{ api.response?.latency_ms != null ? api.response.latency_ms + 'ms' : 'N/A' }}</span>
            </div>
            <div v-if="api.response?.body" class="detail-field">
              <span class="field-key">Body</span>
              <pre class="code-block flex-1" style="max-height:300px;overflow:auto">{{ jsonPretty(api.response?.body) }}</pre>
            </div>
          </el-card>
        </div>

        <!-- AI 文档状态指示：done 时提示查看 AI 文档 tab -->
        <el-card style="margin-top:12px" v-if="analysisApplied && api.doc?.summary">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-8">
              <span class="card-title">{{ $t('api_detail.ai_doc') }}</span>
              <el-tag type="success" size="small">{{ $t('api_detail.ai_doc_generated') }}</el-tag>
            </div>
            <el-button size="small" @click="activeTab = 'aidoc'">{{ $t('api_detail.view_edit_doc') }}</el-button>
          </div>
          <p style="color:var(--text-2);font-size:13px;margin-top:8px">{{ api.doc?.summary }}</p>
        </el-card>

       <!-- 场景生成失败警告 -->
        <el-alert
          v-if="api.scenario_error"
          type="error"
          :title="$t('api_detail.scenario_error_title')"
          :description="api.scenario_error"
          show-icon
          :closable="false"
          style="margin-top:12px"
        />

        <!-- 关联场景：展示包含本 API 的测试场景 -->
        <el-card style="margin-top:12px">
          <template #header>
            <div class="flex items-center justify-between">
              <span class="card-title">{{ $t('api_detail.related_scenarios', { count: relatedScenarios.length }) }}</span>
              <el-button size="small" @click="$router.push('/scenarios')">{{ $t('api_detail.view_all_scenarios') }}</el-button>
            </div>
          </template>
          <el-empty v-if="!relatedScenarios.length" :description="$t('api_detail.no_related_scenarios')" :image-size="36" style="padding:20px" />
          <el-table v-else :data="relatedScenarios" size="small" @row-click="(row) => $router.push(`/scenarios/${row.id}`)" style="cursor:pointer">
            <el-table-column :label="$t('common.name')" min-width="140">
              <template #default="{ row: s }">
                <span style="font-weight:500">{{ s.name }}</span>
                <el-tag v-if="s.ai_generated" type="info" size="small" style="margin-left:6px">{{ $t('api_detail.ai_generated') }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="$t('scenarios.col_steps')" width="70">
              <template #default="{ row: s }">{{ s.steps?.length || 0 }}</template>
            </el-table-column>
            <el-table-column :label="$t('common.status')" width="80">
              <template #default="{ row: s }">
                <el-tag :type="s.status === 'done' ? 'success' : s.status === 'running' ? 'warning' : s.status === 'failed' ? 'danger' : 'info'" size="small">{{ s.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="$t('common.time')" width="120">
              <template #default="{ row: s }">
                <span class="text-2" style="font-size:11px">{{ fmt.fromNow(s.updated_at) }}</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <!-- 关联知识库 & 记忆：始终显示，无数据时提供提取入口 -->
        <el-card style="margin-top:12px">
          <template #header>
            <div class="flex items-center justify-between">
              <span class="card-title">{{ $t('api_detail.related_memories', { n: relatedKnowledge.length }) }}</span>
              <div class="flex items-center gap-8">
                <el-button v-if="relatedKnowledge.length" size="small" @click="activeTab = 'knowledge'">{{ $t('api_detail.view_all') }}</el-button>
                <el-button v-if="analysisApplied" size="small"
                  @click="extractMemories" :disabled="extractingMem" :loading="extractingMem">
                  {{ extractingMem ? '…' : $t('api_detail.extract_memories') }}
                </el-button>
              </div>
            </div>
          </template>
          <!-- 无关联知识库条目时显示空状态 + 提取入口 -->
          <el-empty v-if="!relatedKnowledge.length" :description="$t('api_detail.no_related_memories')" :image-size="36" />
          <div v-else class="memory-cards">
            <div v-for="m in relatedKnowledge" :key="m.id" class="memory-card-item">
              <div class="flex items-center gap-8">
                <el-tag :color="typeColor(m.type)" size="small" style="color:#fff;border:none">{{ $t('knowledge_type_' + m.type) }}</el-tag>
                <span class="memory-card-title">{{ m.title }}</span>
                <span class="memory-confidence text-3" style="font-size:10px">{{ (m.confidence*100).toFixed(0) }}%</span>
              </div>
              <div class="text-2" style="font-size:12px;margin-top:4px">{{ truncate(m.content, 120) }}</div>
              <!-- 标签：与知识库 Tab 保持一致 -->
              <div v-if="m.tags?.length" class="flex items-center gap-4" style="margin-top:6px">
                <el-tag v-for="t in m.tags" :key="t" size="small" type="info" style="font-size:10px">{{ tagLabel(t) }}</el-tag>
              </div>
            </div>
          </div>
        </el-card>
      </div>
      <!-- 可编辑的 AI 文档 -->
      <div v-if="activeTab === 'aidoc'">
        <el-empty v-if="!analysisApplied || !api.doc?.summary" :description="$t('api_detail.no_ai_doc_hint')" :image-size="48" style="padding:60px" />
        <div v-else>
          <el-card style="margin-bottom:12px">
            <template #header>
              <div class="flex items-center justify-between">
                <span class="card-title">{{ $t('api_detail.api_doc_title') }}</span>
                <el-button type="primary" size="small" @click="saveDoc" :disabled="savingDoc">
                  {{ savingDoc ? $t('api_detail.saving') : $t('api_detail.save_doc') }}
                </el-button>
              </div>
            </template>
            <div class="form-group">
              <label class="form-label">{{ $t('api_detail.summary') }}</label>
              <el-input v-model="docForm.summary" :placeholder="$t('api_detail.summary')" />
            </div>
            <div class="form-group">
              <label class="form-label">{{ $t('common.description') }}</label>
              <el-input v-model="docForm.description" type="textarea" :rows="3" :placeholder="$t('common.description')" />
            </div>
            <div class="form-group">
              <label class="form-label">{{ $t('api_detail.tags') }}</label>
              <el-input :model-value="(docForm.tags||[]).join(',')" @update:model-value="docForm.tags = $event.split(',').map(s=>s.trim()).filter(Boolean)" :placeholder="$t('api_detail.tags')" />
            </div>
          </el-card>

          <!-- 请求参数 -->
          <el-card style="margin-bottom:12px">
            <template #header>
              <div class="flex items-center justify-between">
                <span class="card-title">{{ $t('api_detail.request_params', { count: docForm.params?.length || 0 }) }}</span>
                <el-button size="small" @click="addDocParam">{{ $t('api_detail.add') }}</el-button>
              </div>
            </template>
            <el-table :data="docForm.params || []" size="small" :empty-text="$t('api_detail.no_params')">
              <el-table-column :label="$t('api_detail.col_field_name')">
                <template #default="{ row: p }">
                  <el-input v-model="p.name" size="small" :placeholder="$t('api_detail.col_field_name')" />
                </template>
              </el-table-column>
              <el-table-column :label="$t('api_detail.col_location')" width="100">
                <template #default="{ row: p }">
                  <el-select v-model="p.location" size="small">
                    <el-option v-for="loc in ['query','path','header','body','form']" :key="loc" :label="loc" :value="loc" />
                  </el-select>
                </template>
              </el-table-column>
              <el-table-column :label="$t('api_detail.col_type')" width="100">
                <template #default="{ row: p }">
                  <el-select v-model="p.type" size="small">
                    <el-option v-for="t in ['string','integer','number','boolean','array','object']" :key="t" :label="t" :value="t" />
                  </el-select>
                </template>
              </el-table-column>
              <el-table-column :label="$t('api_detail.col_required')" width="60">
                <template #default="{ row: p }">
                  <el-switch v-model="p.required" size="small" />
                </template>
              </el-table-column>
              <el-table-column :label="$t('api_detail.col_description')">
                <template #default="{ row: p }">
                  <el-input v-model="p.description" size="small" :placeholder="$t('api_detail.col_description')" />
                </template>
              </el-table-column>
              <el-table-column :label="$t('api_detail.col_example')">
                <template #default="{ row: p }">
                  <el-input v-model="p.example" size="small" :placeholder="$t('api_detail.col_example')" />
                </template>
              </el-table-column>
              <el-table-column label="" width="50">
                <template #default="{ $index: i }">
                  <el-button type="danger" size="small" :icon="Close" @click="docForm.params.splice(i,1)" />
                </template>
              </el-table-column>
            </el-table>
          </el-card>

          <!-- 响应字段 -->
          <el-card>
            <template #header>
              <div class="flex items-center justify-between">
                <span class="card-title">{{ $t('api_detail.response_fields', { count: docForm.response_fields?.length || 0 }) }}</span>
                <el-button size="small" @click="addDocRespField">{{ $t('api_detail.add') }}</el-button>
              </div>
            </template>
            <el-table :data="docForm.response_fields || []" size="small" :empty-text="$t('api_detail.no_resp_fields')">
              <el-table-column :label="$t('api_detail.col_field_name')">
                <template #default="{ row: p }">
                  <el-input v-model="p.name" size="small" :placeholder="$t('api_detail.col_field_name')" />
                </template>
              </el-table-column>
              <el-table-column :label="$t('api_detail.col_type')" width="100">
                <template #default="{ row: p }">
                  <el-select v-model="p.type" size="small">
                    <el-option v-for="t in ['string','integer','number','boolean','array','object']" :key="t" :label="t" :value="t" />
                  </el-select>
                </template>
              </el-table-column>
              <el-table-column :label="$t('api_detail.col_description')">
                <template #default="{ row: p }">
                  <el-input v-model="p.description" size="small" :placeholder="$t('api_detail.col_description')" />
                </template>
              </el-table-column>
              <el-table-column :label="$t('api_detail.col_example')">
                <template #default="{ row: p }">
                  <el-input v-model="p.example" size="small" :placeholder="$t('api_detail.col_example')" />
                </template>
              </el-table-column>
              <el-table-column label="" width="50">
                <template #default="{ $index: i }">
                  <el-button type="danger" size="small" :icon="Close" @click="docForm.response_fields.splice(i,1)" />
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </div>
      </div>

      <!-- Asserts tab -->
      <div v-if="activeTab === 'asserts'">
        <el-card>
          <template #header>
            <div class="flex items-center justify-between">
              <span class="card-title">{{ $t('api_detail.asserts_title', { count: asserts.length }) }}</span>
              <el-button type="primary" size="small" @click="addAssertRow">{{ $t('api_detail.add') }}</el-button>
            </div>
          </template>
          <el-table :data="asserts" :empty-text="$t('api_detail.no_asserts')" size="small">
            <el-table-column :label="$t('api_detail.col_field_jsonpath')">
              <template #default="{ row: rule }">
                <el-input v-model="rule.field" :placeholder="$t('api_detail.field_placeholder')" size="small" />
              </template>
            </el-table-column>
            <el-table-column :label="$t('api_detail.col_operator')">
              <template #default="{ row: rule }">
                <!-- P0-2：operator 选项从后端单一来源拉取，支持分组 + help 提示 -->
                <el-select v-model="rule.operator" size="small" :title="opHelp(rule.operator)">
                  <el-option-group v-for="grp in operatorGroups" :key="grp.group" :label="$t('assert.group_' + grp.group)">
                    <el-option
                      v-for="item in grp.items"
                      :key="item.op"
                      :label="$t(item.label_key)"
                      :value="item.op"
                      :title="item.help_zh"
                    />
                  </el-option-group>
                </el-select>
              </template>
            </el-table-column>
            <el-table-column :label="$t('api_detail.col_expected')">
              <template #default="{ row: rule }">
                <!-- P0-2：按 expected_type 动态渲染 expected 控件 -->
                <!-- none：exists/empty 类无需期望值，禁用输入 -->
                <el-input v-if="expectedType(rule.operator) === 'none'" model-value="—" disabled size="small" />
                <!-- number：gt/lt/response_time_lt 等数值比较 -->
                <el-input-number v-else-if="expectedType(rule.operator) === 'number'" v-model="rule.expected" :controls="false" size="small" style="width:100%" />
                <!-- select_type：type_match 类型下拉 -->
                <el-select v-else-if="expectedType(rule.operator) === 'select_type'" v-model="rule.expected" size="small">
                  <el-option v-for="tp in typeCandidates" :key="tp" :label="tp" :value="tp" />
                </el-select>
                <!-- header_name：响应头名输入，field 列填 header 名，expected 填期望值 -->
                <el-input v-else-if="expectedType(rule.operator) === 'header_name'" v-model="rule.expected" :placeholder="$t('api_detail.expected_placeholder')" size="small" />
                <!-- multi：in/not_in 多值（逗号分隔），保存时由后端解析 -->
                <el-input v-else-if="expectedType(rule.operator) === 'multi'" :model-value="formatMulti(rule.expected)" @update:model-value="rule.expected = parseMulti($event)" :placeholder="$t('api_detail.expected_multi_placeholder', '值1,值2')" size="small" />
                <!-- json：json_schema 用 textarea -->
                <el-input v-else-if="expectedType(rule.operator) === 'json'" v-model="rule.expected" type="textarea" :rows="2" :placeholder="expectedJsonPlaceholder" size="small" />
                <!-- text：默认文本输入 -->
                <el-input v-else v-model="rule.expected" :placeholder="$t('api_detail.expected_placeholder')" size="small" />
              </template>
            </el-table-column>
            <el-table-column :label="$t('api_detail.col_risk_level')">
              <template #default="{ row: rule }">
                <el-select v-model="rule.risk_level" size="small">
                  <el-option v-for="r in ['low','medium','high','critical']" :key="r" :label="$t('assert.risk_' + r)" :value="r" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column :label="$t('api_detail.col_description')">
              <template #default="{ row: rule }">
                <el-input v-model="rule.description" :placeholder="$t('api_detail.description_placeholder')" size="small" />
              </template>
            </el-table-column>
            <el-table-column label="" width="50">
              <template #default="{ $index: i }">
                <el-button type="danger" size="small" :icon="Close" @click="asserts.splice(i,1)" />
              </template>
            </el-table-column>
          </el-table>
          <div style="padding:16px 0 0; display:flex; gap:8px">
            <el-button type="primary" @click="saveAsserts" :disabled="savingAsserts">
              {{ savingAsserts ? $t('api_detail.saving') : $t('api_detail.save_asserts') }}
            </el-button>
            <el-button @click="loadAsserts">{{ $t('api_detail.reset') }}</el-button>
          </div>
        </el-card>
      </div>

      <!-- Stats tab -->
      <div v-if="activeTab === 'stats'">
        <el-card>
          <template #header>
            <div class="flex items-center justify-between">
              <span class="card-title">{{ $t('api_detail.stats_title') }}</span>
              <div class="flex items-center gap-12">
                <el-tag type="success">{{ $t('api_detail.stats_passed', { passed: statsData.passed, total: statsData.total }) }}</el-tag>
                <el-tag type="info">{{ $t('api_detail.stats_avg_latency', { ms: statsData.avg_latency_ms }) }}</el-tag>
              </div>
            </div>
          </template>
          <VChart :option="statsChartOpt" autoresize style="height:240px" />
        </el-card>
      </div>

      <!-- 知识库 & 记忆 Tab：子Tab整合 Knowledge + Memory(L1/L2/L3) -->
      <div v-if="activeTab === 'knowledge'">
        <div class="flex items-center justify-between" style="margin-bottom:8px">
          <span class="text-2">{{ $t('api_detail.related_memories', { n: knowledgeEntries.length + l1MemTotal + l2MemItems.length + l3MemItems.length }) }}</span>
          <div class="flex items-center gap-8">
            <el-button v-if="analysisApplied" size="small"
              @click="extractMemories" :disabled="extractingMem" :loading="extractingMem">
              {{ extractingMem ? '…' : $t('api_detail.extract_memories') }}
            </el-button>
            <el-button size="small" @click="reloadActiveSubTab">{{ $t('common.refresh') }}</el-button>
          </div>
        </div>

        <!-- 子 Tab：知识库 / L1长期记忆 / L2项目记忆 / L3会话记忆 -->
        <el-tabs v-model="knowledgeSubTab" @tab-change="onKnowledgeSubTabChange">
          <!-- 知识库子 Tab -->
          <el-tab-pane label="知识库" name="knowledge">
            <!-- 知识库筛选栏 -->
            <div class="filter-bar" style="padding:8px 0">
              <el-input v-model="knowledgeSearch" :placeholder="$t('knowledge_search_placeholder')" size="small" style="width:200px" clearable @change="loadKnowledgeEntries" @clear="loadKnowledgeEntries" />
              <el-select v-model="knowledgeFilterType" :placeholder="$t('knowledge_col_type')" size="small" style="width:140px" @change="loadKnowledgeEntries">
                <el-option :label="$t('common.all')" value="" />
                <el-option :label="$t('knowledge_type_field_pattern')" value="field_pattern" />
                <el-option :label="$t('knowledge_type_assertion_pattern')" value="assertion_pattern" />
                <el-option :label="$t('knowledge_type_doc_pattern')" value="doc_pattern" />
                <el-option :label="$t('knowledge_type_scenario_pattern')" value="scenario_pattern" />
              </el-select>
            </div>
            <el-empty v-if="!knowledgeEntries.length" :description="$t('api_detail.no_related_memories')" :image-size="36" style="padding:40px" />
            <div v-else class="knowledge-list">
              <el-card v-for="m in knowledgeEntries" :key="m.id" style="margin-bottom:10px">
                <div class="knowledge-item-header">
                  <div class="flex items-center gap-8">
                    <el-tag :color="typeColor(m.type)" size="small" style="color:#fff;border:none">{{ $t('knowledge_type_' + m.type) }}</el-tag>
                    <span class="knowledge-item-title">{{ m.title }}</span>
                    <span class="text-3" style="font-size:10px">{{ (m.confidence*100).toFixed(0) }}%</span>
                  </div>
                  <div class="flex items-center gap-8">
                    <span class="text-3" style="font-size:10px">{{ $t('knowledge_col_usage') }}: {{ m.usage_count || 0 }}</span>
                    <el-button size="small" @click="editKnowledgeEntry(m)">{{ $t('common.edit') }}</el-button>
                  </div>
                </div>
                <div class="text-2" style="font-size:13px;line-height:1.7;white-space:pre-wrap;margin-top:8px">{{ m.content }}</div>
                <div v-if="m.tags?.length" class="flex items-center gap-4" style="margin-top:8px">
                  <el-tag v-for="t in m.tags" :key="t" size="small" type="info" style="font-size:10px">{{ tagLabel(t) }}</el-tag>
                </div>
                <div v-if="m.sources?.length" class="text-3" style="font-size:10px;margin-top:6px">
                  {{ $t('knowledge_col_sources') }}: {{ m.sources.join(', ') }}
                </div>
              </el-card>
              <!-- 知识库分页 -->
              <div class="pagination-wrapper" v-if="knowledgeTotal > knowledgePageSize">
                <el-pagination
                  v-model:current-page="knowledgePage"
                  :page-size="knowledgePageSize"
                  :total="knowledgeTotal"
                  layout="prev, pager, next"
                  small
                  @current-change="loadKnowledgeEntries"
                />
              </div>
            </div>
          </el-tab-pane>

          <!-- L1 长期记忆子 Tab -->
          <el-tab-pane :label="$t('memory.tabL1')" name="l1">
            <el-empty v-if="!l1MemItems.length && !memLoading.l1" :description="$t('memory.emptyL1')" :image-size="36" style="padding:40px" />
            <el-table v-else :data="l1MemItems" row-key="key" max-height="calc(100vh - 420px)" :empty-text="$t('memory.emptyL1')" v-loading="memLoading.l1">
              <el-table-column :label="$t('memory.colKey')" width="200">
                <template #default="{ row }">
                  <span class="mono" style="font-size:12px;font-weight:600">{{ row.key }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('memory.colContent')" min-width="280">
                <template #default="{ row }">
                  <span class="text-2" style="font-size:12px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden">{{ (row.content || '').slice(0, 300) }}{{ (row.content || '').length > 300 ? '…' : '' }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('memory.colSource')" width="100">
                <template #default="{ row }">{{ $t('memory.source_' + row.source) || row.source || '—' }}</template>
              </el-table-column>
              <el-table-column :label="$t('memory.colUpdatedAt')" width="160">
                <template #default="{ row }">{{ formatTime(row.updated_at || row.created_at) }}</template>
              </el-table-column>
            </el-table>
            <div class="flex-between" style="padding:8px 0" v-if="l1MemTotal > 20">
              <span class="text-3">{{ $t('memory.totalItems', { n: l1MemTotal }) }}</span>
              <el-pagination v-model:current-page="l1MemPage" :page-size="20" :total="l1MemTotal" layout="prev,next" small @current-change="loadL1Memories" />
            </div>
          </el-tab-pane>

          <!-- L2 项目记忆子 Tab -->
          <el-tab-pane :label="$t('memory.tabL2')" name="l2">
            <el-empty v-if="!l2MemItems.length && !memLoading.l2" :description="$t('memory.emptyL2')" :image-size="36" style="padding:40px" />
            <el-table v-else :data="l2MemItems" row-key="id" max-height="calc(100vh - 420px)" :empty-text="$t('memory.emptyL2')" v-loading="memLoading.l2">
              <el-table-column :label="$t('memory.colTitle')" min-width="180">
                <template #default="{ row }">
                  <span class="mono" style="font-size:13px;font-weight:600">{{ row.title }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('memory.colType')" width="110">
                <template #default="{ row }">
                  <el-tag size="small" type="info">{{ typeLabel(row.type) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column :label="$t('memory.colContent')" min-width="260">
                <template #default="{ row }">
                  <span class="text-2" style="font-size:12px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden">{{ (row.content || '').slice(0, 300) }}{{ (row.content || '').length > 300 ? '…' : '' }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('memory.colCreatedAt')" width="160">
                <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
              </el-table-column>
            </el-table>
          </el-tab-pane>

          <!-- L3 会话记忆子 Tab -->
          <el-tab-pane :label="$t('memory.tabL3')" name="l3">
            <el-empty v-if="!l3MemItems.length && !memLoading.l3" :description="$t('memory.emptyL3')" :image-size="36" style="padding:40px" />
            <el-table v-else :data="l3MemItems" row-key="id" max-height="calc(100vh - 420px)" :empty-text="$t('memory.emptyL3')" v-loading="memLoading.l3">
              <el-table-column :label="$t('memory.colSessionId')" width="180">
                <template #default="{ row }">
                  <span class="mono" style="font-size:11px">{{ row.session_id?.slice(0, 12) }}…</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('memory.colSummary')" min-width="280">
                <template #default="{ row }">
                  <span class="text-2" style="font-size:12px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden">{{ (row.summary || '').slice(0, 300) }}{{ (row.summary || '').length > 300 ? '…' : '' }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('memory.colUser')" width="80">
                <template #default="{ row }">{{ row.user_id || '—' }}</template>
              </el-table-column>
              <el-table-column :label="$t('memory.colCreatedAt')" width="160">
                <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
              </el-table-column>
              <el-table-column :label="$t('memory.colExpiresAt')" width="160">
                <template #default="{ row }">{{ formatTime(row.expires_at) }}</template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
        </el-tabs>

        <!-- 编辑知识库条目弹窗 -->
        <el-dialog v-model="showKnowledgeEdit" :title="$t('knowledge_edit_title')" width="520px" :close-on-click-modal="false">
          <el-form label-position="top">
            <el-form-item :label="$t('knowledge_form_title')">
              <el-input v-model="editingEntry.title" />
            </el-form-item>
            <el-form-item :label="$t('knowledge_form_content')">
              <el-input v-model="editingEntry.content" type="textarea" :rows="8" />
            </el-form-item>
            <el-form-item :label="$t('knowledge_form_tags')">
              <el-input :model-value="(editingEntry.tags||[]).join(',')" @update:model-value="editingEntry.tags = $event.split(',').map(s=>s.trim()).filter(Boolean)" />
            </el-form-item>
          </el-form>
          <template #footer>
            <el-button @click="showKnowledgeEdit = false">{{ $t('common.cancel') }}</el-button>
            <el-button type="primary" @click="saveKnowledgeEntry" :disabled="savingKnowledge">
              {{ savingKnowledge ? $t('api_detail.saving') : $t('common.save') }}
            </el-button>
          </template>
        </el-dialog>
      </div>

      <!-- 审核 tab：展示当前 API 的全部生成版本，支持按状态筛选 & 审核 -->
      <div v-if="activeTab === 'review'">
        <!-- 工具栏：状态筛选 + 触发AI分析 -->
        <div class="flex items-center justify-between mb-12" style="margin-bottom:10px">
          <div class="flex items-center gap-8">
            <span class="text-2">{{ $t('common.status') }}:</span>
            <el-select v-model="reviewFilterStatus" @change="onReviewFilterChange" :placeholder="$t('common.all')" size="small" style="width:140px" clearable>
              <el-option :label="$t('generations.status_pending_review')" value="pending_review" />
              <el-option :label="$t('generations.status_accepted')" value="accepted" />
              <el-option :label="$t('generations.status_rejected')" value="rejected" />
            </el-select>
          </div>
          <el-button type="primary" size="small" @click="analyzeApi(true)" :disabled="analyzing">
            {{ analyzing ? $t('api_detail.ai_running') : $t('api_detail.trigger_ai') }}
          </el-button>
        </div>

        <el-card v-if="apiGenerations.length === 0 && !reviewLoading">
          <el-empty :description="$t('api_detail.no_generations')" :image-size="36" style="padding:20px">
            <template #default>
              <el-button type="primary" size="small" @click="analyzeApi(true)" :disabled="analyzing">
                {{ $t('api_detail.trigger_ai') }}
              </el-button>
            </template>
          </el-empty>
        </el-card>
        <template v-else>
          <div v-loading="reviewLoading" class="review-list">
            <el-card v-for="gen in apiGenerations" :key="gen.id" class="review-item-card">
              <template #header>
                <div class="flex items-center justify-between">
                  <div class="flex items-center gap-8">
                    <el-tag :type="gen.type === 'doc' ? 'success' : gen.type === 'asserts' ? 'warning' : 'primary'" size="small">
                      {{ $t('generations.type_' + gen.type) }}
                    </el-tag>
                    <!-- 状态标签：pending_review/accepted/rejected -->
                    <el-tag v-if="gen.status === 'pending_review'" type="warning" size="small">
                      {{ $t('generations.status_pending_review') }}
                    </el-tag>
                    <el-tag v-else-if="gen.status === 'accepted'" type="success" size="small">
                      {{ $t('generations.status_accepted') }}
                    </el-tag>
                    <el-tag v-else-if="gen.status === 'rejected'" type="danger" size="small">
                      {{ $t('generations.status_rejected') }}
                    </el-tag>
                    <el-tag v-else size="small">{{ gen.status }}</el-tag>
                    <span class="text-2" style="font-size:13px">{{ gen.summary || '—' }}</span>
                  </div>
                  <span class="text-3" style="font-size:11px">{{ fmt.time(gen.created_at) }}</span>
                </div>
              </template>
              <!-- 审核面板：ReviewPanel 内部 isPending 自动禁用按钮，非 pending 状态可预览内容 -->
              <ReviewPanel
                :generation-id="gen.id"
                @accepted="onReviewAccepted(gen.id)"
                @rejected="onReviewRejected(gen.id)"
              />
            </el-card>
          </div>
        </template>
      </div>

      <!-- AI 操作日志 tab -->
      <div v-if="activeTab === 'aiops'">
        <el-card>
          <template #header>
            <span class="card-title">{{ $t('api_detail.ai_ops_title') }}</span>
          </template>
          <div v-loading="aiOpsLoading">
            <el-empty v-if="!aiOpsLoading && !aiOpsLogs.length" :description="$t('api_detail.no_ai_ops')" :image-size="36" style="padding:20px" />
            <el-table v-else :data="aiOpsLogs" size="small">
              <el-table-column :label="$t('api_detail.ai_ops_type')" width="90">
                <template #default="{ row }">
                  <el-tag :type="row.type === 'analyze' ? 'info' : 'warning'" size="small">
                    {{ row.type === 'analyze' ? $t('api_detail.ai_ops_analyze') : $t('api_detail.ai_ops_scenario') }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column :label="$t('api_detail.ai_ops_status')" width="80">
                <template #default="{ row }">
                  <el-tag :type="row.status === 'success' ? 'success' : 'danger'" size="small">{{ row.status === 'success' ? $t('common.success') : $t('common.failed') }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column :label="$t('api_detail.ai_ops_message')" min-width="200">
                <template #default="{ row }">
                  <div>{{ row.message }}</div>
                  <div v-if="row.error" style="color:var(--red);font-size:11px;margin-top:2px;white-space:pre-wrap">{{ row.error }}</div>
                </template>
              </el-table-column>
              <el-table-column :label="$t('api_detail.ai_ops_scenarios')" width="80">
                <template #default="{ row }">{{ row.scenario_ids?.length || '-' }}</template>
              </el-table-column>
              <el-table-column :label="$t('common.time')" width="170">
                <template #default="{ row }">
                  <span class="text-2" style="font-size:11px">{{ row.created_at ? new Date(row.created_at).toLocaleString() : '-' }}</span>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-card>
      </div>
    </div>

    <!-- Run modal -->
    <el-dialog v-model="showRunModal" :title="$t('api_detail.exec_title')" width="520px">
      <div class="form-group">
        <label class="form-label">{{ $t('api_detail.exec_env') }}</label>
        <el-select v-model="runEnvironmentId" :placeholder="$t('api_detail.exec_env_placeholder')" style="width:100%" clearable>
          <el-option
            v-for="env in environments"
            :key="env.id"
            :value="env.id"
            :label="env.name"
          >
            <span>{{ env.name }}</span>
            <span v-if="env.base_url" class="mono text-3" style="font-size:10px;margin-left:8px">{{ env.base_url }}</span>
          </el-option>
        </el-select>
      </div>
      <!-- P1-5: 调试器结构化编辑 —— 替代原单 textarea，支持 Params/Headers/Body 分 Tab + JSON 高级模式 + 请求历史 -->
      <div class="form-group">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <label class="form-label" style="margin:0">{{ $t('api_detail.exec_override') }}</label>
          <el-radio-group v-model="overrideMode" size="small">
            <el-radio-button value="structured">{{ $t('api_detail.override_structured') }}</el-radio-button>
            <el-radio-button value="json">{{ $t('api_detail.override_json') }}</el-radio-button>
          </el-radio-group>
        </div>
        <!-- 结构化模式：Params / Headers / Body 三 Tab -->
        <div v-if="overrideMode === 'structured'">
          <el-tabs v-model="runActiveTab" type="border-card">
            <!-- Params Tab：query 参数 KV 编辑 -->
            <el-tab-pane :label="$t('api_detail.tab_params')" name="params">
              <div v-for="(p, i) in runParams" :key="i" class="kv-row">
                <el-input v-model="p.key" :placeholder="$t('api_detail.kv_key')" size="small" style="flex:1" />
                <el-input v-model="p.value" :placeholder="$t('api_detail.kv_value')" size="small" style="flex:1" />
                <el-button size="small" :icon="Close" circle @click="runParams.splice(i,1)" />
              </div>
              <el-button size="small" @click="runParams.push({ key: '', value: '' })">{{ $t('api_detail.add_param') }}</el-button>
            </el-tab-pane>
            <!-- Headers Tab：请求头 KV 编辑 -->
            <el-tab-pane :label="$t('api_detail.tab_headers')" name="headers">
              <div v-for="(h, i) in runHeaders" :key="i" class="kv-row">
                <el-input v-model="h.key" :placeholder="$t('api_detail.kv_key')" size="small" style="flex:1" />
                <el-input v-model="h.value" :placeholder="$t('api_detail.kv_value')" size="small" style="flex:1" />
                <el-button size="small" :icon="Close" circle @click="runHeaders.splice(i,1)" />
              </div>
              <el-button size="small" @click="runHeaders.push({ key: '', value: '' })">{{ $t('api_detail.add_header') }}</el-button>
            </el-tab-pane>
            <!-- Body Tab：按 body_type 动态渲染 -->
            <el-tab-pane :label="$t('api_detail.tab_body')" name="body">
              <el-input
                v-model="runBodyJson"
                type="textarea"
                :rows="6"
                :placeholder='$t("api_detail.body_json_placeholder")'
                :class="{ 'input-error': runJsonError }"
                @input="runJsonError = false"
              />
              <span v-if="runJsonError" class="field-error">{{ $t('api_detail.exec_json_error') }}</span>
            </el-tab-pane>
          </el-tabs>
        </div>
        <!-- JSON 高级模式：原 textarea，power user 直接编辑完整 override 对象 -->
        <div v-else>
          <el-input
            v-model="runOverride"
            type="textarea"
            :rows="6"
            :placeholder="$t('api_detail.exec_json_placeholder')"
            :class="{ 'input-error': runJsonError }"
            @input="runJsonError = false"
          />
          <span v-if="runJsonError" class="field-error">{{ $t('api_detail.exec_json_error') }}</span>
        </div>
      </div>
      <!-- P1-5: 请求历史 —— localStorage 存最近 20 次，一键回填 -->
      <div v-if="runHistory.length" class="form-group">
        <label class="form-label">{{ $t('api_detail.run_history') }}</label>
        <div class="run-history-list">
          <div
            v-for="(h, i) in runHistory"
            :key="i"
            class="run-history-item"
            @click="applyHistory(h)"
          >
            <span :class="['dot', h.passed ? 'dot-green' : 'dot-red']"></span>
            <span class="text-2" style="font-size:11px">{{ h.time }}</span>
            <span class="text-3 mono" style="font-size:10px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ h.summary }}</span>
          </div>
        </div>
      </div>
      <div v-if="runResult" class="run-result" :class="runResult.passed ? 'run-ok' : 'run-fail'">
        <div class="run-result-header">
          <ResultTag :passed="runResult.passed" />
          <span class="mono text-2" style="font-size:12px">{{ runResult.steps?.[0]?.response_received?.status_code }} · {{ fmt.duration(runResult.duration_ms) }}</span>
        </div>
        <div v-if="runResult.steps?.[0]?.assert_results?.length" style="padding:12px 16px">
          <div v-for="ar in runResult.steps[0].assert_results" :key="ar.field" class="assert-row">
            <span :class="ar.passed ? 'dot dot-green' : 'dot dot-red'"></span>
            <span class="mono text-2" style="font-size:11px">{{ ar.field }}</span>
            <span class="text-3" style="font-size:11px">{{ ar.operator }} {{ ar.expected }}</span>
            <span class="mono" style="font-size:11px" :class="ar.passed ? 'green' : 'red'">→ {{ ar.actual }}</span>
          </div>
        </div>
        <pre class="code-block" style="margin:0 16px 16px;max-height:180px;overflow:auto">{{ jsonPretty(runResult.steps?.[0]?.response_received?.body) }}</pre>
      </div>
      <template #footer>
        <el-button @click="resetRunForm(); showRunModal = false">{{ $t('api_detail.close') }}</el-button>
        <el-button type="primary" @click="runApi" :disabled="running">
          {{ running ? $t('api_detail.exec_running') : $t('api_detail.exec_btn') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { use } from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import VChart from 'vue-echarts'
import { Close } from '@element-plus/icons-vue'
import { apiApi, scenarioApi, environmentApi, knowledgeApi, memoryApi, aiOpLogApi, generationApi, openWs } from '@/api'
import { useProjectStore, useToastStore } from '@/stores'
import { fmt, methodClass, jsonPretty } from '@/utils'
import ResultTag from '@/components/ResultTag.vue'
import AnalysisStatusTag from '@/components/AnalysisStatusTag.vue'
import ReviewPanel from '@/components/ReviewPanel.vue'
use([BarChart, GridComponent, TooltipComponent, CanvasRenderer])

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const toast = useToastStore()
const projectStore = useProjectStore()
const api   = ref({})
const aiWs  = ref(null)   // WebSocket 连接，onUnmounted 时同步清理
const loading = ref(true)  // 首次加载标记，避免闪白
const asserts = ref([])
const activeTab = ref('overview')
const analysisApplied = computed(() => ['applied', 'done'].includes(api.value?.analysis_status))
// P0-2：断言操作符从后端单一来源加载，替换原硬编码 15 种数组（后端实际支持 22 种）。
// operatorMeta 存全量元数据，operatorGroups 按 group 分组供 el-option-group 渲染，
// typeCandidates 供 type_match 操作符的类型下拉使用。
const operatorMeta = ref([])
const operatorGroups = ref([])
const typeCandidates = ref(['int', 'float', 'str', 'bool', 'list', 'dict', 'null'])
// json 类型 placeholder 默认值 — 避免模板内转义引号导致编译器报错
const expectedJsonPlaceholder = computed(() => t('api_detail.expected_json_placeholder', '{ "type": "object" }'))
// 辅助：按 operator 查找 expected_type，决定 expected 列控件渲染形态
function expectedType(op) {
  const item = operatorMeta.value.find(o => o.op === op)
  return item ? item.expected_type : 'text'
}
// 辅助：按 operator 查找 help_zh，用于下拉项 title 提示
function opHelp(op) {
  const item = operatorMeta.value.find(o => o.op === op)
  return item ? item.help_zh : ''
}
// in/not_in 的 expected 在后端是 list，前端用逗号分隔展示与输入
function formatMulti(v) {
  return Array.isArray(v) ? v.join(',') : (v ?? '')
}
function parseMulti(s) {
  // 空输入 → 空数组；否则按逗号拆分并去除空白、过滤空串
  if (!s || !String(s).trim()) return []
  return String(s).split(',').map(x => x.trim()).filter(x => x !== '')
}
// P0-2：组件挂载时从后端拉取操作符元数据，分组后供模板渲染
async function loadAssertOperators() {
  try {
    const res = await apiApi.assertOperators()
    operatorMeta.value = res.operators || []
    typeCandidates.value = res.type_candidates || typeCandidates.value
    // 按 group 聚合，保持后端定义顺序
    const groupMap = new Map()
    for (const item of operatorMeta.value) {
      if (!groupMap.has(item.group)) groupMap.set(item.group, [])
      groupMap.get(item.group).push(item)
    }
    operatorGroups.value = Array.from(groupMap, ([group, items]) => ({ group, items }))
  } catch (e) {
    // 接口失败时兜底使用最小 operator 集合，保证断言编辑不致完全不可用
    console.warn('load assert operators failed, fallback to minimal set', e)
    const fallback = ['eq','ne','contains','exists']
    operatorMeta.value = fallback.map(op => ({ op, group: 'compare', label_key: 'assert.operator_' + op, expected_type: 'text', help_zh: '' }))
    operatorGroups.value = [{ group: 'compare', items: operatorMeta.value }]
  }
}
loadAssertOperators()

// 知识库记忆辅助函数（与 settings/Index.vue 保持一致）
const typeColorMap = {
  field_pattern: '#4F8EF7',
  assertion_pattern: '#F7A84F',
  doc_pattern: '#4FF7A8',
  scenario_pattern: '#A84FF7',
  // 补充 knowledge_type_* 中缺失的 4 种类型颜色映射
  api: '#6C5CE7',
  scenario: '#00B894',
  monitor: '#FDCB6E',
  general: '#636E72',
}
function typeColor(type) { return typeColorMap[type] || '#999' }
function truncate(s, n) { return s && s.length > n ? s.slice(0, n) + '…' : s || '' }

// 标签翻译辅助：知识库/记忆标签可能为英文 key 或中文文本，尝试 i18n 后回退到原始值
// 同时处理 "memory.tag_api:UUID" 后缀，去掉 UUID 部分后查翻译
function tagLabel(tag) {
  // 处理 "memory.tag_xxx" 格式的后端前缀
  let clean = tag.startsWith('memory.tag_') ? tag.slice(11) : tag.startsWith('memory.') ? tag.slice(7) : tag
  // 去掉 ":UUID" 后缀（如 "tag_api:384fd1b8-..." → "tag_api"）
  const colonIdx = clean.indexOf(':')
  if (colonIdx !== -1) clean = clean.slice(0, colonIdx)
  const key = 'memory.tag_' + clean
  const translated = t(key)
  return translated !== key ? translated : tag
}

const showRunModal     = ref(false)
const runEnvironmentId = ref('')  // 当前选中的执行环境 ID
const environments     = ref([])  // 可用执行环境列表
const runOverride      = ref('')  // P1-5: JSON 高级模式文本（与结构化模式互斥）
const runResult        = ref(null)
const running          = ref(false)
const runJsonError     = ref(false)  // run modal JSON 格式错误标记
// P1-5: 调试器结构化编辑模式 —— Params/Headers/Body 分 Tab，替代原单 textarea
// overrideMode 切换 'structured'（默认，结构化）/ 'json'（高级，原 textarea）
const overrideMode     = ref('structured')
const runActiveTab     = ref('params')  // params / headers / body
// 结构化 override 数据：params/headers 为 KV 数组（支持增删），body 按 body_type 渲染
const runParams   = ref([])  // [{key, value}]
const runHeaders  = ref([])  // [{key, value}]
const runBodyText = ref('')  // body 文本（json/text/xml/raw）
const runBodyJson = ref('')  // json body 文本（独立便于 JSON 校验）
// 请求历史：localStorage 存最近 20 次 override 快照，支持一键回填
const runHistory  = ref([])
const RUN_HISTORY_KEY = 'apipulse_run_history'
const RUN_HISTORY_MAX = 20
const analyzing     = ref(false)
// P0-4: AI 流式输出内容（打字机效果），streamTaskType 标识当前生成类型（doc/asserts）
const streamContent   = ref('')
const streamTaskType  = ref('')
// P2-4: Mock 生成 + 契约校验状态
const mocking = ref(false)
const mockResult = ref(null)
const mockCases = ref([])
const mockCaseLoading = ref(false)
const contractChecking = ref(false)
const contractResult = ref(null)
const genScenarioting = ref(false)  // AI生成场景中
// 审核 tab：当前 API 的生成版本（支持按状态筛选）
const apiGenerations = ref([])         // 全部状态的 GenerationVersion 列表
const reviewLoading = ref(false)       // 加载中
const reviewFilterStatus = ref('')     // 审核 tab 状态筛选：''=全部, pending_review/accepted/rejected 等
const pendingReviewCount = ref(0)      // 待审核数量，用于 tab 徽章
const savingAsserts = ref(false)
const savingDoc = ref(false)
const docForm = ref({ summary: '', description: '', tags: [], params: [], response_fields: [] })
const relatedScenarios = ref([])  // 包含当前 API 的关联场景
const relatedKnowledge = ref([])   // 关联知识库条目（overview 卡片展示，最多5条）
const knowledgeEntries = ref([])  // 知识库子Tab 全部条目
const knowledgeSearch = ref('')       // 知识库子Tab搜索关键词
const knowledgeFilterType = ref('')   // 知识库子Tab类型筛选
const knowledgePage = ref(1)          // 知识库子Tab当前页码
const knowledgePageSize = ref(20)     // 知识库子Tab每页条数
const knowledgeTotal = ref(0)         // 知识库子Tab总数
const showKnowledgeEdit = ref(false)  // 编辑知识库条目弹窗可见性
const editingEntry = ref({})          // 当前编辑的知识库条目
const savingKnowledge = ref(false)    // 保存知识库条目中
const extractingMem = ref(false)  // 记忆提取中

// 知识库&记忆 Tab 子Tab状态
const knowledgeSubTab = ref('knowledge')  // 当前子Tab：knowledge | l1 | l2 | l3

// Memory(L1/L2/L3) 状态
const l1MemItems = ref([])    // L1长期记忆列表
const l1MemTotal = ref(0)     // L1总数
const l1MemPage = ref(1)      // L1当前页码
const l2MemItems = ref([])    // L2项目记忆列表
const l3MemItems = ref([])    // L3会话记忆列表
const memLoading = reactive({ l1: false, l2: false, l3: false })  // Memory加载状态
const aiOpsLogs    = ref([])     // AI 操作日志列表
const aiOpsLoading = ref(false)  // AI 操作日志加载中
const aiOpsLoaded  = ref(false)  // 是否已加载过 AI 操作日志（懒加载标记）
const statsData     = ref({ total: 0, passed: 0, avg_latency_ms: 0, trend: [] })
// 双通道状态跟踪：WebSocket + 轮询回退
let _pollTimer = null   // 轮询定时器（WS 降级时启用）
let _pollTimeout = null // 轮询超时定时器（最长 60s 后停止）
const POLL_INTERVAL = 3000   // 轮询间隔 3s
const POLL_MAX_DURATION = 60000  // 最大轮询时长 60s

const hasQuery = computed(() => Object.keys(api.value.request?.query_params || {}).length > 0)

function cloneJson(value) {
  if (value == null) return value
  try { return JSON.parse(JSON.stringify(value)) }
  catch { return value }
}

function objectToKvRows(obj) {
  return Object.entries(obj || {}).map(([key, value]) => ({
    key,
    value: typeof value === 'string' ? value : JSON.stringify(value),
  }))
}

function initRunFormDefaults() {
  const req = api.value.request || {}
  runJsonError.value = false
  overrideMode.value = 'structured'
  runActiveTab.value = 'params'
  runParams.value = objectToKvRows(req.query_params || {})
  runHeaders.value = objectToKvRows(req.headers || {})
  if (req.body !== undefined && req.body !== null && req.body !== '') {
    runBodyJson.value = typeof req.body === 'string'
      ? req.body
      : JSON.stringify(req.body, null, 2)
  } else {
    runBodyJson.value = ''
  }
  const defaultOverride = {}
  for (const row of runParams.value) {
    if (row.key) defaultOverride[row.key] = row.value
  }
  if (runBodyJson.value.trim()) {
    try {
      const parsed = JSON.parse(runBodyJson.value)
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        Object.assign(defaultOverride, parsed)
      } else {
        defaultOverride._body = parsed
        defaultOverride._body_type = 'json'
      }
    } catch {
      defaultOverride._body = runBodyJson.value
      defaultOverride._body_type = req.body_type || 'text'
    }
  }
  runOverride.value = Object.keys(defaultOverride).length ? JSON.stringify(defaultOverride, null, 2) : ''
}

// 质量评分细分：将各维度的原始分数转换为满分百分比，用于进度条展示
const qualityBreakdown = computed(() => {
  const b = api.value.quality?.breakdown || {}
  const rows = [
    { key: 'analysis', max: 20 },
    { key: 'doc', max: 30 },
    { key: 'asserts', max: 30 },
    { key: 'scenario', max: 10 },
    { key: 'response', max: 10 },
  ]
  return rows.map(row => {
    const score = b[row.key] || 0
    return {
      ...row,
      score,
      percent: Math.round((score / row.max) * 100),
      label: t('quality.breakdown_' + row.key),
    }
  })
})

function qualityColor(level) {
  const map = {
    low: '#3ecf8e',
    medium: '#f0b44c',
    high: '#f06f4f',
    critical: '#f06060',
  }
  // 未知 level 回退到 critical 颜色（最显眼），确保问题可见
  return map[level] || map.critical
}

function qualityTagType(level) {
  return level === 'low' ? 'success' : level === 'medium' ? 'warning' : 'danger'
}

const statsChartOpt = computed(() => {
  const trend = statsData.value.trend || []
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', backgroundColor: '#1c2030', borderColor: '#2a2f45', textStyle: { color: '#e2e6f0', fontSize: 12 } },
    grid: { left: 50, right: 20, top: 10, bottom: 24 },
    xAxis: {
      type: 'category',
      data: trend.map((_, i) => i + 1),
      axisLine: { lineStyle: { color: '#2a2f45' } },
      axisLabel: { color: '#555d78', fontSize: 10 },
    },
    yAxis: [
      { type: 'value', name: 'ms', axisLabel: { color: '#555d78', fontSize: 10 }, axisLine: { show: false }, splitLine: { lineStyle: { color: '#1c2030' } } },
    ],
    series: [
      { type: 'bar', data: trend.map(t => ({ value: t.latency_ms, itemStyle: { color: t.passed ? '#3ecf8e' : '#f06060' } })), barMaxWidth: 12 },
    ],
  }
})

async function load() {
  try {
    api.value = await apiApi.get(route.params.id)
    initDocForm()  // 初始化 AI 文档编辑表单
    await loadAsserts()
    loadRelatedScenarios()  // 异步加载关联场景，不阻塞页面
    loadKnowledgeOverview() // 异步加载关联知识库&记忆概述，不阻塞页面
    loadKnowledgeEntries()  // 异步加载知识库tab数据，不阻塞页面
    loadMockCases()         // 异步加载 API Mock Case，供资产页直接管理
  } catch (e) {
    // 加载失败（如 API 已删除、权限不足等）：显示错误提示，页面仍可交互
    toast.error(e.message || t('api_detail.load_detail_failed'))
  } finally {
    loading.value = false
  }
}

async function loadRelatedScenarios() {
  try {
    // 查询 steps 中包含当前 api_id 的场景（关联场景/测试用例）
    const res = await scenarioApi.list({ project_id: projectStore.current, api_id: route.params.id, limit: 20 })
    relatedScenarios.value = res.items || []
  } catch { /* 静默失败，关联场景为辅助功能 */ }
}

// 时间格式化：将 ISO/datetime 字符串转为可读格式
function formatTime(raw) {
  if (!raw) return '—'
  try {
    const d = new Date(raw)
    if (isNaN(d.getTime())) return raw // 非标准格式直接返回原值
    const pad = (n) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  } catch { return raw }
}

// 记忆类型翻译：去掉 "memory.type_" 前缀后尝试 i18n 翻译
function typeLabel(type) {
  if (!type) return '—'
  const clean = type.startsWith('memory.type_') ? type.slice(12) : type
  const memoryKey = 'memory.type_' + clean
  const translated = t(memoryKey)
  if (translated !== memoryKey) return translated
  const knowledgeKey = 'knowledge_type_' + clean
  const kTranslated = t(knowledgeKey)
  return kTranslated !== knowledgeKey ? kTranslated : clean
}

// 从 API 路径和标签构建搜索关键词，用于知识库和记忆关联检索
function buildSearchKeywords() {
  const apiData = api.value
  const pathSegments = (apiData.request?.path || '')
    .replace(/\/\{[^}]+\}/g, '')
    .split('/').filter(Boolean)
  const tags = (apiData.tags || []).filter(Boolean)
  const pathStr = pathSegments.join(' ')
  return [pathStr, ...tags].filter(Boolean).join(' ')
}

// 加载概览卡片关联知识库（最多5条，使用多层回退确保有结果）
async function loadKnowledgeOverview() {
  try {
    // 使用逐层回退搜索：路径+tags → 仅tags → 首个路径段 → 空
    const apiData = api.value
    const pathSegments = (apiData.request?.path || '')
      .replace(/\/\{[^}]+\}/g, '')
      .split('/').filter(Boolean)
    const tags = (apiData.tags || []).filter(Boolean)

    const searches = []
    const pathStr = pathSegments.join(' ')
    const exact = [pathStr, ...tags].filter(Boolean).join(' ')
    if (exact) searches.push(exact)
    const tagsOnly = tags.join(' ')
    if (tagsOnly && !searches.includes(tagsOnly)) searches.push(tagsOnly)
    if (pathSegments.length > 0) {
      const broad = pathSegments[0]
      if (broad && !searches.includes(broad)) searches.push(broad)
    }
    if (!searches.includes('')) searches.push('')

    for (const search of searches) {
      const res = await knowledgeApi.list({ project_id: projectStore.current, search, limit: 5 })
      if (res.items?.length) {
        relatedKnowledge.value = res.items
        return
      }
    }
    relatedKnowledge.value = []
  } catch (e) {
    toast.error(e.message || t('api_detail.knowledge_load_failed'))
  }
}

// 加载知识库条目（带搜索、类型筛选和分页）
async function loadKnowledgeEntries() {
  try {
    const params = {
      project_id: projectStore.current,
      skip: (knowledgePage.value - 1) * knowledgePageSize.value,
      limit: knowledgePageSize.value,
    }
    // 知识库子Tab有手动输入搜索词则用搜索词，否则用 API 关键词
    const search = knowledgeSearch.value || buildSearchKeywords()
    if (search) params.search = search
    if (knowledgeFilterType.value) params.type = knowledgeFilterType.value
    const res = await knowledgeApi.list(params)
    knowledgeEntries.value = res.items || []
    knowledgeTotal.value = res.total || 0
  } catch (e) {
    toast.error(e.message || t('api_detail.knowledge_load_failed'))
  }
}

// 加载 L1 长期记忆（跨项目，按当前 API 关联过滤）
async function loadL1Memories() {
  memLoading.l1 = true
  try {
    const params = { skip: (l1MemPage.value - 1) * 20, limit: 20 }
    if (route.params.id) params.api_id = route.params.id
    const res = await memoryApi.listL1(params)
    l1MemItems.value = res.items || []
    l1MemTotal.value = res.total || 0
  } catch (e) {
    console.error('load L1 memories failed:', e)
  } finally {
    memLoading.l1 = false
  }
}

// 加载 L2 项目记忆（按当前 API 关联过滤，通过 tags 中的 api:UUID 匹配）
async function loadL2Memories() {
  memLoading.l2 = true
  try {
    const params = { project_id: projectStore.current, limit: 200 }
    if (route.params.id) params.api_id = route.params.id
    const res = await memoryApi.listL2(params)
    l2MemItems.value = res.items || []
  } catch (e) {
    console.error('load L2 memories failed:', e)
  } finally {
    memLoading.l2 = false
  }
}

// 加载 L3 会话记忆（按当前 API 关联过滤，通过 tags 中的 api:UUID 匹配）
async function loadL3Memories() {
  memLoading.l3 = true
  try {
    const params = { project_id: projectStore.current, limit: 200 }
    if (route.params.id) params.api_id = route.params.id
    const res = await memoryApi.listL3(params)
    l3MemItems.value = res.items || []
  } catch (e) {
    console.error('load L3 memories failed:', e)
  } finally {
    memLoading.l3 = false
  }
}

// 知识库&记忆子Tab切换时按需加载（@tab-change 事件 + watch 双重保障）
function onKnowledgeSubTabChange(name) {
  if (name === 'knowledge') loadKnowledgeEntries()
  else if (name === 'l1') loadL1Memories()
  else if (name === 'l2') loadL2Memories()
  else if (name === 'l3') loadL3Memories()
}
watch(knowledgeSubTab, (newTab) => { onKnowledgeSubTabChange(newTab) })

// 子Tab刷新按钮
function reloadActiveSubTab() {
  onKnowledgeSubTabChange(knowledgeSubTab.value)
}

function editKnowledgeEntry(m) {
  editingEntry.value = {
    id: m.id,
    title: m.title || '',
    content: m.content || '',
    tags: [...(m.tags || [])],
  }
  showKnowledgeEdit.value = true
}

async function saveKnowledgeEntry() {
  savingKnowledge.value = true
  try {
    await knowledgeApi.update(editingEntry.value.id, {
      title: editingEntry.value.title,
      content: editingEntry.value.content,
      tags: editingEntry.value.tags,
    })
    toast.success(t('knowledge_toast_updated'))
    showKnowledgeEdit.value = false
    await loadKnowledgeEntries()
  } catch (e) {
    toast.error(e.message)
  } finally {
    savingKnowledge.value = false
  }
}

async function extractMemories() {
  if (!api.value || !analysisApplied.value) return
  extractingMem.value = true
  try {
    await knowledgeApi.extract(route.params.id)
    toast.success(t('api_detail.mem_extracted', { n: 1 }))
    await loadKnowledgeOverview()
    await loadKnowledgeEntries()
  } catch (e) {
    toast.error(e.message)
  } finally {
    extractingMem.value = false
  }
}

async function loadAsserts() {
  try {
    const rules = await apiApi.getAsserts(route.params.id)
    asserts.value = rules.map(r => ({ ...r }))
  } catch (e) {
    toast.error(e.message || t('api_detail.load_asserts_failed'))
  }
}

function addAssertRow() {
  asserts.value.push({ field: 'status_code', operator: 'eq', expected: 200, risk_level: 'medium', description: '' })
}

// ── AI 文档编辑 ──
// 初始化 docForm 从 api.doc 复制
function initDocForm() {
  const d = api.value.doc
  if (d) {
    docForm.value = {
      summary: d.summary || '',
      description: d.description || '',
      tags: [...(d.tags || [])],
      params: (d.params || []).map(p => ({ name: p.name || '', location: p.location || 'body', type: p.type || 'string', required: !!p.required, description: p.description || '', example: p.example || '' })),
      response_fields: (d.response_fields || []).map(p => ({ name: p.name || '', type: p.type || 'string', description: p.description || '', example: p.example || '' })),
    }
  } else {
    // api.doc 为 null（未分析或分析失败）：重置表单为空，避免残留上一个 API 的编辑数据
    docForm.value = { summary: '', description: '', tags: [], params: [], response_fields: [] }
  }
}

function addDocParam() {
  if (!docForm.value.params) docForm.value.params = []
  docForm.value.params.push({ name: '', location: 'body', type: 'string', required: false, description: '', example: '' })
}

function addDocRespField() {
  if (!docForm.value.response_fields) docForm.value.response_fields = []
  docForm.value.response_fields.push({ name: '', type: 'string', description: '', example: '' })
}

async function saveDoc() {
  savingDoc.value = true
  try {
    await apiApi.update(route.params.id, { doc: docForm.value })
    // 更新本地 api.doc 使概览 tab 同步（深拷贝避免共享嵌套对象引用）
    api.value.doc = JSON.parse(JSON.stringify(docForm.value))
    toast.success(t('api_detail.doc_saved'))
  } catch (e) { toast.error(e.message || t('api_detail.save_doc_failed')) }
  finally { savingDoc.value = false }
}

async function saveAsserts() {
  savingAsserts.value = true
  try {
    const cleaned = asserts.value.map(r => ({
      ...r,
      // 三层判断清理 expected 值：null/undefined 保持 null，非数字字符串保持原样，空字符串转 null，数字字符串转 Number
      // isNaN(null) 返回 false（null 被隐式转换为 0），需先判空避免 null 被错误转为 0
      expected: r.expected == null ? null : (isNaN(r.expected) ? r.expected : (r.expected === '' ? null : Number(r.expected))),
    }))
    await apiApi.replaceAsserts(route.params.id, cleaned)
    toast.success(t('api_detail.asserts_saved'))
  } catch (e) { toast.error(e.message) }
  finally { savingAsserts.value = false }
}

async function runApi() {
  running.value = true
  runResult.value = null
  try {
    let override = {}
    let overrideSummary = ''  // 用于请求历史摘要展示
    if (overrideMode.value === 'structured') {
      // P1-5: 结构化模式 —— 把 params/headers/body 合并成 override 对象
      // params → override_params（后端 run_single 会把 override_params 同时覆盖 query 和 body）
      const paramsObj = {}
      for (const p of runParams.value) {
        if (p.key.trim()) paramsObj[p.key.trim()] = p.value
      }
      const headersObj = {}
      for (const h of runHeaders.value) {
        if (h.key.trim()) headersObj[h.key.trim()] = h.value
      }
      // body：JSON 文本解析为对象，解析失败报错
      if (runBodyJson.value.trim()) {
        try {
          const parsedBody = JSON.parse(runBodyJson.value)
          if (parsedBody && typeof parsedBody === 'object' && !Array.isArray(parsedBody)) {
            override = parsedBody
          } else {
            override = { _body: parsedBody, _body_type: 'json' }
          }
        } catch {
          override = { _body: runBodyJson.value, _body_type: api.value.request?.body_type || 'text' }
        }
      }
      // params 合并到 override（与原 override_params 语义一致）
      override = { ...override, ...paramsObj }
      overrideSummary = [
        Object.keys(paramsObj).length ? `${Object.keys(paramsObj).length} params` : '',
        Object.keys(headersObj).length ? `${Object.keys(headersObj).length} headers` : '',
        runBodyJson.value.trim() ? 'body' : '',
      ].filter(Boolean).join(', ') || 'empty'
    } else {
      // JSON 高级模式：直接解析 textarea
      if (runOverride.value.trim()) {
        try { override = JSON.parse(runOverride.value) } catch { runJsonError.value = true; toast.error(t('api_detail.json_parse_error')); running.value = false; return }
        overrideSummary = JSON.stringify(override).slice(0, 60)
      } else {
        overrideSummary = 'empty'
      }
    }
    const body = { override_params: override }
    // P1-5: 结构化模式的 headers 作为独立字段传（后端 run_single_api 支持 override_headers），
    // 不能塞进 override_params（那会污染 body 字段）。JSON 模式无 headers 编辑，不传。
    if (overrideMode.value === 'structured') {
      const headersObj = {}
      for (const h of runHeaders.value) {
        if (h.key.trim()) headersObj[h.key.trim()] = h.value
      }
      if (Object.keys(headersObj).length) {
        body.override_headers = headersObj
      }
    }
    // 仅在用户选择了执行环境时传入 environment_id，未选择时不传字段（后端使用默认环境）
    if (runEnvironmentId.value) {
      body.environment_id = runEnvironmentId.value
    }
    runResult.value = await apiApi.run(route.params.id, body)
    toast[runResult.value.passed ? 'success' : 'error'](runResult.value.passed ? t('api_detail.toast_pass') : t('api_detail.toast_fail', { reason: runResult.value.failure_reason || t('common.none') }))
    // P1-5: 执行完成后保存请求历史（无论成功失败都记，失败历史也有调试价值）
    saveRunHistory({
      time: new Date().toLocaleTimeString(),
      passed: runResult.value.passed,
      summary: overrideSummary,
      // 快照完整 override 数据，回填时还原结构化表单
      snapshot: {
        mode: overrideMode.value,
        params: JSON.parse(JSON.stringify(runParams.value)),
        headers: JSON.parse(JSON.stringify(runHeaders.value)),
        bodyJson: runBodyJson.value,
        override: runOverride.value,
        environmentId: runEnvironmentId.value,
      },
    })
  } catch (e) { toast.error(e.message) }
  finally { running.value = false }
}

// P1-5: 保存请求历史到 localStorage（按 API ID 隔离，避免不同接口历史混淆）
function saveRunHistory(entry) {
  // 历史 key 带 api_id，按接口隔离
  const key = `${RUN_HISTORY_KEY}_${route.params.id}`
  let list = []
  try {
    const raw = localStorage.getItem(key)
    if (raw) list = JSON.parse(raw)
  } catch { list = [] }
  // 新条目插到头部，超过上限截断
  list.unshift(entry)
  if (list.length > RUN_HISTORY_MAX) list = list.slice(0, RUN_HISTORY_MAX)
  try { localStorage.setItem(key, JSON.stringify(list)) } catch { /* 配额满静默忽略 */ }
  runHistory.value = list
}

// P1-5: 一键回填历史快照到当前编辑表单
function applyHistory(h) {
  if (!h.snapshot) return
  const s = h.snapshot
  overrideMode.value = s.mode || 'structured'
  runParams.value = JSON.parse(JSON.stringify(s.params || []))
  runHeaders.value = JSON.parse(JSON.stringify(s.headers || []))
  runBodyJson.value = s.bodyJson || ''
  runOverride.value = s.override || ''
  runEnvironmentId.value = s.environmentId || ''
  toast.info(t('api_detail.history_applied'))
}

// P1-1: 跳转数据工厂，带 api_id query（工厂页自动创建模板并推断字段）
function goFactory() {
  router.push(`/factory?api_id=${route.params.id}`)
}

function goMockServices() {
  router.push(`/mock-services?api_id=${route.params.id}`)
}

// P2: 头部"更多"下拉分发
function handleMoreAction(cmd) {
  if (cmd === 'mock') generateMock()
  else if (cmd === 'contract') checkContract()
  else if (cmd === 'factory') goFactory()
  else if (cmd === 'mockService') goMockServices()
}

// P1-4: 质量建议可点击 —— 每条建议映射到一个具体操作
function suggestionAction(suggestion) {
  // 文档类建议 → 跳转 aidoc tab
  if (['add_summary', 'add_description', 'document_params', 'document_response_fields'].includes(suggestion)) {
    activeTab.value = 'aidoc'
    return
  }
  // 断言类建议 → 跳转 asserts tab
  if (suggestion === 'add_business_asserts') {
    activeTab.value = 'asserts'
    return
  }
  // 场景生成建议 → 触发生成
  if (suggestion === 'generate_scenario') {
    genScenarioForApi()
    return
  }
  // AI 分析类建议 → 触发分析
  if (['retry_ai_analysis', 'run_ai_analysis', 'capture_status_code', 'capture_response_body'].includes(suggestion)) {
    analyzeApi(suggestion === 'retry_ai_analysis')
    return
  }
}

// P2-4: Mock 生成 —— 基于 doc.response_fields 生成类型正确的 Mock 响应（前端联调用）
async function generateMock() {
  mocking.value = true
  mockResult.value = null
  try {
    const res = await apiApi.mock(route.params.id)
    mockResult.value = res
    toast.success(t('api_detail.mock_generated'))
  } catch (e) {
    toast.error(e.message || t('api_detail.mock_error'))
  } finally {
    mocking.value = false
  }
}

async function loadMockCases() {
  if (!route.params.id) return
  mockCaseLoading.value = true
  try {
    const res = await apiApi.listMockCases(route.params.id)
    mockCases.value = res.items || []
  } catch (e) {
    mockCases.value = []
  } finally {
    mockCaseLoading.value = false
  }
}

async function createMockCaseFromDoc() {
  mocking.value = true
  try {
    const created = await apiApi.createMockCase(route.params.id, { name: '文档默认 Case', from_doc: true })
    mockResult.value = created.response
    await loadMockCases()
    toast.success('已生成 Mock Case')
  } catch (e) {
    toast.error(e.message || t('api_detail.mock_error'))
  } finally {
    mocking.value = false
  }
}

async function saveMockPreviewAsCase() {
  if (!mockResult.value) return
  try {
    const created = await apiApi.createMockCase(route.params.id, {
      name: `Mock Case ${new Date().toLocaleString()}`,
      response: mockResult.value,
    })
    await loadMockCases()
    toast.success(`已保存 ${created.name}`)
  } catch (e) {
    toast.error(e.message || '保存 Mock Case 失败')
  }
}

async function previewMockCase(row) {
  try {
    mockResult.value = await apiApi.mock(route.params.id, undefined, row.id)
  } catch (e) {
    toast.error(e.message || 'Mock Case 预览失败')
  }
}

async function deleteMockCase(row) {
  try {
    await apiApi.deleteMockCase(route.params.id, row.id)
    if (mockResult.value?.case_id === row.id) mockResult.value = null
    await loadMockCases()
    toast.success('已删除 Mock Case')
  } catch (e) {
    toast.error(e.message || '删除 Mock Case 失败')
  }
}

// P2-4: 契约校验 —— 实际响应 vs doc 字段结构比对
async function checkContract() {
  // 用最近执行结果的响应体校验（runResult 中的 steps[0].response_received.body）
  const actualBody = runResult.value?.steps?.[0]?.response_received?.body
  if (!actualBody) {
    toast.error(t('api_detail.contract_no_response'))
    return
  }
  contractChecking.value = true
  contractResult.value = null
  try {
    const res = await apiApi.contractCheck(route.params.id, actualBody)
    contractResult.value = res
    if (res.passed) {
      toast.success(t('api_detail.contract_passed'))
    } else {
      toast.warning(res.summary)
    }
  } catch (e) {
    toast.error(e.message || t('api_detail.contract_error'))
  } finally {
    contractChecking.value = false
  }
}

// P1-5: 打开 run modal 时重置表单 + 加载当前接口的历史
function openRunModal() {
  initRunFormDefaults()
  showRunModal.value = true
  // 加载当前接口的请求历史
  const key = `${RUN_HISTORY_KEY}_${route.params.id}`
  try {
    const raw = localStorage.getItem(key)
    runHistory.value = raw ? JSON.parse(raw) : []
  } catch { runHistory.value = [] }
}

// P1-5: 关闭 run modal 时重置结构化表单（保留环境选择，便于连续调试）
function resetRunForm() {
  initRunFormDefaults()
  runResult.value = null
  runJsonError.value = false
}

// 双通道状态轮询：WebSocket 实时推送 + 轮询回退
function startStatusPolling() {
  stopStatusPolling()  // 清除已在运行的轮询
  const apiId = route.params.id
  let startedAt = Date.now()

  // 轮询回退：每 3s 调用 GET /apis/{id}，检查 analysis_status 变化
  _pollTimer = setInterval(async () => {
    try {
      const fresh = await apiApi.get(apiId)
      if (fresh.analysis_status !== api.value.analysis_status) {
        api.value.analysis_status = fresh.analysis_status
        api.value.analysis_error = fresh.analysis_error || ''
        // 终态时拉取完整数据（文档、断言等），idle 不触发拉取因为无变更
        if (['applied', 'done', 'pending_review', 'failed'].includes(fresh.analysis_status)) {
          api.value = { ...api.value, ...fresh }
          await loadAsserts()
        }
      }
      // 到达终态（applied/pending_review/failed/idle）或超时后停止轮询，避免无限请求
      const elapsed = Date.now() - startedAt
      if (['applied', 'done', 'pending_review', 'failed', 'idle'].includes(fresh.analysis_status) || elapsed > POLL_MAX_DURATION) {
        stopStatusPolling()
      }
    } catch { /* 静默失败，下次轮询重试 */ }
  }, POLL_INTERVAL)

  // 最长 60s 超时强制停止
  _pollTimeout = setTimeout(() => stopStatusPolling(), POLL_MAX_DURATION)
}

function stopStatusPolling() {
  if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null }
  if (_pollTimeout) { clearTimeout(_pollTimeout); _pollTimeout = null }
}

// WebSocket 消息处理：匹配 api_id 后更新本页状态，同时处理场景生成完成事件
// 消息类型路由：scenario_done/failed → 全项目广播，status → 按 api_id 过滤
function onAiWsMessage(msg) {
  if (!msg) return
  // scenario_done：场景生成任务完成，广播给所有页面，刷新关联场景列表
  if (msg.type === 'scenario_done') {
    toast.successAction(
      t('scenarios.gen_ws_done', { count: msg.generation_ids?.length ?? 0 }),
      t('scenarios.go_review'),
      () => router.push('/generations?type=scenario&status=pending_review')
    )
    loadRelatedScenarios()
    return
  }
  // scenario_failed：场景生成任务失败，广播错误信息
  if (msg.type === 'scenario_failed') {
    toast.error(t('scenarios.gen_ws_failed', { error: msg.error || '' }))
    return
  }
  // generation_pending_review：AI 生成新版本待审核，刷新待审核列表
  if (msg.type === 'generation_pending_review') {
    if (msg.api_id === route.params.id) loadApiGenerations()
    return
  }
  // P0-4: ai_chunk 流式增量——按 job_id(=api_id) 过滤，仅渲染当前 API 的生成进度
  // 收到增量时累积到 streamContent，done 时清空打字机区域
  if (msg.type === 'ai_chunk') {
    if (msg.job_id !== route.params.id) return
    if (msg.done) {
      // 生成完成：延迟清空，让用户看到最终内容后再淡出
      setTimeout(() => { streamContent.value = ''; streamTaskType.value = '' }, 1500)
    } else if (msg.delta) {
      streamContent.value += msg.delta
      streamTaskType.value = msg.task_type || ''
    }
    return
  }
  // 非当前 API 的状态消息 → 静默忽略，不处理其他 API 的状态变更
  if (msg.api_id !== route.params.id) return
  if (msg.type === 'status') {
    api.value.analysis_status = msg.status
    if (msg.error) api.value.analysis_error = msg.error
    // 到达终态时拉取完整数据（仅在轮询未运行时补拉，否则轮询会自动拉取）
    if (msg.status === 'done' || msg.status === 'failed') {
      if (!_pollTimer) load()
    }
  }
}

async function analyzeApi(force = false) {
  analyzing.value = true
  try {
    await apiApi.analyze(route.params.id, force)
    // 设置本地状态为 queued，启动双通道轮询等待状态变更
    api.value.analysis_status = 'queued'
    api.value.analysis_error = ''
    startStatusPolling()
    toast.info(t('api_detail.analyze_queued_toast'))
  } catch (e) { toast.error(e.message) }
  finally { analyzing.value = false }
}

// AI 生成场景：为当前API生成单接口场景(single)
async function genScenarioForApi() {
  genScenarioting.value = true
  try {
    const res = await scenarioApi.generate([route.params.id], projectStore.current, 'single')
    if (res.queued) {
      toast.info(t('scenarios.gen_queued'))
    } else {
      toast.success(t('scenarios.gen_done', { count: res.generation_ids?.length ?? 0 }))
    }
  } catch (e) { toast.error(e.message) }
  finally { genScenarioting.value = false }
}

async function analyzeDoc(force = false) {
  // 仅入队文档生成任务
  analyzing.value = true
  try {
    await apiApi.analyzeDoc(route.params.id, force)
    api.value.analysis_status = 'queued'
    api.value.analysis_error = ''
    startStatusPolling()
    toast.info(t('api_detail.analyze_queued_toast'))
  } catch (e) { toast.error(e.message) }
  finally { analyzing.value = false }
}

async function analyzeAsserts(force = false) {
  // 仅入队断言生成任务
  analyzing.value = true
  try {
    await apiApi.analyzeAsserts(route.params.id, force)
    api.value.analysis_status = 'queued'
    api.value.analysis_error = ''
    startStatusPolling()
    toast.info(t('api_detail.analyze_queued_toast'))
  } catch (e) { toast.error(e.message) }
  finally { analyzing.value = false }
}

async function loadStats() {
  try {
    statsData.value = await apiApi.stats(route.params.id, 50)
  } catch (e) {
    toast.error(e.message || t('api_detail.load_stats_failed'))
  }
}

// 懒加载 AI 操作日志：首次切换到 aiops 选项卡时拉取数据
async function loadAiOpsLogs() {
  if (aiOpsLoaded.value || aiOpsLoading.value) return
  aiOpsLoading.value = true
  try {
    const res = await aiOpLogApi.list({ project_id: projectStore.current, api_id: route.params.id, limit: 50 })
    aiOpsLogs.value = res.items || []
    aiOpsLoaded.value = true
  } catch { /* 静默失败，AI 操作为辅助功能 */ }
  finally { aiOpsLoading.value = false }
}

// 加载当前 API 的生成版本（审核 tab 用），支持按状态筛选
async function loadApiGenerations() {
  if (reviewLoading.value) return
  reviewLoading.value = true
  try {
    const params = {
      project_id: projectStore.current,
      api_id: route.params.id,
      limit: 20,
    }
    // 仅在用户主动选择筛选状态时传入，默认展示全部状态
    if (reviewFilterStatus.value) params.status = reviewFilterStatus.value
    const res = await generationApi.list(params)
    apiGenerations.value = res.items || []
    // 单独查询待审核总数用于 tab 徽章
    if (!reviewFilterStatus.value || reviewFilterStatus.value === 'pending_review') {
      pendingReviewCount.value = res.total || 0
    }
  } catch (e) {
    console.error('加载生成版本列表失败:', e)
    toast.error(e.message || '加载生成版本列表失败')
  } finally { reviewLoading.value = false }
}

// 筛选变更时重置并重新加载
async function onReviewFilterChange() {
  await loadApiGenerations()
  // 重新获取待审核总数用于徽章
  if (reviewFilterStatus.value && reviewFilterStatus.value !== 'pending_review') {
    try {
      const res = await generationApi.list({
        project_id: projectStore.current,
        api_id: route.params.id,
        status: 'pending_review',
        limit: 1,
      })
      pendingReviewCount.value = res.total || 0
    } catch { /* ignore */ }
  }
}

// 审核操作完成后的回调：从列表中移除已处理的版本并刷新数据
function onReviewAccepted(genId) {
  apiGenerations.value = apiGenerations.value.filter(g => g.id !== genId)
  pendingReviewCount.value = Math.max(0, pendingReviewCount.value - 1)
  load()  // 刷新主体 API 数据（doc/asserts 变更后）
}
function onReviewRejected(genId) {
  apiGenerations.value = apiGenerations.value.filter(g => g.id !== genId)
  pendingReviewCount.value = Math.max(0, pendingReviewCount.value - 1)
}

// 监听选项卡切换，首次进入 aiops/review 时懒加载
watch(activeTab, (tab) => {
  if (tab === 'aiops') loadAiOpsLogs()
  if (tab === 'review') loadApiGenerations()
})

async function loadEnvironments() {
  try {
    // 加载执行环境列表，供执行弹窗选择
    environments.value = await environmentApi.list(projectStore.current)
  } catch {
    // 静默失败，不影响主流程
  }
}

onMounted(async () => {
  await load()
  // loadStats 不阻塞页面渲染；错误已在函数内处理
  loadStats()
  loadEnvironments()
  // 静默加载待审核版本列表，用于审核 tab 徽章
  loadApiGenerations()
  // 建立 WebSocket 连接接收 AI 分析状态推送（在 onUnmounted 中同步清理, 避免 await 后组件实例丢失）
  aiWs.value = openWs(`/ai-analysis?project_id=${encodeURIComponent(projectStore.current || api.value.project_id || 'default')}`, onAiWsMessage)
  // 如果当前 API 处于 queued/running 中间状态（如页面刷新后），启动轮询监控等待终态
  if (api.value.analysis_status === 'queued' || api.value.analysis_status === 'running') {
    startStatusPolling()
  }
})

onUnmounted(() => {
  // 清理 WebSocket 连接，避免组件卸载后残留连接
  if (aiWs.value?.terminate) aiWs.value.terminate()
  // 清理轮询定时器，避免组件卸载后回调操作已销毁的响应式数据
  stopStatusPolling()
})
</script>

<style scoped>
.detail-grid  { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.detail-field { display: flex; gap: 12px; padding: 8px 0; border-bottom: 1px solid var(--border); align-items: flex-start; }
.detail-field:last-child { border-bottom: none; }
.field-key    { width: 110px; flex-shrink: 0; font-size: 11px; font-weight: 600; color: var(--text-3); text-transform: uppercase; letter-spacing: .05em; padding-top: 2px; }
.field-val    { font-size: 12px; color: var(--text-2); word-break: break-all; }
.headers-grid { display: grid; grid-template-columns: auto 1fr; gap: 4px 16px; }
.assert-row   { display: flex; align-items: center; gap: 10px; padding: 4px 0; }
.run-result { border-radius: var(--radius); border: 1px solid var(--border); overflow: hidden; margin-top: 12px; }
.run-ok     { border-color: rgba(62,207,142,.3); }
.run-fail   { border-color: rgba(240,96,96,.3); }
.run-result-header { display: flex; align-items: center; gap: 10px; padding: 10px 16px; border-bottom: 1px solid var(--border); }
.input-error :deep(.el-textarea__inner) { border-color: var(--red) !important; background: rgba(240,96,96,.04); }
.field-error { font-size: 11px; color: var(--red); display: block; margin-top: 4px; }
.form-label { font-size: 11px; font-weight: 600; color: var(--text-3); text-transform: uppercase; letter-spacing: .05em; margin-bottom: 6px; }
.form-row { display: flex; gap: 12px; }
.quality-panel { margin-bottom: 12px; }
.quality-total {
  font-size: 22px;
  font-weight: 800;
  color: var(--text);
}
.quality-content {
  display: grid;
  grid-template-columns: 140px 1fr 280px;
  gap: 18px;
  align-items: center;
}
.quality-meter {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
}
.quality-breakdown {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.quality-breakdown-row {
  display: grid;
  grid-template-columns: 80px 1fr 52px;
  gap: 10px;
  align-items: center;
}
.quality-suggestions {
  display: flex;
  align-items: flex-start;
  align-content: flex-start;
  gap: 6px;
  flex-wrap: wrap;
  min-height: 72px;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-2);
}
.mock-case-card {
  margin-bottom: 12px;
}
.mock-case-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(260px, 0.9fr);
  gap: 16px;
}
.mock-preview-json {
  min-height: 180px;
  max-height: 300px;
  overflow: auto;
}
@media (max-width: 900px) { .detail-grid { grid-template-columns: 1fr; } }
@media (max-width: 1100px) { .quality-content { grid-template-columns: 1fr; } }
@media (max-width: 900px) { .mock-case-layout { grid-template-columns: 1fr; } }

/* 关联记忆卡片 */
.memory-cards {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.memory-card-item {
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-2);
}
.memory-card-title {
  font-size: 13px;
  font-weight: 600;
  flex: 1;
}
.gap-8 { gap: 8px; }
.gap-4 { gap: 4px; }
.memory-confidence { font-size: 10px; color: var(--text-3); white-space: nowrap; }

/* 知识库 tab */
.knowledge-item-header {
  display: flex; align-items: center; justify-content: space-between;
  flex-wrap: wrap; gap: 8px;
}
.knowledge-item-title {
  font-size: 14px; font-weight: 600;
}

/* 审核 tab 徽章偏移 */
.review-badge {
  margin-left: 4px;
  vertical-align: top;
}
.review-badge :deep(.el-badge__content) {
  transform: translate(0, -2px);
}

/* 审核列表卡片间距 */
.review-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.review-item-card {
  border: 1px solid var(--border);
}
/* P0-4: AI 流式输出打字机面板样式 */
.ai-stream-panel {
  margin: 0 0 12px;
  padding: 12px 16px;
  background: var(--bg-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  border-left: 3px solid var(--accent);
}
/* P1-5: 结构化 KV 编辑行 + 请求历史样式 */
.kv-row {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
  align-items: center;
}
.run-history-list {
  max-height: 120px;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 4px;
}
.run-history-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 4px;
  cursor: pointer;
}
.run-history-item:hover {
  background: var(--bg-2);
}
.ai-stream-header {
  display: flex;
  align-items: center;
  margin-bottom: 8px;
}
.ai-stream-content {
  margin: 0;
  padding: 8px 12px;
  background: var(--bg-1);
  border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 320px;
  overflow-y: auto;
}
/* 闪烁光标模拟打字机效果 */
.ai-stream-content .cursor {
  animation: blink 1s step-end infinite;
  color: var(--accent);
}
@keyframes blink {
  0%, 50% { opacity: 1; }
  51%, 100% { opacity: 0; }
}
</style>
