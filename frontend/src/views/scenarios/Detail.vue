<template>
  <div class="page">
    <div class="page-header">
      <div class="flex items-center gap-12">
        <el-button @click="$router.back()">{{ $t('scenario_detail.back') }}</el-button>
        <div>
          <div class="page-title">{{ scenario.name }}</div>
          <div class="page-subtitle">{{ scenario.description }}</div>
        </div>
        <el-tag v-if="scenario.ai_generated" type="info">{{ $t('scenario_detail.ai_generated') }}</el-tag>
        <el-tag v-if="scenario.owner" type="warning" size="small">{{ $t('common.owner') }}: {{ scenario.owner }}</el-tag>
        <!-- 未保存变更指示器：提醒用户当前有未保存的修改 -->
        <el-tag v-if="hasUnsavedChanges" type="danger" size="small" effect="dark">{{ $t('scenario_detail.unsaved_indicator') }}</el-tag>
        <!-- 待审核 AI 生成场景提示：点击跳转审核中心 -->
        <el-tag v-if="pendingGenCount > 0" type="primary" size="small" effect="dark" class="review-hint-badge">
          <router-link to="/generations?type=scenario&status=pending_review" class="review-hint-link">
            {{ $t('scenario_detail.pending_review_hint', { count: pendingGenCount }) }}
          </router-link>
        </el-tag>
      </div>
      <div class="flex items-center gap-8">
        <!-- P1-3: 撤销/重做按钮全 tab 可见（此前仅 steps/dag tab 可见，用户在 result/history tab 找不到撤销入口） -->
        <el-button :disabled="!canUndo" size="small" @click="undo" :title="t('scenario_detail.undo_tip')">↩</el-button>
        <el-button :disabled="!canRedo" size="small" @click="redo" :title="t('scenario_detail.redo_tip')">↪</el-button>
        <el-button @click="saveScenario" :disabled="saving" :loading="saving">
          {{ saving ? $t('scenario_detail.saving') : $t('common.save') }}
        </el-button>
        <el-button size="small" @click="validateScenarioRemote" :loading="validating">
          {{ $t('scenario_detail.validate_btn') }}
        </el-button>
        <el-button size="small" type="warning" plain @click="quickFixDependencies">
          {{ $t('scenario_detail.quick_fix_dependencies') }}
        </el-button>
        <el-button type="primary" @click="runScenario" :disabled="running" :loading="running">
          {{ running ? $t('scenario_detail.running') : $t('scenario_detail.run_btn') }}
        </el-button>
        <!-- 从场景添加至数据工厂/巡检：通过 query param 跳转并预填表单 -->
        <el-dropdown trigger="click" @command="(cmd) => handleAddTo(cmd)">
          <el-button size="small">{{ $t('scenarios.add_to') }}</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="factory">{{ $t('scenarios.add_to_factory') }}</el-dropdown-item>
              <el-dropdown-item command="monitor">{{ $t('scenarios.add_to_monitor') }}</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </div>

    <div class="page-body" v-loading="loading">
      <el-tabs v-model="activeTab">
        <el-tab-pane :label="$t('scenario_detail.dag_tab')" name="dag" />
        <el-tab-pane :label="$t('scenario_detail.steps_edit_tab')" name="steps" />
        <el-tab-pane :label="$t('scenario_detail.result_tab')" name="result" />
        <el-tab-pane :label="$t('scenario_detail.history_tab')" name="history" />
      </el-tabs>
      <el-alert
        v-if="serverIssues.length"
        :title="$t('scenario_detail.server_validation_issue_count', { count: serverIssues.length })"
        :type="serverHasErrors ? 'error' : 'warning'"
        :closable="false"
        show-icon
        style="margin-bottom:12px"
      >
        <div class="validation-issue-list">
          <div v-for="(issue, idx) in serverIssues" :key="`${issue.code}-${issue.step_id}-${idx}`" class="validation-issue-row">
            <el-tag size="small" :type="issue.level === 'error' ? 'danger' : 'warning'">
              {{ issue.level === 'error' ? $t('scenario_detail.issue_error') : $t('scenario_detail.issue_warning') }}
            </el-tag>
            <span v-if="issue.step_id" class="mono">{{ issue.step_id }}</span>
            <span>{{ issue.message }}</span>
            <el-button v-if="issue.step_id" link type="primary" size="small" @click="locateIssue(issue)">{{ $t('scenario_detail.locate_issue') }}</el-button>
          </div>
        </div>
      </el-alert>

      <!-- DAG 可视化 (VueFlow) -->
      <div v-if="activeTab==='dag'">
        <el-card class="dag-card">
          <template #header>
            <div class="flex items-center justify-between">
              <span class="card-title">{{ $t('scenario_detail.dag_title') }}</span>
              <span class="text-3" style="font-size:11px">{{ $t('scenario_detail.dag_hint') }}</span>
            </div>
          </template>
          <div class="dag-vueflow-wrapper" style="position:relative">
            <VueFlow
              v-model="vfElements"
              :node-types="nodeTypes"
              :default-viewport="{ x: 0, y: 0, zoom: 1 }"
              :min-zoom="0.15"
              :max-zoom="2"
              :snap-to-grid="true"
              :snap-grid="[10, 10]"
              :connection-line-style="{ stroke: 'var(--brand, #4f8ef7)', strokeWidth: 2 }"
              fit-view-on-init
              @node-context-menu="vfNodeContextMenu"
              @node-drag-stop="vfNodeDragStop"
              @node-click="vfNodeClick"
              @edge-click="vfEdgeClick"
              @edge-mouse-enter="vfEdgeMouseEnter"
              @edge-mouse-leave="vfEdgeMouseLeave"
              @pane-click="vfPaneClick"
              @pane-context-menu="vfPaneContextMenu"
              @connect="vfConnect"
            >
              <Background pattern-color="var(--border)" :gap="20" />
              <Controls />
              <!-- P1-5: 自动排列按钮 —— 强制重算所有节点位置（kahnLayout 此前仅 pos=0 时生效，拖过后永不再排） -->
              <div class="dag-auto-layout-btn" @click="autoLayoutAll" :title="t('scenario_detail.auto_layout_tip')">
                ⠿ {{ t('scenario_detail.auto_layout') }}
              </div>
              <!-- API 步骤节点（#node-step） -->
              <template #node-step="nodeProps">
                <div
                  class="vf-step-node"
                  :class="{
                    'is-selected': selectedNodeId === nodeProps.id,
                    'in-container': nodeProps.data.inContainer,
                    'exec-state-queued': nodeProps.data.execState === 'queued',
                    'exec-state-running': nodeProps.data.execState === 'running',
                    'exec-state-passed': nodeProps.data.execState === 'passed',
                    'exec-state-failed': nodeProps.data.execState === 'failed',
                    'exec-state-skipped': nodeProps.data.execState === 'skipped',
                  }"
                  :style="{ background: nodeProps.data.bg, borderColor: nodeProps.data.stroke }"
                  :title="nodeProps.data.tooltip"
                >
                  <div class="node-line1 mono">{{ nodeProps.data.step_id }}</div>
                  <div class="node-line2">{{ truncate(nodeProps.data.label, 18) }}</div>
                  <Handle type="target" :position="Position.Left" class="vf-handle vf-handle-target" />
                  <Handle type="source" :position="Position.Right" class="vf-handle vf-handle-source" />
                </div>
              </template>
              <!-- 条件容器节点（#node-condition）：菱形边框 + 蓝色主题 -->
              <template #node-condition="nodeProps">
                <div
                  class="vf-container-node vf-node-cond"
                  :class="{
                    'is-selected': selectedNodeId === nodeProps.id,
                    'exec-state-queued': nodeProps.data.execState === 'queued',
                    'exec-state-running': nodeProps.data.execState === 'running',
                    'exec-state-passed': nodeProps.data.execState === 'passed',
                    'exec-state-failed': nodeProps.data.execState === 'failed',
                    'exec-state-skipped': nodeProps.data.execState === 'skipped',
                  }"
                >
                  <span class="container-icon">&#9671;</span>
                  <div class="container-body">
                    <div class="container-title mono">{{ nodeProps.data.step_id }}</div>
                    <div class="container-summary" v-if="nodeProps.data.condSummary">{{ nodeProps.data.condSummary }}</div>
                    <div class="container-children-count text-3">{{ nodeProps.data.childrenCount }} {{ $t('scenario_detail.steps_count', { n: nodeProps.data.childrenCount }) }}</div>
                  </div>
                  <Handle type="target" :position="Position.Left" class="vf-handle vf-handle-target" />
                  <Handle type="source" :position="Position.Right" class="vf-handle vf-handle-source" />
                </div>
              </template>
              <!-- 循环容器节点（#node-loop）：圆角边框 + 绿色主题 -->
              <template #node-loop="nodeProps">
                <div
                  class="vf-container-node vf-node-loop"
                  :class="{
                    'is-selected': selectedNodeId === nodeProps.id,
                    'exec-state-queued': nodeProps.data.execState === 'queued',
                    'exec-state-running': nodeProps.data.execState === 'running',
                    'exec-state-passed': nodeProps.data.execState === 'passed',
                    'exec-state-failed': nodeProps.data.execState === 'failed',
                    'exec-state-skipped': nodeProps.data.execState === 'skipped',
                  }"
                >
                  <span class="container-icon">&#8635;</span>
                  <div class="container-body">
                    <div class="container-title mono">{{ nodeProps.data.step_id }}</div>
                    <div class="container-summary" v-if="nodeProps.data.loopSummary">{{ nodeProps.data.loopSummary }}</div>
                    <!-- 循环节点内展示子步骤名称列表，点击跳转步骤编辑 -->
                    <div v-if="nodeProps.data.childStepLabels?.length" class="loop-child-list">
                      <div
                        v-for="(label, ci) in nodeProps.data.childStepLabels"
                        :key="ci"
                        class="loop-child-step"
                        @click.stop="handleLoopChildClick(nodeProps.data.childStepIds?.[ci])"
                      >&#8627; {{ label }}</div>
                    </div>
                  </div>
                  <Handle type="target" :position="Position.Left" class="vf-handle vf-handle-target" />
                  <Handle type="source" :position="Position.Right" class="vf-handle vf-handle-source" />
                </div>
              </template>
              <!-- 开始节点（#node-start）：绿色主题，仅出边，不可拖拽 -->
              <template #node-start="nodeProps">
                <div
                  class="vf-container-node vf-node-start"
                  :class="{ 'is-selected': selectedNodeId === nodeProps.id }"
                >
                  <span class="container-icon">&#9654;</span>
                  <div class="container-body">
                    <div class="container-title mono">START</div>
                    <div class="container-summary" v-if="nodeProps.data.paramCount">{{ $t('scenario_detail.start_param_count', { n: nodeProps.data.paramCount }) }}</div>
                  </div>
                  <Handle type="source" :position="Position.Right" class="vf-handle vf-handle-source" />
                </div>
              </template>
              <!-- 结束节点（#node-end）：红色/灰色主题，仅入边，不可拖拽 -->
              <template #node-end="nodeProps">
                <div
                  class="vf-container-node vf-node-end"
                  :class="{ 'is-selected': selectedNodeId === nodeProps.id }"
                >
                  <span class="container-icon">&#9632;</span>
                  <div class="container-body">
                    <div class="container-title mono">END</div>
                    <div class="container-summary text-3" v-if="nodeProps.data.depCount">{{ $t('scenario_detail.end_dep_count', { n: nodeProps.data.depCount }) }}</div>
                  </div>
                  <Handle type="target" :position="Position.Left" class="vf-handle vf-handle-target" />
                </div>
              </template>
            </VueFlow>
            <!-- DAG 右键菜单 -->
            <div v-if="dagCtx.visible" class="dag-ctx-menu" :style="{ left: dagCtx.x + 'px', top: dagCtx.y + 'px' }" @click.stop>
              <template v-if="dagCtx.node">
                <!-- Start/End 节点：仅展示信息，不可删除 -->
                <template v-if="dagCtx.node.id === 'start'">
                  <div class="dag-ctx-item dag-ctx-readonly">{{ $t('scenario_detail.start_label') }} — {{ (startStep?.start_params || []).length }} {{ $t('scenario_detail.start_param_count') }}</div>
                  <div class="dag-ctx-item dag-ctx-divider" />
                  <div class="dag-ctx-item" @click="dagEditNode">{{ $t('scenario_detail.start_edit_title') }}</div>
                  <div class="dag-ctx-item" @click="dagAddStepAfter">{{ $t('scenario_detail.dag_add_step_after') }}</div>
                  <div class="dag-ctx-item" @click="dagAddConditionAfter">{{ $t('scenario_detail.add_condition') }}</div>
                  <div class="dag-ctx-item" @click="dagAddLoopAfter">{{ $t('scenario_detail.add_loop') }}</div>
                </template>
                <template v-else-if="dagCtx.node.id === 'end'">
                  <div class="dag-ctx-item dag-ctx-readonly">{{ $t('scenario_detail.end_label') }} — {{ (endStep?.depends_on || []).length }} {{ $t('scenario_detail.end_dep_count') }}</div>
                  <div class="dag-ctx-item dag-ctx-divider" />
                  <div class="dag-ctx-item dag-ctx-readonly">{{ $t('scenario_detail.end_readonly_hint') }}</div>
                </template>
                <!-- 普通/容器节点 -->
                <template v-else>
                  <div v-if="dagCtx.node.type === 'condition' || dagCtx.node.type === 'loop'" class="dag-ctx-item" @click="dagEditContainer">
                    {{ $t('scenario_detail.edit_container') }}
                  </div>
                  <div v-else class="dag-ctx-item" @click="dagEditNode">{{ $t('scenario_detail.dag_context_edit') }}</div>
                  <!-- 并行/后续步骤：支持 DAG 多后驱节点 -->
                  <div class="dag-ctx-item" @click="dagAddParallelStep">{{ $t('scenario_detail.dag_add_parallel_step') }}</div>
                  <div class="dag-ctx-item" @click="dagAddStepAfter">{{ $t('scenario_detail.dag_add_step_after') }}</div>
                  <div class="dag-ctx-item dag-ctx-divider" />
                  <!-- 在当前节点后插入容器 -->
                  <div class="dag-ctx-item" @click="dagAddConditionAfter">{{ $t('scenario_detail.dag_add_condition_after') }}</div>
                  <div class="dag-ctx-item" @click="dagAddLoopAfter">{{ $t('scenario_detail.dag_add_loop_after') }}</div>
                  <div class="dag-ctx-item dag-ctx-divider" />
                  <!-- 复制/粘贴步骤 -->
                  <div class="dag-ctx-item" @click="dagCopyStep">{{ $t('scenario_detail.dag_copy_step') }}</div>
                  <div class="dag-ctx-item" @click="dagPasteStep">{{ $t('scenario_detail.dag_paste_step') }}</div>
                  <div class="dag-ctx-item dag-ctx-divider" />
                  <div class="dag-ctx-item" style="color:var(--red)" @click="dagDeleteNode">{{ $t('scenario_detail.dag_context_delete') }}</div>
                  <div class="dag-ctx-item dag-ctx-divider" />
                  <div class="dag-ctx-item" style="color:var(--warning)" @click="dagClearAllEdges">{{ $t('scenario_detail.dag_clear_all_edges') }}</div>
                </template>
              </template>
              <template v-else>
                <div class="dag-ctx-item" @click="dagAddNode">{{ $t('scenario_detail.dag_add_node') }}</div>
                <div class="dag-ctx-item dag-ctx-divider" />
                <div class="dag-ctx-item" @click="addContainer('condition')">{{ $t('scenario_detail.add_condition') }}</div>
                <div class="dag-ctx-item" @click="addContainer('loop')">{{ $t('scenario_detail.add_loop') }}</div>
                <!-- P1-4: 空白右键补粘贴项（此前粘贴必须先右键一个节点，空白处无法粘贴） -->
                <template v-if="stepClipboard">
                  <div class="dag-ctx-item dag-ctx-divider" />
                  <div class="dag-ctx-item" @click="dagPasteStep()">{{ $t('scenario_detail.dag_paste_step') }}</div>
                </template>
              </template>
            </div>
            <div v-if="dagCtx.visible" class="dag-ctx-overlay" @click="dagCtx.visible = false" @contextmenu.prevent="dagCtx.visible = false" />
            <!-- 连线悬浮 tooltip：显示完整的依赖关系说明 -->
            <transition name="edge-tooltip-fade">
              <div v-if="edgeTooltip.visible" class="dag-edge-tooltip" :style="{ left: edgeTooltip.x + 'px', top: edgeTooltip.y + 'px' }">
                <div class="edge-tooltip-header mono">{{ $t('scenario_detail.dag_edge_matched_flow') }}</div>
                <div v-if="edgeTooltip.sourceLabel" class="edge-tooltip-row">
                  <span class="edge-tooltip-label">{{ $t('scenario_detail.dag_edge_from') }}:</span>
                  <span class="mono">{{ edgeTooltip.sourceLabel }}</span>
                </div>
                <div v-if="edgeTooltip.paramFlow?.length" class="edge-tooltip-flows">
                  <div v-for="f in edgeTooltip.paramFlow" :key="f" class="edge-tooltip-flow mono">{{ f }}</div>
                </div>
                <div v-else class="edge-tooltip-row text-3" style="font-size:10px">{{ $t('scenario_detail.dag_edge_no_extract') }}</div>
              </div>
            </transition>
          </div>
        </el-card>

        <!-- 右侧节点配置面板：点击节点后滑出 -->
        <transition name="panel-slide">
          <div v-if="selectedNodeId && selectedStep && selectedStep.type === 'api'" class="dag-node-panel">
            <div class="panel-header">
              <span class="panel-title mono">{{ selectedStep.step_id }}</span>
              <el-button :icon="Close" size="small" circle @click="selectedNodeId = null" />
            </div>
            <div class="panel-body">
              <div class="panel-summary">
                <div class="text-2" style="font-size:11px;margin-bottom:6px">
                  {{ apiNameMap[selectedStep.api_id] || selectedStep.api_id || '—' }}
                </div>
                <div class="text-3" style="font-size:10px;margin-bottom:8px">
                  {{ Object.keys(selectedStep.override_params || {}).filter(k => k !== '_body' && k !== '_body_type').length }} {{ $t('step_editor.params_count') }}
                  &middot; {{ Object.keys(selectedStep.override_headers || {}).length }} {{ $t('step_editor.headers_count') }}
                  &middot; {{ (selectedStep.assertions || []).length }} {{ $t('step_editor.assertions_count') }}
                </div>
                <!-- 快速编辑：超时/重试/等待时间等关键字段，避免频繁打开全屏编辑器 -->
                <div class="panel-field">
                  <span class="form-label">{{ $t('scenario_detail.timeout_label') }}</span>
                  <el-input-number v-model="selectedStep.timeout_s" :min="1" :max="300" size="small" controls-position="right" style="width:100%" />
                </div>
                <div class="panel-row">
                  <div class="panel-field" style="flex:1">
                    <span class="form-label">{{ $t('scenario_detail.retry_label') }}</span>
                    <el-input-number v-model="selectedStep.retry" :min="0" :max="10" size="small" controls-position="right" style="width:100%" />
                  </div>
                  <div class="panel-field" style="flex:1">
                    <span class="form-label">{{ $t('scenario_detail.retry_delay_label') }}</span>
                    <el-input-number v-model="selectedStep.retry_delay_s" :min="0" :max="60" size="small" controls-position="right" style="width:100%" />
                  </div>
                </div>
                <div class="panel-field">
                  <span class="form-label">{{ $t('scenario_detail.wait_ms_label_short') }}</span>
                  <el-input-number v-model="selectedStep.wait_ms" :min="0" :max="60000" :step="100" size="small" controls-position="right" style="width:100%" />
                </div>
                <div class="panel-actions">
                  <el-button size="small" @click="openStepEditor(selectedStep.step_id)">
                    {{ $t('common.edit') }}
                  </el-button>
                </div>
              </div>
            </div>
          </div>
          <!-- Start 节点面板：显示参数列表 -->
          <div v-else-if="selectedNodeId === 'start'" class="dag-node-panel">
            <div class="panel-header">
              <span class="panel-title mono">START</span>
              <el-button :icon="Close" size="small" circle @click="selectedNodeId = null" />
            </div>
            <div class="panel-body">
              <div class="text-3" style="font-size:10px;margin-bottom:4px">{{ $t('scenario_detail.start_param_count', { n: (startStep?.start_params || []).length }) }}</div>
              <div v-if="startStep?.start_params?.length">
                <div v-for="p in startStep.start_params" :key="p.name" class="text-2" style="font-size:11px;padding:2px 0">
                  <span class="mono">{{ p.name }}</span>: {{ p.type }} <span v-if="p.default !== undefined && p.default !== ''" class="text-3">= {{ p.default }}</span>
                </div>
              </div>
              <div v-else class="text-3" style="font-size:11px">{{ $t('scenario_detail.start_no_params') }}</div>
              <div class="panel-actions">
                <el-button size="small" @click="activeTab = 'steps'; openStartEditDialog()">
                  {{ $t('common.edit') }}
                </el-button>
              </div>
            </div>
          </div>
          <!-- End 节点面板：只读显示 -->
          <div v-else-if="selectedNodeId === 'end'" class="dag-node-panel">
            <div class="panel-header">
              <span class="panel-title mono">END</span>
              <el-button :icon="Close" size="small" circle @click="selectedNodeId = null" />
            </div>
            <div class="panel-body">
              <div class="text-3" style="font-size:10px;margin-bottom:4px">{{ $t('scenario_detail.end_dep_count', { n: (endStep?.depends_on || []).length }) }}</div>
              <div v-if="endStep?.depends_on?.length">
                <div v-for="dep in endStep.depends_on" :key="dep" class="mono text-2" style="font-size:11px;padding:2px 0">{{ dep }}</div>
              </div>
              <div v-else class="text-3" style="font-size:11px">{{ $t('scenario_detail.end_readonly_hint') }}</div>
            </div>
          </div>
        </transition>
      </div>

      <!-- 容器编辑弹窗（统一的条件/循环编辑对话框）—— 放在 tab 条件渲染之外，确保任意 tab 下均可弹出 -->
      <el-dialog v-model="containerDialog.visible" :title="containerDialog.type === 'condition' ? $t('scenario_detail.cond_title') : $t('scenario_detail.loop_title')" width="460px" append-to-body>
        <!-- 条件容器配置 -->
        <div v-if="containerDialog.type === 'condition'" style="display:flex;flex-direction:column;gap:12px">
          <div>
            <div class="form-label">{{ $t('scenario_detail.cond_variable') }}</div>
            <el-input v-model="containerDialog.variable" placeholder="response.body.code" size="small" />
          </div>
          <div>
            <div class="form-label">{{ $t('scenario_detail.cond_operator') }}</div>
            <el-select v-model="containerDialog.operator" size="small" style="width:100%">
              <el-option v-for="op in condOperators" :key="op" :value="op" :label="$t('assert.operator_' + op)" />
            </el-select>
          </div>
          <div>
            <div class="form-label">{{ $t('scenario_detail.cond_value') }}</div>
            <el-input v-model="containerDialog.value" placeholder="200" size="small" />
          </div>
          <div>
            <div class="form-label">{{ $t('scenario_detail.cond_on_false') }}</div>
            <el-select v-model="containerDialog.on_false" size="small" style="width:100%">
              <el-option value="skip" :label="$t('scenario_detail.cond_on_false_skip')" />
              <el-option value="fail" :label="$t('scenario_detail.cond_on_false_fail')" />
              <el-option value="continue" :label="$t('scenario_detail.cond_on_false_continue')" />
            </el-select>
          </div>
        </div>
        <!-- 循环容器配置 -->
        <div v-else style="display:flex;flex-direction:column;gap:12px">
          <div class="text-3" style="font-size:11px;margin-bottom:4px">{{ $t('scenario_detail.loop_mutex_hint') }}</div>
          <div>
            <div class="form-label">{{ $t('scenario_detail.loop_var') }}</div>
            <el-input v-model="containerDialog.loop_var" placeholder="user_ids" size="small" @update:model-value="containerDialog.loop_count = null" />
          </div>
          <div>
            <div class="form-label">{{ $t('scenario_detail.loop_count') }}</div>
            <el-input v-model.number="containerDialog.loop_count" type="number" :min="1" placeholder="5" size="small" @update:model-value="containerDialog.loop_var = null" />
          </div>
        </div>
        <template #footer>
          <el-button size="small" @click="containerDialog.visible = false">{{ $t('common.cancel') }}</el-button>
          <el-button size="small" type="primary" @click="containerDialogSave">{{ $t('common.save') }}</el-button>
        </template>
      </el-dialog>

      <!-- 步骤编辑（树形结构：condition/loop 为容器节点，api 为叶节点） -->
      <div v-if="activeTab==='steps'">
        <el-card>
          <template #header>
            <div class="flex items-center justify-between">
              <span class="card-title">{{ $t('scenario_detail.steps_list') }}</span>
              <div class="flex items-center gap-8">
                <el-button size="small" @click="addContainer('condition')">{{ $t('scenario_detail.add_condition') }}</el-button>
                <el-button size="small" @click="addContainer('loop')">{{ $t('scenario_detail.add_loop') }}</el-button>
                <el-button type="primary" size="small" @click="addStep()">{{ $t('scenario_detail.add_step') }}</el-button>
              </div>
            </div>
          </template>

          <div class="variable-graph-card">
            <div class="variable-graph-head">
              <div>
                <div class="card-title">变量引用图</div>
                <div class="text-3" style="font-size:12px;margin-top:2px">
                  展示 Start 参数、Extract、SQL、脚本变量的生产与消费关系，便于排查断链和未使用变量
                </div>
              </div>
              <el-tag size="small" type="info">{{ variableGraphRows.length }} 个变量</el-tag>
            </div>
            <el-table :data="variableGraphRows" size="small" :empty-text="$t('common.no_data')" max-height="260">
              <el-table-column label="变量" min-width="170">
                <template #default="{ row }">
                  <div class="variable-cell">
                    <span class="mono">{{ row.display }}</span>
                    <el-tag size="small" effect="plain" :type="variableKindTag(row.kind)">{{ row.kind_label }}</el-tag>
                  </div>
                  <div class="text-3 mono" style="font-size:11px">{{ row.syntax }}</div>
                </template>
              </el-table-column>
              <el-table-column label="由谁产生" min-width="160">
                <template #default="{ row }">
                  <el-button
                    v-if="row.producer_step_id && row.producer_step_id !== 'context' && row.producer_step_id !== 'env'"
                    link
                    type="primary"
                    size="small"
                    @click="locateStep(row.producer_step_id)"
                  >
                    {{ row.producer_step_id }}
                  </el-button>
                  <span v-else class="text-2">{{ row.producer_name }}</span>
                  <div v-if="row.producer_name && row.producer_name !== row.producer_step_id" class="text-3" style="font-size:11px">
                    {{ row.producer_name }}
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="被谁消费" min-width="260">
                <template #default="{ row }">
                  <div v-if="row.consumers.length" class="variable-consumers">
                    <el-button
                      v-for="consumer in row.consumers"
                      :key="`${row.key}-${consumer.step_id}`"
                      link
                      size="small"
                      type="primary"
                      @click="locateStep(consumer.step_id)"
                    >
                      {{ consumer.step_id }}
                      <span class="text-3 consumer-fields">{{ (consumer.fields || []).join(' / ') }}</span>
                    </el-button>
                  </div>
                  <el-tag v-else size="small" type="warning" effect="plain">未被使用</el-tag>
                </template>
              </el-table-column>
            </el-table>
          </div>

          <!-- Start 步骤卡片：固定在最上方，绿色边框，不可拖拽/删除 -->
          <div v-if="startStep" class="step-card card-start">
            <div class="step-header">
              <span class="container-icon-start">&#9654;</span>
              <span class="step-index mono">S</span>
              <span class="mono" style="font-size:12px;font-weight:600;color:var(--text)">{{ startStep.step_id }}</span>
              <span style="flex:1;font-size:12px;color:var(--text-2)">{{ startStep.name }}</span>
              <span class="text-3" style="font-size:10px">{{ (startStep.start_params || []).length }} {{ $t('scenario_detail.start_param_count', { n: (startStep.start_params || []).length }) }}</span>
              <el-button size="small" @click="openStartEditDialog">{{ $t('common.edit') }}</el-button>
            </div>
          </div>

          <el-empty v-if="!steps.length" :description="$t('scenario_detail.no_steps_hint')" :image-size="48" style="padding:20px" />
          <template v-else>
            <!-- 步骤校验问题汇总提示条：仅在有问题时展示 -->
            <el-alert
              v-if="stepValidation.issueCount > 0"
              :title="$t('scenario_detail.step_validation_summary', { count: stepValidation.issueCount })"
              type="warning"
              :closable="false"
              show-icon
              style="margin-bottom:12px"
            />
            <StepTreeChildren
            :steps="steps"
            :nesting-level="0"
            :api-name-map="apiNameMap"
            :step-exec-states="stepExecStates"
            :step-errors="stepValidation.errors"
            @update:model-value="onStepsReorder"
            @edit-step="openStepEditor"
            @delete-step="deleteStep"
            @edit-container="openContainerDialog"
            @delete-container="deleteContainer"
            @add-step="addStep"
            @add-parallel="addParallelStep"
            @add-container="addChildContainer"
          />
          </template>

          <!-- End 步骤卡片：固定在最下方，灰色边框，不可拖拽/删除 -->
          <div v-if="endStep" class="step-card card-end" style="margin-top:10px">
            <div class="step-header">
              <span class="container-icon-end">&#9632;</span>
              <span class="step-index mono">E</span>
              <span class="mono" style="font-size:12px;font-weight:600;color:var(--text)">{{ endStep.step_id }}</span>
              <span style="flex:1;font-size:12px;color:var(--text-2)">{{ endStep.name }}</span>
              <span class="text-3" style="font-size:10px">{{ (endStep.depends_on || []).length }} {{ $t('scenario_detail.end_dep_count', { n: (endStep.depends_on || []).length }) }}</span>
            </div>
          </div>
        </el-card>

        <!-- Start 步骤参数编辑弹窗 -->
        <el-dialog v-model="startEditDialog.visible" :title="$t('scenario_detail.start_edit_title')" width="520px" append-to-body>
          <div style="display:flex;flex-direction:column;gap:10px">
            <div v-if="!startEditDialog.params.length" class="text-3" style="padding:12px;text-align:center">{{ $t('scenario_detail.start_no_params') }}</div>
            <div v-for="(p, idx) in startEditDialog.params" :key="idx" style="display:flex;gap:8px;align-items:center">
              <el-input v-model="p.name" :placeholder="$t('scenario_detail.start_param_name')" size="small" style="flex:1" />
              <el-select v-model="p.type" size="small" style="width:100px">
                <el-option value="string" label="string" />
                <el-option value="number" label="number" />
                <el-option value="boolean" label="boolean" />
                <el-option value="object" label="object" />
                <el-option value="array" label="array" />
              </el-select>
              <el-input v-model="p.default" :placeholder="$t('scenario_detail.start_param_default')" size="small" style="flex:1" />
              <el-button size="small" type="danger" :icon="Close" circle @click="startEditDialog.params.splice(idx, 1)" />
            </div>
            <el-button size="small" @click="startEditDialog.params.push({ name: '', type: 'string', default: '' })" style="width:100%">
              {{ $t('scenario_detail.start_add_param') }}
            </el-button>
          </div>
          <template #footer>
            <el-button size="small" @click="startEditDialog.visible = false">{{ $t('common.cancel') }}</el-button>
            <el-button size="small" type="primary" @click="startEditDialogSave">{{ $t('common.save') }}</el-button>
          </template>
        </el-dialog>
      </div>

      <!-- 执行历史 -->
      <div v-if="activeTab==='history'">
        <el-card v-loading="statsLoading">
          <template #header>
            <div class="flex items-center justify-between">
              <span class="card-title">{{ $t('scenario_detail.trend_title') }}</span>
              <span class="text-3" style="font-size:11px">
                {{ $t('scenario_detail.stats_summary', { total: scenarioStats.total, pct: scenarioStats.pass_rate_pct }) }}
              </span>
            </div>
          </template>
          <el-empty v-if="!scenarioStats.recent?.length" :description="$t('scenario_detail.no_history')" :image-size="48" style="padding:30px" />
          <VChart v-else :option="historyChartOption" autoresize style="height:200px" />
        </el-card>
        <el-card style="margin-top:12px">
          <template #header>
            <span class="card-title">{{ $t('scenario_detail.recent_execs') }}</span>
          </template>
          <el-table :data="scenarioStats.recent || []" size="small" :empty-text="$t('scenario_detail.no_records')">
            <el-table-column :label="$t('common.result')" width="80">
              <template #default="{ row }">
                <ResultTag :passed="row.passed" />
              </template>
            </el-table-column>
            <el-table-column :label="$t('common.duration')" width="100">
              <template #default="{ row }">
                <span class="mono text-2">{{ fmt.duration(row.duration_ms) }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="$t('scenario_detail.failure_reason')" min-width="200">
              <template #default="{ row }">
                <span v-if="row.failure_reason" class="text-2" style="font-size:11px;color:var(--red)">{{ row.failure_reason }}</span>
                <span v-else class="text-3">—</span>
              </template>
            </el-table-column>
            <el-table-column :label="$t('common.time')" width="160">
              <template #default="{ row }">
                <span class="text-2">{{ fmt.time(row.started_at) }}</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
        <el-card v-loading="versionsLoading" style="margin-top:12px">
          <template #header>
            <div class="flex items-center justify-between">
              <span class="card-title">{{ $t('scenario_detail.version_history') }}</span>
              <el-button size="small" @click="loadScenarioVersions">{{ $t('common.refresh') }}</el-button>
            </div>
          </template>
          <el-empty v-if="!scenarioVersions.length" :description="$t('scenario_detail.no_versions')" :image-size="48" style="padding:24px" />
          <el-table v-else :data="scenarioVersions" size="small">
            <el-table-column :label="$t('scenario_detail.version_col')" width="80">
              <template #default="{ row }">
                <span class="mono">v{{ row.version }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="$t('common.name')" min-width="160">
              <template #default="{ row }">
                <span class="truncate">{{ row.name || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="$t('scenario_detail.steps_count')" width="90">
              <template #default="{ row }">{{ row.steps_count || 0 }}</template>
            </el-table-column>
            <el-table-column :label="$t('common.owner')" width="120">
              <template #default="{ row }">{{ row.actor || '—' }}</template>
            </el-table-column>
            <el-table-column :label="$t('common.time')" width="160">
              <template #default="{ row }">{{ fmt.fromNow(row.created_at) }}</template>
            </el-table-column>
            <el-table-column :label="$t('common.actions')" width="110" fixed="right">
              <template #default="{ row }">
                <el-button size="small" text type="primary" @click="restoreScenarioVersion(row)" :disabled="restoringVersion">
                  {{ $t('scenario_detail.restore_version') }}
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </div>

      <!-- 执行结果 -->
      <el-empty v-if="activeTab==='result' && !lastResult" :description="$t('scenario_detail.not_executed_hint')" :image-size="48" style="padding:40px" />
      <div v-else-if="activeTab==='result'">
        <!-- 执行汇总面板：展示整体统计、断言覆盖率、步骤耗时分布 -->
        <el-card style="margin-bottom:12px">
          <template #header>
            <div class="flex items-center justify-between">
              <span class="card-title">{{ $t('scenario_detail.result_summary_title') }}</span>
              <span class="text-3" style="font-size:11px">{{ fmt.time(lastResult.started_at) }}</span>
            </div>
          </template>
          <div class="result-summary-grid">
            <!-- 整体通过状态 -->
            <div class="summary-item">
              <ResultTag :passed="lastResult.passed" />
              <span class="text-2" style="font-size:12px;margin-left:8px">{{ fmt.duration(lastResult.duration_ms) }}</span>
            </div>
            <!-- 步骤级统计 -->
            <div v-if="resultStats" class="summary-item">
              <div class="text-3" style="font-size:11px;margin-bottom:4px">{{ $t('scenario_detail.result_pass_rate') }}</div>
              <div class="summary-bar">
                <div class="summary-bar-pass" :style="{ width: resultStats.total ? (resultStats.pass / resultStats.total * 100) + '%' : '0%' }"></div>
                <div class="summary-bar-fail" :style="{ width: resultStats.total ? (resultStats.fail / resultStats.total * 100) + '%' : '0%' }"></div>
                <div class="summary-bar-skip" :style="{ width: resultStats.total ? (resultStats.skip / resultStats.total * 100) + '%' : '0%' }"></div>
              </div>
              <div class="text-3" style="font-size:10px;margin-top:4px">
                {{ $t('scenario_detail.result_stats', { pass: resultStats.pass, fail: resultStats.fail, skip: resultStats.skip, total: resultStats.total }) }}
              </div>
            </div>
            <!-- 断言覆盖率 -->
            <div class="summary-item">
              <div class="text-3" style="font-size:11px;margin-bottom:4px">{{ $t('scenario_detail.result_assertion_coverage') }}</div>
              <span class="text-2 mono" style="font-size:16px;font-weight:600">{{ assertionCoverage.total }} / {{ lastResult.steps?.length || 0 }}</span>
              <span class="text-3" style="font-size:10px;margin-left:4px">{{ $t('step_editor.assertions_count') }}</span>
            </div>
            <!-- 步骤耗时分布条 -->
            <div v-if="lastResult.steps?.length" class="summary-item summary-durations">
              <div class="text-3" style="font-size:11px;margin-bottom:4px">{{ $t('scenario_detail.result_duration_breakdown') }}</div>
              <div v-for="step in lastResult.steps" :key="'dur-' + step.step_id" class="duration-row">
                <span class="mono text-3" style="font-size:10px;min-width:60px">{{ step.step_id }}</span>
                <div class="duration-bar-track">
                  <div class="duration-bar-fill" :style="{ width: maxStepLatency ? (step.latency_ms / maxStepLatency * 100) + '%' : '0%', background: step.passed ? 'var(--green, #3ecf8e)' : step.skipped ? 'var(--text-4, #ccc)' : 'var(--red, #f56c6c)' }"></div>
                </div>
                <span class="mono text-3" style="font-size:10px;min-width:36px;text-align:right">{{ step.latency_ms }}ms</span>
              </div>
            </div>
          </div>
        </el-card>

        <!-- 步骤详情 -->
        <el-card>
          <template #header>
            <div class="flex items-center justify-between">
              <span class="card-title">{{ $t('scenario_detail.steps_count', { n: lastResult.steps?.length || 0 }) }}</span>
              <el-button size="small" @click="expandAll = !expandAll">
                {{ expandAll ? $t('scenario_detail.collapse_all') : $t('scenario_detail.expand_all') }}
              </el-button>
            </div>
          </template>
          <div v-for="step in lastResult.steps" :key="step.step_id" class="result-step">
            <div class="result-step-header">
              <span :class="step.skipped ? 'dot dot-gray' : step.passed ? 'dot dot-green' : 'dot dot-red'"></span>
              <span class="mono" style="font-size:12px;font-weight:500">{{ step.step_id }}</span>
              <span class="text-2" style="font-size:11px">{{ step.name }}</span>
              <!-- 重试信息：非首次尝试时显示重试次数 -->
              <el-tag v-if="step.attempt > 1" type="warning" size="small" effect="plain" style="margin-left:6px">
                {{ $t('scenario_detail.result_final_attempt', { total: step.attempt }) }}
              </el-tag>
              <span class="mono text-2" style="font-size:11px;margin-left:auto">{{ step.latency_ms }}ms</span>
              <el-button v-if="!step.passed && !step.skipped" link type="danger" size="small" @click="locateStep(step.step_id)">
                定位步骤
              </el-button>
              <el-tag v-if="step.skipped" type="info">{{ $t('execution_detail.skip') }}</el-tag>
              <ResultTag v-else :passed="step.passed" />
            </div>
            <div v-if="step.error" class="result-error">
              <div>{{ step.error }}</div>
              <!-- ConnectError 等连接失败时给出排查建议 -->
              <div v-if="isConnectError(step.error)" class="connect-hint">
                <strong>{{ $t('scenario_detail.connect_error_hint') }}</strong>
                <ul>
                  <li>{{ $t('scenario_detail.connect_error_check1') }}</li>
                  <li>{{ $t('scenario_detail.connect_error_check2') }}</li>
                  <li>{{ $t('scenario_detail.connect_error_check3') }}</li>
                </ul>
              </div>
            </div>
            <!-- 提取的变量展示：显示步骤间数据传递 -->
            <div v-if="step.extracted_vars && Object.keys(step.extracted_vars).length" class="extracted-vars">
              <span class="text-3" style="font-size:10px;font-weight:500;margin-right:8px">{{ $t('scenario_detail.result_extracted_vars') }}:</span>
              <el-tag v-for="(val, key) in step.extracted_vars" :key="key" size="small" effect="plain" class="extracted-var-tag">
                <span class="mono">{{ key }}</span>
                <span class="text-3" style="margin:0 4px">=</span>
                <span class="mono">{{ truncate(String(val), 30) }}</span>
              </el-tag>
            </div>
            <div v-if="step.script_results?.length" class="extracted-vars">
              <span class="text-3" style="font-size:10px;font-weight:500;margin-right:8px">脚本:</span>
              <el-tag
                v-for="(item, idx) in step.script_results"
                :key="`${step.step_id}-script-${idx}`"
                size="small"
                effect="plain"
                :type="item.ok === false ? 'danger' : item.phase === 'pre' ? 'warning' : 'success'"
                class="extracted-var-tag"
              >
                <span class="mono">{{ item.phase }}.{{ item.var || item.op }}</span>
                <span v-if="item.ok !== false && item.var" class="mono">={{ truncate(String(item.value ?? ''), 24) }}</span>
                <span v-else-if="item.error" class="mono">{{ truncate(String(item.error), 28) }}</span>
              </el-tag>
            </div>
            <!-- 断言对比视图：通过断言行正常显示，失败断言高亮 diff -->
            <div v-if="step.assert_results?.length" class="assert-list">
              <div v-for="ar in step.assert_results" :key="ar.field" class="assert-row" :class="{ 'assert-row-failed': !ar.passed }">
                <span :class="ar.passed ? 'dot dot-green' : 'dot dot-red'"></span>
                <span class="mono text-2" style="font-size:11px;flex-shrink:0">{{ ar.field }} {{ $t('assert.operator_' + ar.operator) }}</span>
                <el-tag v-if="ar.source" size="small" effect="plain">{{ assertSourceLabel(ar) }}</el-tag>
                <template v-if="!ar.passed">
                  <!-- 失败断言：左右对比期望值 vs 实际值 -->
                  <span class="assert-diff-expected mono">{{ $t('scenario_detail.result_expected_label') }}: {{ ar.expected }}</span>
                  <span class="assert-diff-arrow mono red">→</span>
                  <span class="assert-diff-actual mono red">{{ $t('scenario_detail.result_actual_label') }}: {{ ar.actual }}</span>
                  <span v-if="ar.error || ar.sql_error" class="assert-diff-error mono">{{ ar.error || ar.sql_error }}</span>
                </template>
                <template v-else>
                  <!-- 通过断言：简洁显示结果 -->
                  <span class="mono green" style="font-size:11px">{{ ar.expected }}</span>
                </template>
              </div>
            </div>
            <!-- 请求详情：配合 expandAll 控制展开/折叠 -->
            <details v-if="step.request_sent" class="resp-detail" :open="expandAll">
              <summary class="text-3" style="cursor:pointer;font-size:11px;padding:4px 0">{{ $t('scenario_detail.step_request') }}</summary>
              <div style="padding:4px 0 8px;font-size:11px">
                <div class="mono text-2"><span class="text-3">{{ $t('scenario_detail.step_request_url') }}:</span> {{ step.request_sent.method }} {{ step.request_sent.url }}</div>
                <details v-if="step.request_sent.headers && Object.keys(step.request_sent.headers).length" style="margin-top:4px" :open="expandAll">
                  <summary class="text-3" style="cursor:pointer;font-size:10px">{{ $t('scenario_detail.step_request_headers') }}</summary>
                  <pre class="code-block" style="margin-top:4px;max-height:120px;overflow:auto;font-size:10px">{{ jsonPretty(step.request_sent.headers) }}</pre>
                </details>
                <details v-if="step.request_sent.body" style="margin-top:4px" :open="expandAll">
                  <summary class="text-3" style="cursor:pointer;font-size:10px">{{ $t('scenario_detail.step_request_body') }}</summary>
                  <pre class="code-block" style="margin-top:4px;max-height:160px;overflow:auto;font-size:10px">{{ jsonPretty(step.request_sent.body) }}</pre>
                </details>
              </div>
            </details>
            <!-- 响应详情 -->
            <details v-if="step.response_received" class="resp-detail" :open="expandAll">
              <summary class="text-3" style="cursor:pointer;font-size:11px;padding:4px 0">{{ $t('scenario_detail.step_response') }} ({{ step.response_received.status_code }})</summary>
              <div style="padding:4px 0 8px;font-size:11px">
                <details v-if="step.response_received.headers && Object.keys(step.response_received.headers).length" style="margin-top:4px" :open="expandAll">
                  <summary class="text-3" style="cursor:pointer;font-size:10px">{{ $t('scenario_detail.step_response_headers') }}</summary>
                  <pre class="code-block" style="margin-top:4px;max-height:120px;overflow:auto;font-size:10px">{{ jsonPretty(step.response_received.headers) }}</pre>
                </details>
                <details v-if="step.response_received.body" style="margin-top:4px" :open="expandAll">
                  <summary class="text-3" style="cursor:pointer;font-size:10px">{{ $t('scenario_detail.response_body') }}</summary>
                  <pre class="code-block" style="margin-top:4px;max-height:200px;overflow:auto;font-size:10px">{{ jsonPretty(step.response_received.body) }}</pre>
                </details>
              </div>
            </details>
          </div>
        </el-card>
      </div>
    </div>

    <!-- StepEditor 对话框 -->
    <StepEditor
      v-if="editingStepId"
      :visible="editorVisible"
      :step="editingStep || {}"
      :scenario-id="String(route.params.id)"
      :api-list="apiList"
      :available-steps="availableDependencyIds"
      :extract-usage="extractUsage"
      :variable-options="stepVariableOptions"
      @update:visible="editorVisible = $event"
      @confirm="handleStepConfirm"
      @navigate-step="openStepEditor"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted, watch, markRaw, nextTick } from 'vue'
import type { ScenarioStepTree, ScenarioStep, StepType, StepExecState } from '@/types'
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Close } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import { use } from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import VChart from 'vue-echarts'
import { VueFlow, Handle, Position, MarkerType, useVueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import { scenarioApi, apiApi, generationApi, executionApi } from '@/api'
import { useWebSocket } from '@/composables/useWebSocket'
import { useScenarioHistory } from '@/composables/useScenarioHistory'
import { useProjectStore, useToastStore } from '@/stores'
import { fmt, jsonPretty } from '@/utils'
import ResultTag from '@/components/ResultTag.vue'
import StepEditor from '@/components/StepEditor.vue'
import StepTreeChildren from '@/components/StepTreeChildren.vue'

const { t } = useI18n()

const route    = useRoute()
const router   = useRouter()
const toast    = useToastStore()
const projectStore = useProjectStore()
const scenario = ref({})
// 树形步骤列表（前端编辑器内部使用，condition/loop 为容器节点，不含 start/end）
const steps    = ref<ScenarioStepTree[]>([])
// 撤销/重做：基于 steps 快照，最大 30 条历史记录
const { canUndo, canRedo, pushSnapshot, undo, redo } = useScenarioHistory(steps)
// Start/End 特殊步骤：每个场景自动包含，固定不可删除/拖拽，单独管理不混入 steps 数组
const startStep = ref<ScenarioStepTree | null>(null)
const endStep   = ref<ScenarioStepTree | null>(null)
const activeTab = ref('dag')
const saving    = ref(false)
const running   = ref(false)
const lastResult = ref(null)
const expandAll = ref(false)  // 结果 tab 中展开/折叠全部请求响应详情
// 执行状态实时追踪：step_id → 当前执行状态，驱动 DAG 节点和步骤卡片高亮
const stepExecStates = ref<Record<string, StepExecState>>({})
let execWs: any = null       // 当前执行的 WebSocket 连接（用于实时接收步骤状态）
let execWsTimer: any = null  // 执行轮询兜底定时器
const loading   = ref(true)  // 首次加载标记，避免闪白
const pendingGenCount = ref(0)  // 当前项目待审核的 AI 生成场景数
const validating = ref(false)
const serverIssues = ref<any[]>([])
const serverHasErrors = computed(() => serverIssues.value.some(i => i.level === 'error'))

// 未保存变更追踪：保存 load 时的原始快照，与当前状态对比判断是否有未保存的修改
// Vue 3 Proxy 响应式：push/splice/重排等数组操作会触发 steps.value 依赖更新，computed 自动重算
const originalSteps = ref(null)       // 最近一次保存/加载时的步骤快照
const originalScenarioName = ref('')  // 最近一次保存/加载时的场景名称
const hasUnsavedChanges = computed(() => {
  if (!originalSteps.value) return false
  // 将当前树形步骤展平后与原始快照对比
  const cur = JSON.stringify(flattenSteps(steps.value))
  const orig = JSON.stringify(originalSteps.value)
  if (cur !== orig) return true
  if (scenario.value.name !== originalScenarioName.value) return true
  return false
})
const scenarioStats   = ref({ total: 0, passed: 0, pass_rate_pct: 0, recent: [] })
const statsLoading    = ref(false)
const scenarioVersions = ref<any[]>([])
const versionsLoading = ref(false)
const restoringVersion = ref(false)
// StepEditor 弹窗状态
const editorVisible = ref(false)
// 当前编辑的步骤 ID（改为按 ID 查找，适配树形结构）
const editingStepId = ref('')
// 根据 editingStepId 从树中查找步骤，供 StepEditor 使用
const editingStep = computed(() => {
  if (!editingStepId.value) return null
  return findStepById(editingStepId.value)
})

// 变量交叉引用：对于当前编辑步骤的每个 extract 变量，查找哪些步骤引用了它
// 匹配模式 {{step_id.var_name}}，返回 var_name → [引用该变量的 step_id 列表]
const extractUsage = computed(() => {
  const usage: Record<string, string[]> = {}
  const step = editingStep.value
  if (!step || !step.extract || !step.step_id) return usage

  const extractVars = Object.keys(step.extract)
  if (extractVars.length === 0) return usage

  for (const v of extractVars) usage[v] = []

  // 构建引用前缀：{{当前步骤的step_id.
  const refPrefix = `{{${step.step_id}.`
  function scan(list: ScenarioStepTree[]) {
    for (const node of list) {
      // 跳过当前编辑的步骤自身
      if (node.step_id === step!.step_id) {
        if (node.children?.length) scan(node.children)
        continue
      }
      // 拼接该步骤所有可能包含变量引用的文本字段
      const texts = [
        JSON.stringify(node.override_params || {}),
        JSON.stringify(node.override_headers || {}),
        JSON.stringify(node.assertions || []),
      ]
      const combined = texts.join(' ')
      for (const varName of extractVars) {
        if (combined.includes(`${refPrefix}${varName}}}`)) {
          usage[varName].push(node.step_id)
        }
      }
      if (node.children?.length) scan(node.children)
    }
  }
  scan(steps.value)
  return usage
})

// 收集当前所有步骤 ID，供 depends_on 下拉选择器使用
const allStepIds = computed(() => {
  const ids = collectAllDagNodes(steps.value).map(n => n.step_id)
  if (startStep.value) ids.push(startStep.value.step_id)
  if (endStep.value) ids.push(endStep.value.step_id)
  return ids
})

const availableDependencyIds = computed(() => allStepIds.value.filter(id => id !== 'end'))

type VariableConsumer = { step_id: string; fields: string[] }
type VariableGraphRow = {
  key: string
  display: string
  syntax: string
  kind: string
  kind_label: string
  var_name: string
  producer_step_id: string
  producer_name: string
  consumers: VariableConsumer[]
}

function safeJsonParse(raw: any, fallback: any = null) {
  if (!raw || typeof raw !== 'string') return fallback
  try {
    return JSON.parse(raw)
  } catch {
    return fallback
  }
}

function collectTemplateRefs(value: any): string[] {
  const text = typeof value === 'string' ? value : JSON.stringify(value || {})
  const refs: string[] = []
  const re = /\{\{\s*([^}]+?)\s*\}\}/g
  let match: RegExpExecArray | null
  while ((match = re.exec(text))) refs.push(match[1].trim())
  return refs
}

function normalizeVariableRef(ref: string) {
  const clean = String(ref || '').trim()
  if (!clean) return null
  // 与后端变量解析语义保持一致：steps.login.token 与 login.token 都指向 login 步骤产物。
  if (clean.startsWith('steps.')) {
    const parts = clean.split('.')
    if (parts.length >= 3) return { key: `${parts[1]}.${parts.slice(2).join('.')}`, source: parts[1], name: parts.slice(2).join('.') }
  }
  const parts = clean.split('.')
  if (['start', 'env', 'loop', 'response', 'extracted'].includes(parts[0])) {
    return { key: `${parts[0]}.${parts.slice(1).join('.')}`, source: parts[0], name: parts.slice(1).join('.') }
  }
  if (parts[0] === 'sql' && parts.length >= 2) {
    return { key: `sql.${parts[1]}`, source: 'sql', name: parts[1] }
  }
  if (parts.length >= 2) return { key: `${parts[0]}.${parts.slice(1).join('.')}`, source: parts[0], name: parts.slice(1).join('.') }
  return { key: `context.${clean}`, source: 'context', name: clean }
}

function scanStepRefs(step: ScenarioStepTree): Array<{ key: string; field: string }> {
  const fields: Array<{ label: string; value: any }> = [
    { label: 'Params', value: step.override_params },
    { label: 'Headers', value: step.override_headers },
    { label: 'Auth', value: step.auth },
    { label: 'Pre Script', value: step.pre_script },
    { label: 'Post Script', value: step.post_script },
    { label: 'Assertions', value: step.assertions },
    { label: 'Extract', value: step.extract },
    { label: 'Pre SQL', value: step.pre_sql },
    { label: 'Post SQL', value: step.post_sql },
    { label: 'Condition', value: step.condition },
    { label: 'Loop', value: step.loop || step.loop_var },
  ]
  const refs: Array<{ key: string; field: string }> = []
  for (const field of fields) {
    for (const raw of collectTemplateRefs(field.value)) {
      const normalized = normalizeVariableRef(raw)
      if (normalized) refs.push({ key: normalized.key, field: field.label })
    }
  }
  return refs
}

function collectScriptProducedVars(script: string): string[] {
  const parsed = safeJsonParse(script, [])
  if (!Array.isArray(parsed)) return []
  const names = new Set<string>()
  for (const action of parsed) {
    const name = action?.var || action?.target || action?.target_var || action?.name
    if (name) names.add(String(name))
  }
  return Array.from(names)
}

function collectSqlProducedVars(list: any[] | undefined): string[] {
  if (!Array.isArray(list)) return []
  const names = new Set<string>()
  for (const query of list) {
    const name = query?.target_var || query?.name
    if (name) names.add(String(name))
  }
  return Array.from(names)
}

function collectVariableGraphNodes(tree: ScenarioStepTree[]): ScenarioStepTree[] {
  const result: ScenarioStepTree[] = []
  for (const node of tree) {
    // 变量引用图需要覆盖 loop 内部子步骤；不能复用 DAG 布局的收集函数。
    result.push(node)
    if (node.children?.length) result.push(...collectVariableGraphNodes(node.children))
  }
  return result
}

function addVariableRow(rows: Map<string, VariableGraphRow>, row: Omit<VariableGraphRow, 'consumers'>) {
  if (!row.key || rows.has(row.key)) return
  rows.set(row.key, { ...row, consumers: [] })
}

const variableGraphRows = computed<VariableGraphRow[]>(() => {
  const rows = new Map<string, VariableGraphRow>()
  const allNodes = collectVariableGraphNodes(steps.value)

  for (const p of ((startStep.value as any)?.start_params || [])) {
    if (!p?.name) continue
    addVariableRow(rows, {
      key: `start.${p.name}`,
      display: `start.${p.name}`,
      syntax: `{{start.${p.name}}}`,
      kind: 'start',
      kind_label: 'Start',
      var_name: p.name,
      producer_step_id: 'start',
      producer_name: 'Start 参数',
    })
  }

  for (const node of allNodes) {
    if (!node.step_id) continue
    for (const name of Object.keys(node.extract || {})) {
      addVariableRow(rows, {
        key: `${node.step_id}.${name}`,
        display: `${node.step_id}.${name}`,
        syntax: `{{${node.step_id}.${name}}}`,
        kind: 'extract',
        kind_label: 'Extract',
        var_name: name,
        producer_step_id: node.step_id,
        producer_name: node.name || node.step_id,
      })
    }
    for (const name of [...collectScriptProducedVars(node.pre_script), ...collectScriptProducedVars(node.post_script)]) {
      addVariableRow(rows, {
        key: `${node.step_id}.${name}`,
        display: `${node.step_id}.${name}`,
        syntax: `{{${node.step_id}.${name}}}`,
        kind: 'script',
        kind_label: 'Script',
        var_name: name,
        producer_step_id: node.step_id,
        producer_name: node.name || node.step_id,
      })
    }
    for (const name of [...collectSqlProducedVars(node.pre_sql), ...collectSqlProducedVars(node.post_sql)]) {
      addVariableRow(rows, {
        key: `${node.step_id}.${name}`,
        display: `${node.step_id}.${name}`,
        syntax: `{{${node.step_id}.${name}}}`,
        kind: 'sql',
        kind_label: 'SQL',
        var_name: name,
        producer_step_id: node.step_id,
        producer_name: node.name || node.step_id,
      })
      addVariableRow(rows, {
        key: `sql.${name}`,
        display: `sql.${name}`,
        syntax: `{{sql.${name}.scalar}}`,
        kind: 'sql',
        kind_label: 'SQL',
        var_name: name,
        producer_step_id: node.step_id,
        producer_name: node.name || node.step_id,
      })
    }
  }

  addVariableRow(rows, {
    key: 'env.BASE_URL',
    display: 'env.BASE_URL',
    syntax: '{{env.BASE_URL}}',
    kind: 'env',
    kind_label: 'Env',
    var_name: 'BASE_URL',
    producer_step_id: 'env',
    producer_name: '环境变量',
  })

  for (const node of allNodes) {
    for (const ref of scanStepRefs(node)) {
      let rowKey = ref.key
      if (!rows.has(rowKey) && (rowKey.startsWith('context.') || rowKey.startsWith('extracted.'))) {
        const varName = rowKey.split('.').slice(1).join('.')
        const matches = Array.from(rows.values()).filter(row => row.var_name === varName && !['start', 'env'].includes(row.kind))
        // 裸变量只有在唯一匹配时才归因，避免多个步骤都产出 token 时误导用户。
        if (matches.length === 1) rowKey = matches[0].key
      }
      const existing = rows.get(rowKey)
      if (!existing || existing.producer_step_id === node.step_id) continue
      const consumer = existing.consumers.find(item => item.step_id === node.step_id)
      if (consumer) {
        if (!consumer.fields.includes(ref.field)) consumer.fields.push(ref.field)
      } else {
        existing.consumers.push({ step_id: node.step_id, fields: [ref.field] })
      }
    }
  }

  // 扫描各步骤关联的 API 定义中的变量引用（如 api.request.url 中的 {{env.BASE_URL}}），
  // 补充到变量引用图中，避免 env.BASE_URL 等变量显示为"未被使用"。
  for (const node of allNodes) {
    if (!node.api_id) continue
    const api = (apiList.value || []).find((a: any) => a.id === node.api_id)
    if (!api) continue
    const urlFields: string[] = []
    if (api.base_url_override) urlFields.push(api.base_url_override)
    if (api.request?.url) urlFields.push(api.request.url)
    if (api.request?.path && api.request.path !== api.request?.url) urlFields.push(api.request.path)
    for (const field of urlFields) {
      for (const raw of collectTemplateRefs(field)) {
        const normalized = normalizeVariableRef(raw)
        if (!normalized) continue
        const existing = rows.get(normalized.key)
        if (!existing || existing.producer_step_id === node.step_id) continue
        const consumer = existing.consumers.find(item => item.step_id === node.step_id)
        if (consumer) {
          if (!consumer.fields.includes('API URL')) consumer.fields.push('API URL')
        } else {
          existing.consumers.push({ step_id: node.step_id, fields: ['API URL'] })
        }
      }
    }
  }

  return Array.from(rows.values()).sort((a, b) => {
    if (a.consumers.length !== b.consumers.length) return a.consumers.length ? 1 : -1
    return a.key.localeCompare(b.key)
  })
})

function variableKindTag(kind: string) {
  const map: Record<string, string> = {
    start: 'success',
    extract: 'primary',
    script: 'warning',
    sql: 'info',
    env: 'success',
  }
  return map[kind] || 'info'
}

// StepEditor 变量插入器候选：统一展示 start 入参、上游 extract、环境变量和循环变量引用语法。
const stepVariableOptions = computed(() => {
  const options: Array<{ label: string; value: string }> = []
  const seen = new Set<string>()
  const currentId = editingStepId.value
  const currentStep = editingStep.value

  function add(label: string, value: string) {
    if (!value || seen.has(value)) return
    seen.add(value)
    options.push({ label, value })
  }

  for (const p of ((startStep.value as any)?.start_params || [])) {
    if (p?.name) add(`start.${p.name}`, `{{start.${p.name}}}`)
  }

  for (const node of collectAllDagNodes(steps.value)) {
    // 仅把其他步骤已提取变量作为候选，避免当前步骤引用自身未产生的数据。
    if (!node.step_id || node.step_id === currentId) continue
    for (const varName of Object.keys(node.extract || {})) {
      add(`${node.step_id}.${varName}`, `{{${node.step_id}.${varName}}}`)
    }
  }

  add('env.BASE_URL', '{{env.BASE_URL}}')
  // 当前节点位于 loop 容器内时，提供循环上下文变量候选。
  if ((currentStep as any)?.parent_id || currentStep?.type === 'loop') {
    add('loop.item', '{{loop.item}}')
    add('loop.index', '{{loop.index}}')
  }
  return options
})

// 步骤内联校验：收集空 api_id 和重复 step_id，驱动步骤卡片警告指示器
const stepValidation = computed(() => {
  const errors: Record<string, string[]> = {}
  const emptyApiIds = new Set<string>()
  const duplicateStepIds = new Set<string>()
  const idCount = new Map<string, number>()

  // 递归遍历所有步骤（包括容器内的子步骤），收集校验问题
  function walk(list: ScenarioStepTree[]) {
    for (const node of list) {
      idCount.set(node.step_id, (idCount.get(node.step_id) || 0) + 1)
      // API 步骤检查 api_id 是否为空
      if (node.type === 'api' && !node.api_id) {
        emptyApiIds.add(node.step_id)
      }
      if (node.children?.length) walk(node.children)
    }
  }
  walk(steps.value)

  // 找出重复的 step_id（出现次数 > 1）
  for (const [id, count] of idCount) {
    if (count > 1) duplicateStepIds.add(id)
  }

  // 构建 step_id → 错误类型列表的映射，供 StepTreeChildren 警告指示器使用
  for (const id of emptyApiIds) {
    if (!errors[id]) errors[id] = []
    errors[id].push('empty_api')
  }
  for (const id of duplicateStepIds) {
    if (!errors[id]) errors[id] = []
    errors[id].push('duplicate_id')
  }

  return { errors, emptyApiIds, duplicateStepIds, issueCount: Object.keys(errors).length }
})

use([BarChart, GridComponent, TooltipComponent, CanvasRenderer])
const apiList = ref([])  // 项目内所有 API（用于步骤中显示 API 名称）
// api_id → API 名称映射，步骤编辑时在 api_id 输入框旁显示对应 API 名称
const apiNameMap = computed(() => Object.fromEntries(apiList.value.map(a => [a.id, a.name || a.request?.path || a.id])))

// ── VueFlow DAG ────────────────────────────────────────────
// 注册自定义节点类型（使用模板 slot 渲染）
const nodeTypes = { step: markRaw({}), 'condition': markRaw({}), 'loop': markRaw({}), 'start': markRaw({}), 'end': markRaw({}) }
const { setCenter } = useVueFlow()

const NODE_W = 180, NODE_H = 52, COL_GAP = 60, ROW_GAP = 24
// Kahn 拓扑布局：仅在节点未手动拖拽过（pos_x=0 && pos_y=0）时计算位置
// 返回 { pos, groups }，其中 groups 为拓扑层级的节点分组（同一层级 = 可并行执行）
// 注：忽略 'start'/'end' 依赖（start/end 为特殊节点，不在 DAG 图中参与拓扑排序）
// P1-5: 自动排列所有节点 —— 重置 pos_x/pos_y 为 0，触发 kahnLayout 重新计算位置
// 此前 kahnLayout 仅在 pos=0 时生效，拖拽过后新增节点会叠在角落，此函数提供手动重排入口
function autoLayoutAll() {
  function reset(list: ScenarioStepTree[]) {
    for (const node of list) {
      if (node.type === 'api') {
        node.pos_x = 0
        node.pos_y = 0
      }
      if (node.children?.length) reset(node.children)
    }
  }
  reset(steps.value)
  pushSnapshot()
  toast.success(t('scenario_detail.auto_layout_done', '已自动排列'))
}

function kahnLayout(ss) {
  const map = Object.fromEntries(ss.map(s => [s.step_id, s]))
  // 入度计算：排除 'start' 和 'end'（它们是隐式入口/出口节点，不参与拓扑排序）
  const indeg = Object.fromEntries(ss.map(s => [s.step_id, (s.depends_on || []).filter(d => d !== 'start' && d !== 'end').length]))
  const children = Object.fromEntries(ss.map(s => [s.step_id, []]))
  // 构建子节点映射：同样跳过 start/end
  ss.forEach(s => (s.depends_on || []).forEach(d => {
    if (d !== 'start' && d !== 'end') children[d]?.push(s.step_id)
  }))

  const groups = []
  // 起始节点：入度为 0 的节点（无依赖，或仅依赖 start），作为拓扑排序的第一层
  let ready = ss.filter(s => !indeg[s.step_id]).map(s => s.step_id)
  while (ready.length) {
    groups.push(ready.map(id => map[id]))
    const next = []
    // 保护：依赖引用了不存在的 step_id 时 children[id] 可能为 undefined
    ready.forEach(id => (children[id] || []).forEach(c => { indeg[c]--; if (!indeg[c]) next.push(c) }))
    ready = next
  }

  const pos = {}
  // x 偏移为 Start 节点预留空间，避免与第一层节点重叠
  const START_RESERVED_X = CONTAINER_W + COL_GAP
  groups.forEach((grp, ci) => {
    grp.forEach((s, ri) => {
      pos[s.step_id] = { x: ci * (NODE_W + COL_GAP) + START_RESERVED_X, y: ri * (NODE_H + ROW_GAP) + 20 }
    })
  })
  return pos
}
const waveColors = ['rgba(79,142,247,.18)', 'rgba(62,207,142,.15)', 'rgba(160,112,240,.15)', 'rgba(240,160,64,.15)']
const waveStrokes = ['rgba(79,142,247,.6)', 'rgba(62,207,142,.6)', 'rgba(160,112,240,.6)', 'rgba(240,160,64,.6)']

// DAG 节点尺寸常量
const CONTAINER_W = 200, CONTAINER_H = 70

// 收集所有需要在 DAG 中显示的节点（API + 容器），用于拓扑布局
// 循环容器(loop)的子步骤不展平到 DAG，显示在循环节点内部；条件容器(condition)的子步骤正常展平为分支节点
function collectAllDagNodes(tree: ScenarioStepTree[]): ScenarioStepTree[] {
  const result: ScenarioStepTree[] = []
  for (const node of tree) {
    if (node.type === 'api' || node.type === 'condition' || node.type === 'loop') {
      result.push(node)
    }
    // 循环容器的子步骤不展平到 DAG（保留在循环节点内部显示）
    if (node.children?.length && node.type !== 'loop') {
      result.push(...collectAllDagNodes(node.children))
    }
  }
  return result
}

// 连线删除确认状态：两步点击防止误操作
// 必须在 vfElements 之前声明，避免 TDZ 错误（vfElements computed 引用了此 ref）
const pendingEdgeDelete = ref<string | null>(null)

// 将树形步骤转换为 VueFlow 兼容的 nodes + edges
// 容器节点使用 #node-condition / #node-loop 模板，API 步骤使用 #node-step 模板
const vfElements = computed(() => {
  const tree = steps.value
  if (!tree.length) return []
  // 引用 pendingEdgeDelete 以触发 Vue 响应式更新（连线删除确认高亮）
  const selectedEdgeId = pendingEdgeDelete.value

  // 收集所有节点（API + 容器）用于统一的 Kahn 拓扑布局，避免节点重叠
  const allDagNodes = collectAllDagNodes(tree)
  const autoPos = kahnLayout(allDagNodes)

  const nodes: any[] = []
  const edges: any[] = []
  // 递归处理树节点
  let waveIdx = 0
  function addNodes(nodeList: ScenarioStepTree[], parentContainer?: ScenarioStepTree) {
    for (const node of nodeList) {
      if (node.type === 'condition' || node.type === 'loop') {
        const isLoop = node.type === 'loop'
        // 容器节点使用 Kahn 布局（分配独立位置）
        const ap = autoPos[node.step_id] || { x: 0, y: waveIdx * (NODE_H + ROW_GAP) }
        const condSummary = node.condition?.variable
          ? `${node.condition.variable} ${node.condition.operator} ${node.condition.value} → ${node.condition.on_false}`
          : ''
        const loopSummary = node.loop_var
          ? t('scenario_detail.container_loop_var_summary', { var: node.loop_var })
          : node.loop_count
            ? t('scenario_detail.container_loop_count_summary', { count: node.loop_count })
            : ''
        // 获取子步骤摘要（仅循环容器需要在内部显示子步骤列表）
        const childStepLabels = (node.children || []).map(c => c.name || c.step_id)
        const childStepIds = (node.children || []).map(c => c.step_id)  // 循环容器子步骤 ID，用于点击跳转编辑
        // 循环容器高度随子步骤数量增加，确保内部内容可见
        const containerH = isLoop && childStepLabels.length ? Math.max(CONTAINER_H, 30 + childStepLabels.length * 24) : CONTAINER_H
        nodes.push({
          id: node.step_id,
          type: node.type === 'condition' ? 'condition' : 'loop',
          position: { x: ap.x, y: ap.y },
          data: {
            step_id: node.step_id,
            label: node.name || (node.type === 'condition' ? '条件' : '循环'),
            condSummary,
            loopSummary,
            childrenCount: (node.children || []).length,
            childStepLabels,  // 循环容器内子步骤名称列表
            childStepIds,    // 循环容器内子步骤 ID 列表，用于点击跳转步骤编辑器
            // 容器节点也展示其内部子步骤的综合执行状态（取最严重的）
            execState: stepExecStates.value[node.step_id] || 'idle',
          },
          style: { width: `${CONTAINER_W}px`, height: `${containerH}px` },
        })
        // 条件容器：子步骤作为分支节点展平到 DAG；循环容器：子步骤在循环节点模板内部显示。
        if (!isLoop && node.children?.length) {
          addNodes(node.children, node)
        }
      } else {
        // API 步骤节点
        const hasManualPos = (node.pos_x !== undefined && node.pos_x !== 0) || (node.pos_y !== undefined && node.pos_y !== 0)
        const ap = autoPos[node.step_id] || { x: 0, y: waveIdx * (NODE_H + ROW_GAP) }
        const x = hasManualPos ? (node.pos_x || 0) : ap.x
        const y = hasManualPos ? (node.pos_y || 0) : ap.y
        const wave = waveIdx % waveColors.length
        waveIdx++

        const apiName = apiNameMap.value[node.api_id] || ''
        const tooltip = apiName ? `${node.step_id}: ${apiName}` : node.step_id

        nodes.push({
          id: node.step_id,
          type: 'step',
          position: { x, y },
          data: {
            step_id: node.step_id,
            label: node.name || apiName || node.api_id || node.step_id,
            api_id: node.api_id,
            tooltip,
            bg: waveColors[wave],
            stroke: waveStrokes[wave],
            // 容器相关信息（用于样式标识）
            inContainer: !!parentContainer,
            containerId: parentContainer?.step_id || null,
            // 实时执行状态：驱动 DAG 节点样式动态变化
            execState: stepExecStates.value[node.step_id] || 'idle',
          },
          style: { width: `${NODE_W}px`, height: `${NODE_H}px` },
        })

      }
    }
  }

  function addEdges(nodeList: ScenarioStepTree[]) {
    for (const node of nodeList) {
      if (node.type === 'api' || node.type === 'condition' || node.type === 'loop') {
        (node.depends_on || []).forEach(dep => {
          if (dep === 'start' || dep === 'end') return
          if (!autoPos[dep]) return
          const depStep = allDagNodes.find(x => x.step_id === dep)
          const paramFlow: string[] = []
          // 收集来源步骤的所有提取变量名，用于 tooltip 展示完整依赖信息
          const sourceExtractKeys: string[] = depStep?.extract ? Object.keys(depStep.extract) : []
          if (depStep?.extract && node.override_params) {
            const extractKeys = Object.keys(depStep.extract)
            const overrideKeys = Object.keys(node.override_params)
            overrideKeys.forEach(ok => {
              const val = node.override_params[ok]
              if (typeof val === 'string') {
                extractKeys.forEach(ek => {
                  if (val.includes(`${dep}.${ek}`) || val.includes(`{{${dep}.${ek}}}`)) {
                    paramFlow.push(`${ek} → ${ok}`)
                  }
                })
              }
            })
          }
          // 来源步骤的显示标签
          const sourceLabel = depStep ? `${depStep.step_id}${depStep.name ? ': ' + depStep.name : ''}` : dep
          const edgeLabel = paramFlow.length ? paramFlow.join(', ') : ''
          const depEdgeId = `${dep}->${node.step_id}`
          edges.push({
            id: depEdgeId,
            source: dep,
            target: node.step_id,
            type: 'smoothstep',
            animated: false,
            markerEnd: MarkerType.ArrowClosed,
            label: edgeLabel,
            // 连线删除确认高亮
            class: selectedEdgeId === depEdgeId ? 'vf-edge-selected' : undefined,
            // 连线 data 存储完整依赖信息，供 hover tooltip 展示
            data: {
              depInfo: {
                sourceId: dep,
                targetId: node.step_id,
                sourceLabel,
                sourceExtractVars: sourceExtractKeys,
                targetOverrideKeys: Object.keys(node.override_params || {}).filter(k => k !== '_body' && k !== '_body_type'),
                paramFlow: paramFlow.length ? paramFlow : [],
              },
            },
            style: { stroke: 'var(--border-2)', strokeWidth: 1.5 },
            labelStyle: { fill: 'var(--text-3)', fontSize: 10, fontWeight: 400 },
            labelBgStyle: { fill: 'var(--bg-2)', fillOpacity: 0.9 },
            labelBgPadding: [4, 3],
            labelBgBorderRadius: 3,
          })
        })
      }
      // 递归处理子步骤的连线
      if (node.children?.length) {
        addEdges(node.children)
      }
    }
  }

  // 先添加所有节点
  addNodes(tree)
  // 添加 depends_on 连线
  addEdges(tree)

  // 计算所有节点的最大 x 坐标，用于定位 End 节点
  const maxX = nodes.length ? Math.max(...nodes.map(n => n.position.x)) : 0
  const midY = nodes.length ? nodes.reduce((s, n) => s + n.position.y, 0) / nodes.length : 0

  // 添加 Start 节点：固定在最左侧，仅出边
  if (startStep.value) {
    nodes.push({
      id: 'start',
      type: 'start',
      position: { x: 0, y: midY },
      data: {
        step_id: 'start',
        paramCount: (startStep.value.start_params || []).length,
      },
      style: { width: `${CONTAINER_W}px`, height: `${CONTAINER_H}px` },
      draggable: false,
    })
    // Start → 所有显式依赖 start 的节点连线；节点允许同时存在多个出边，天然支持并行流程。
    for (const s of allDagNodes) {
      if ((s.depends_on || []).includes('start')) {
        edges.push({
          id: `start->${s.step_id}`,
          source: 'start',
          target: s.step_id,
          type: 'smoothstep',
          animated: false,
          markerEnd: MarkerType.ArrowClosed,
          style: { stroke: 'var(--green, #3ecf8e)', strokeWidth: 1.5 },
        })
      }
    }
  }

  // 添加 End 节点：固定在最右侧，仅入边，不可拖拽
  if (endStep.value) {
    nodes.push({
      id: 'end',
      type: 'end',
      position: { x: maxX + NODE_W + COL_GAP, y: midY },
      data: {
        step_id: 'end',
        depCount: (endStep.value.depends_on || []).length,
      },
      style: { width: `${CONTAINER_W}px`, height: `${CONTAINER_H}px` },
      draggable: false,
    })
    // 叶节点 → End 连线（灰色虚线）
    for (const leafId of (endStep.value.depends_on || [])) {
      edges.push({
        id: `${leafId}->end`,
        source: leafId,
        target: 'end',
        type: 'smoothstep',
        animated: false,
        markerEnd: MarkerType.ArrowClosed,
        style: { stroke: 'var(--text-3, #999)', strokeWidth: 1, strokeDasharray: '5,3' },
      })
    }
  }

  return [...nodes, ...edges]
})

// ── VueFlow 事件处理 ──────────────────────────────────────
const dagCtx = reactive({ visible: false, x: 0, y: 0, node: null })

// DAG 步骤剪贴板：用于复制/粘贴步骤节点
const stepClipboard = ref<ScenarioStepTree | null>(null)
const selectedNodeId = ref(null)  // 当前选中的节点 ID，用于右侧配置面板

// 连线悬浮 tooltip 状态：鼠标悬停在连线上时显示完整依赖关系
const edgeTooltip = reactive({ visible: false, x: 0, y: 0, sourceLabel: '', paramFlow: [] as string[] })

// 根据选中节点 ID 计算对应的 step 引用（递归查找树）
const selectedStep = computed(() => {
  if (!selectedNodeId.value) return null
  return findStepById(selectedNodeId.value)
})

// 执行结果步骤级通过/失败/跳过统计
const resultStats = computed(() => {
  const steps = lastResult.value?.steps
  if (!steps?.length) return null
  let pass = 0, fail = 0, skip = 0
  steps.forEach(s => {
    if (s.skipped) skip++
    else if (s.passed) pass++
    else fail++
  })
  return { pass, fail, skip, total: steps.length }
})

// 断言覆盖率：有断言的步骤数 / 总步骤数
const assertionCoverage = computed(() => {
  const steps = lastResult.value?.steps
  if (!steps?.length) return { total: 0, withAssertions: 0 }
  const withAssertions = steps.filter(s => s.assert_results?.length).length
  return { total: withAssertions, withAssertions }
})

// 最大步骤耗时：用于耗时分布条的比例计算
const maxStepLatency = computed(() => {
  const steps = lastResult.value?.steps
  if (!steps?.length) return 0
  return Math.max(...steps.map(s => s.latency_ms || 0), 1)
})

// 点击节点：选中并显示右侧配置面板
function vfNodeClick(event) {
  selectedNodeId.value = event.node.id
  dagCtx.visible = false
  clearPendingEdgeDelete()
  // 点击条件/循环容器节点时，直接打开编辑弹窗
  if (event.node.type === 'condition' || event.node.type === 'loop') {
    const container = findStepById(event.node.id)
    if (container) openContainerDialog(container)
  } else if (event.node.type !== 'start' && event.node.type !== 'end') {
    // 点击 API 步骤节点时，切换到步骤编辑 Tab
    activeTab.value = 'steps'
  }
}

// 循环容器子步骤点击：根据步骤类型打开对应编辑弹窗（API→StepEditor，容器→ContainerDialog）
function handleLoopChildClick(stepId?: string) {
  if (!stepId) return
  const step = findStepById(stepId)
  if (!step) return
  if (step.type === 'condition' || step.type === 'loop') {
    openContainerDialog(step)
  } else {
    openStepEditor(stepId)
  }
}

// 拖拽结束：同步 pos_x/pos_y 到 step（递归查找树）
function vfNodeDragStop(event) {
  const step = findStepById(event.node.id)
  if (step) {
    step.pos_x = Math.round(event.node.position.x)
    step.pos_y = Math.round(event.node.position.y)
    pushSnapshot()
  }
}

// 点击画布空白：取消选中节点，关闭菜单和连线 tooltip
function vfPaneClick() {
  dagCtx.visible = false
  selectedNodeId.value = null
  edgeTooltip.visible = false
  clearPendingEdgeDelete()
}

// 点击连线：首次选中高亮，再次点击确认删除依赖关系
function vfEdgeClick(event) {
  const edgeId = event.edge.id
  // 第一次点击：标记为待删除，高亮显示
  if (pendingEdgeDelete.value !== edgeId) {
    pendingEdgeDelete.value = edgeId
    // P1-6: 提示用户需再次点击确认（此前静默高亮，用户不知道下一步操作）
    toast.info(t('scenario_detail.connect_delete_hint'))
    // 3 秒后自动取消选中态
    setTimeout(() => {
      if (pendingEdgeDelete.value === edgeId) pendingEdgeDelete.value = null
    }, 3000)
    return
  }
  // 第二次点击同一连线：确认删除
  pendingEdgeDelete.value = null
  const [from, to] = edgeId.split('->')
  if (to === 'end' && endStep.value) {
    endStep.value.depends_on = (endStep.value.depends_on || []).filter(d => d !== from)
    pushSnapshot()
    return
  }
  const target = findStepById(to)
  if (target && target.depends_on) {
    target.depends_on = target.depends_on.filter(d => d !== from)
    if (target.depends_on.length === 0) {
      // 删除最后一条入边后保留孤立状态，方便用户重新拖线，避免立即自动补 start 边。
      target.depends_on = []
    }
    refreshEndDepends()
    pushSnapshot()
  }
}

// 清除连线选中态（点击空白区域或节点时调用）
function clearPendingEdgeDelete() {
  pendingEdgeDelete.value = null
}

// 鼠标进入连线：显示依赖关系 tooltip（非容器内部连线）
function vfEdgeMouseEnter(event) {
  const depInfo = event.edge.data?.depInfo
  if (!depInfo) return
  edgeTooltip.sourceLabel = depInfo.sourceLabel || ''
  edgeTooltip.paramFlow = depInfo.paramFlow || []
  edgeTooltip.x = event.event.clientX + 14
  edgeTooltip.y = event.event.clientY - 10
  edgeTooltip.visible = true
}

// 鼠标离开连线：隐藏 tooltip
function vfEdgeMouseLeave() {
  edgeTooltip.visible = false
}

// 拖拽连接：从 source 节点连接到 target 节点，添加依赖关系
// P0-5: 连线实时环检测 —— 在 vfConnect 拒绝会形成环的连线
// 检测逻辑：若 target 已能（直接或间接）到达 source，则添加 target→source 依赖会形成环
function wouldCreateCycle(sourceId, targetId, steps) {
  // 构建邻接表（step_id → depends_on 列表）
  const adj = {}
  for (const s of steps) {
    if (s.type === 'api' || s.type === 'condition' || s.type === 'loop') {
      adj[s.step_id] = [...(s.depends_on || [])].filter(d => d !== 'start' && d !== 'end')
    }
  }
  // 模拟添加新依赖：target 依赖 source
  if (!adj[targetId]) adj[targetId] = []
  if (!adj[targetId].includes(sourceId)) adj[targetId].push(sourceId)
  // DFS 检测环（从 target 出发能否回到 target）
  const inStack = new Set()
  function dfs(id) {
    if (inStack.has(id)) return true
    inStack.add(id)
    for (const dep of (adj[id] || [])) {
      if (adj[dep] !== undefined && dfs(dep)) return true
    }
    inStack.delete(id)
    return false
  }
  return dfs(targetId)
}

function vfConnect(connection) {
  const sourceId = connection.source
  const targetId = connection.target
  if (sourceId === targetId) return
  if (sourceId === 'end' || targetId === 'start') return
  if (targetId === 'end') {
    if (!endStep.value || sourceId === 'start') return
    if (!(endStep.value.depends_on || []).includes(sourceId)) {
      endStep.value.depends_on = [...(endStep.value.depends_on || []), sourceId]
      pushSnapshot()
    }
    return
  }
  const targetStep = findStepById(targetId)
  if (!targetStep) return
  if (targetStep.type === 'start' || targetStep.type === 'end') return
  if (!targetStep.depends_on) targetStep.depends_on = []
  if (targetStep.depends_on.includes(sourceId)) return
  // P0-5: 连线前环检测，拒绝会形成循环依赖的连线（此前只能在保存时报错）
  if (wouldCreateCycle(sourceId, targetId, flattenSteps())) {
    toast.error(t('scenario_detail.connect_cycle_rejected', '该连线会形成循环依赖，已拒绝'))
    return
  }
  targetStep.depends_on.push(sourceId)
  refreshEndDepends()
  // 记录快照以支持撤销
  pushSnapshot()
}

// 右键画布空白：显示上下文菜单（新增节点）
// P1-4: 菜单边界检测 —— 靠近右/下边缘时反转偏移，避免菜单溢出视口
function clampMenuPos(clientX: number, clientY: number): { x: number, y: number } {
  const MENU_W = 260, MENU_H = 400
  const vw = window.innerWidth, vh = window.innerHeight
  // 默认菜单显示在点击点左上方（-W/-偏移），靠近边缘时改为右下方
  let x = clientX - MENU_W
  let y = clientY - 60
  if (x < 8) x = clientX + 8  // 靠近左边缘 → 显示在右侧
  if (y + MENU_H > vh - 8) y = Math.max(8, clientY - MENU_H - 8)  // 靠近底部 → 上移
  if (y < 8) y = 8
  return { x, y }
}

function vfPaneContextMenu(event) {
  dagCtx.visible = false  // 先关闭节点菜单
  dagCtx.visible = true
  const pos = clampMenuPos(event.event.clientX, event.event.clientY)
  dagCtx.x = pos.x
  dagCtx.y = pos.y
  dagCtx.node = null  // 标记为画布右键（非节点右键）
}

// 右键画布新增节点
function dagAddNode() {
  dagCtx.visible = false
  addStep()
}

// 右键节点：弹出上下文菜单
function vfNodeContextMenu(event) {
  dagCtx.visible = true
  // P1-4: 用边界检测替代硬编码偏移，避免靠近边缘时菜单溢出
  const pos = clampMenuPos(event.event.clientX, event.event.clientY)
  dagCtx.x = pos.x
  dagCtx.y = pos.y
  dagCtx.node = event.node
}

// ── 容器编辑对话框状态 ─────────────────────────────────
const containerDialog = reactive({
  visible: false,
  type: 'condition' as StepType,
  step_id: '',
  // 条件容器字段
  variable: '',
  operator: 'eq',
  value: '',
  on_false: 'skip' as 'skip' | 'fail' | 'continue',
  // 循环容器字段
  loop_var: null as string | null,
  loop_count: null as number | null,
})

// 条件比较操作符列表
const condOperators = ['eq', 'ne', 'gt', 'lt', 'gte', 'lte', 'contains', 'exists']

// Start 步骤参数编辑对话框
const startEditDialog = reactive({
  visible: false,
  params: [] as { name: string; type: string; default: any }[],
})

function openStartEditDialog() {
  if (!startStep.value) return
  // 深拷贝当前 start_params 到编辑缓冲区，避免直接修改影响撤销
  startEditDialog.params = JSON.parse(JSON.stringify(startStep.value.start_params || []))
  startEditDialog.visible = true
}

function startEditDialogSave() {
  if (!startStep.value) return
  // 过滤空名称的参数
  startStep.value.start_params = startEditDialog.params.filter(p => p.name.trim())
  startEditDialog.visible = false
  // 记录快照以支持撤销
  pushSnapshot()
}

// 打开容器编辑对话框
// 使用 nextTick 设置 visible：避免因 click.stop 事件冒泡时序导致 dialog 未能正确显示
async function openContainerDialog(container: ScenarioStepTree) {
  containerDialog.step_id = container.step_id
  containerDialog.type = container.type
  if (container.type === 'condition') {
    containerDialog.variable = container.condition?.variable || ''
    containerDialog.operator = container.condition?.operator || 'eq'
    containerDialog.value = container.condition?.value ?? ''
    containerDialog.on_false = container.condition?.on_false || 'skip'
  } else {
    containerDialog.loop_var = container.loop?.list_ref || container.loop_var || null
    containerDialog.loop_count = container.loop?.count || container.loop_count || null
  }
  // 延迟设置 visible，确保 DOM 状态同步后 dialog 正确渲染
  await nextTick()
  containerDialog.visible = true
}

// 保存容器编辑
function containerDialogSave() {
  const container = findStepById(containerDialog.step_id)
  if (!container) return
  if (containerDialog.type === 'condition') {
    if (containerDialog.variable.trim()) {
      container.condition = {
        variable: containerDialog.variable.trim(),
        operator: containerDialog.operator,
        value: containerDialog.value,
        on_false: containerDialog.on_false,
      }
    } else {
      container.condition = null
    }
  } else {
    container.loop_var = containerDialog.loop_var || null
    container.loop_count = containerDialog.loop_count || null
    container.loop = containerDialog.loop_var
      ? { mode: 'list', list_ref: containerDialog.loop_var, item_alias: 'item' }
      : { mode: 'count', count: containerDialog.loop_count || 1, list_ref: '', item_alias: 'item' }
  }
  containerDialog.visible = false
  // 记录快照以支持撤销
  pushSnapshot()
}

// 递归查找节点
function findStepById(id: string, list?: ScenarioStepTree[]): ScenarioStepTree | null {
  const searchList = list || steps.value
  for (const node of searchList) {
    if (node.step_id === id) return node
    if (node.children?.length) {
      const found = findStepById(id, node.children)
      if (found) return found
    }
  }
  return null
}

function removeDependencyRefs(removedIds: Set<string>) {
  function walk(list: ScenarioStepTree[]) {
    for (const node of list) {
      node.depends_on = (node.depends_on || []).filter(d => !removedIds.has(d))
      if (node.children?.length) walk(node.children)
    }
  }
  walk(steps.value)
  if (endStep.value) {
    endStep.value.depends_on = (endStep.value.depends_on || []).filter(d => !removedIds.has(d))
  }
}

function replaceDependencyRefs(oldId: string, newId: string) {
  if (!oldId || !newId || oldId === newId) return
  function walk(list: ScenarioStepTree[]) {
    for (const node of list) {
      node.depends_on = (node.depends_on || []).map(d => d === oldId ? newId : d)
      if (node.parent_id === oldId) node.parent_id = newId
      if (node.children?.length) walk(node.children)
    }
  }
  walk(steps.value)
  if (endStep.value) {
    endStep.value.depends_on = (endStep.value.depends_on || []).map(d => d === oldId ? newId : d)
  }
}

// 添加容器（顶层）
function addContainer(type: 'condition' | 'loop') {
  const container = createContainerStep(type)
  container.nesting_level = 0
  const prevNode = [...collectAllDagNodes(steps.value)].reverse().find(s => s.step_id !== container.step_id)
  container.depends_on = prevNode ? [prevNode.step_id] : ['start']
  steps.value.push(container)
  refreshEndDepends()
  // 记录快照以支持撤销
  pushSnapshot()
}

// 向指定容器内添加子容器（条件/循环），需校验嵌套深度不超过上限
function addChildContainer({ parent, type }: { parent: ScenarioStepTree; type: 'condition' | 'loop' }) {
  if (!parent.children) parent.children = []
  const childNesting = (parent.nesting_level || 0) + 1
  // 嵌套深度上限保护：最多5层
  if (childNesting >= 5) {
    toast.warning(t('scenario_detail.nesting_limit_reached'))
    return
  }
  const container = createContainerStep(type)
  container.parent_id = parent.step_id
  container.depends_on = [parent.step_id]
  container.nesting_level = childNesting
  parent.children.push(container)
  // 记录快照以支持撤销
  pushSnapshot()
}

// 删除容器
// P0-4: 删除容器加二次确认（容器删除会连带删除所有子步骤，此前直接 splice 无确认）
async function deleteContainer(container: ScenarioStepTree) {
  // Start/End 步骤固定不可删除
  if (container.step_id === 'start' || container.step_id === 'end') {
    toast.warning(t('scenario_detail.cannot_delete_start_end'))
    return
  }
  // 统计子步骤数（含递归），提示用户连带删除范围
  const childCount = countDescendants(container)
  const msg = childCount > 0
    ? t('scenario_detail.confirm_delete_container_with_children', { name: container.step_id, n: childCount })
    : t('scenario_detail.confirm_delete_container', { name: container.step_id })
  try {
    await ElMessageBox.confirm(msg, t('common.confirm'), { type: 'warning', confirmButtonText: t('common.delete'), cancelButtonText: t('common.cancel') })
  } catch {
    return  // 用户取消
  }
  function removeFrom(list: ScenarioStepTree[]) {
    const idx = list.findIndex(n => n.step_id === container.step_id)
    if (idx >= 0) { list.splice(idx, 1); return true }
    for (const node of list) {
      if (node.children?.length && removeFrom(node.children)) return true
    }
    return false
  }
  removeFrom(steps.value)
  removeDependencyRefs(new Set([container.step_id, ...collectAllDagNodes(container.children || []).map(n => n.step_id)]))
  // 容器删除后刷新 End 依赖（叶节点可能变化）
  refreshEndDepends()
  // 记录快照以支持撤销（在 refreshEndDepends 之后，捕获完整状态）
  pushSnapshot()
}

// 统计节点的所有后代步骤数（递归），用于删除确认提示
function countDescendants(node: ScenarioStepTree): number {
  if (!node.children?.length) return 0
  let count = node.children.length
  for (const child of node.children) {
    count += countDescendants(child)
  }
  return count
}

// P0-4: 删除普通步骤加二次确认（避免误删，仅靠撤销栈兜底风险高）
async function deleteStep(step: ScenarioStepTree) {
  // Start/End 步骤固定不可删除
  if (step.step_id === 'start' || step.step_id === 'end') {
    toast.warning(t('scenario_detail.cannot_delete_start_end'))
    return
  }
  try {
    await ElMessageBox.confirm(
      t('scenario_detail.confirm_delete_step', { name: step.step_id }),
      t('common.confirm'),
      { type: 'warning', confirmButtonText: t('common.delete'), cancelButtonText: t('common.cancel') }
    )
  } catch {
    return  // 用户取消
  }
  function removeFrom(list: ScenarioStepTree[]) {
    const idx = list.findIndex(n => n.step_id === step.step_id)
    if (idx >= 0) { list.splice(idx, 1); return true }
    for (const node of list) {
      if (node.children?.length && removeFrom(node.children)) return true
    }
    return false
  }
  removeFrom(steps.value)
  removeDependencyRefs(new Set([step.step_id, ...collectAllDagNodes(step.children || []).map(n => n.step_id)]))
  // 步骤删除后刷新 End 依赖（叶节点可能变化）
  refreshEndDepends()
  // 记录快照以支持撤销（在 refreshEndDepends 之后，捕获完整状态）
  pushSnapshot()
}

// 文本截断：超过 maxLen 时添加省略号
function truncate(str, maxLen) {
  if (!str) return ''
  return str.length > maxLen ? str.slice(0, maxLen) + '…' : str
}

function assertSourceLabel(ar) {
  const source = ar.origin_source || ar.source
  if (source === 'sql') return ar.sql_name ? `SQL:${ar.sql_name}` : 'SQL'
  const map = {
    response: 'Response',
    status: 'Status',
    header: 'Header',
    performance: 'Performance',
    step: 'Step',
  }
  return map[ar.source] || 'Response'
}

// 右键菜单 → 编辑步骤：切换到步骤编辑 tab 并滚动到目标步骤
function dagEditNode() {
  if (!dagCtx.node) return
  dagCtx.visible = false
  // Start 节点：直接打开参数编辑弹窗
  if (dagCtx.node.id === 'start') {
    selectedNodeId.value = 'start'
    activeTab.value = 'dag'
    openStartEditDialog()
    return
  }
  activeTab.value = 'steps'
}

// 右键菜单 → 删除节点：从树中移除对应步骤（start/end 不可删除）
function dagDeleteNode() {
  if (!dagCtx.node) return
  dagCtx.visible = false
  // Start/End 节点固定在 DAG 中，不可删除
  if (dagCtx.node.id === 'start' || dagCtx.node.id === 'end') {
    toast.warning(t('scenario_detail.cannot_delete_start_end'))
    return
  }
  const node = findStepById(dagCtx.node.id)
  if (node) deleteStep(node)
}

// 右键菜单 → 编辑容器：打开容器编辑对话框
function dagEditContainer() {
  if (!dagCtx.node) return
  dagCtx.visible = false
  const container = findStepById(dagCtx.node.id)
  if (container) openContainerDialog(container)
}

// ── DAG 右键菜单增强函数 ──────────────────────────────

// 在指定列表中找到目标节点后插入新节点，返回是否成功
function insertAfterNode(targetId: string, newNode: ScenarioStepTree): boolean {
  function doInsert(list: ScenarioStepTree[]): boolean {
    const idx = list.findIndex(n => n.step_id === targetId)
    if (idx >= 0) { list.splice(idx + 1, 0, newNode); return true }
    // 递归查找子节点内的容器
    for (const node of list) {
      if (node.children?.length && doInsert(node.children)) return true
    }
    return false
  }
  return doInsert(steps.value)
}

// 右键 → 添加并行步骤：新步骤复制选中节点的上游依赖（同级拓扑，位于同一 DAG 列中并行执行）
function dagAddParallelStep() {
  if (!dagCtx.node) return
  dagCtx.visible = false
  if (dagCtx.node.id === 'start' || dagCtx.node.id === 'end') return
  const source = findStepById(dagCtx.node.id)
  if (!source) return
  const node = createApiStep()
  // 复制源节点的上游依赖，使新节点与源节点位于同一拓扑层级（并行执行）
  node.depends_on = [...(source.depends_on || [])]
  if (insertAfterNode(dagCtx.node.id, node)) {
    // 继承源节点的父容器信息，确保嵌套结构一致
    if (source.parent_id) {
      node.parent_id = source.parent_id
      node.nesting_level = source.nesting_level || 0
    }
    refreshEndDepends()
    // 记录快照以支持撤销
    pushSnapshot()
  }
}

// 右键 → 添加后续步骤：新步骤仅依赖选中节点（形成新的下游分支，不影响已有后续节点）
function dagAddStepAfter() {
  if (!dagCtx.node) return
  dagCtx.visible = false
  if (dagCtx.node.id === 'end') return
  const source = dagCtx.node.id === 'start' ? startStep.value : findStepById(dagCtx.node.id)
  if (!source) return
  const node = createApiStep()
  // 仅依赖选中节点，成为其新的下游分支
  node.depends_on = [dagCtx.node.id]
  const inserted = dagCtx.node.id === 'start'
    ? (steps.value.unshift(node), true)
    : insertAfterNode(dagCtx.node.id, node)
  if (inserted) {
    if (source.parent_id) {
      node.parent_id = source.parent_id
      node.nesting_level = source.nesting_level || 0
    }
    refreshEndDepends()
    // 记录快照以支持撤销
    pushSnapshot()
  }
}

// 右键 → 在当前节点后插入条件容器
function dagAddConditionAfter() {
  if (!dagCtx.node) return
  dagCtx.visible = false
  if (dagCtx.node.id === 'end') return
  const container = createContainerStep('condition')
  container.depends_on = [dagCtx.node.id]
  const inserted = dagCtx.node.id === 'start'
    ? (steps.value.unshift(container), true)
    : insertAfterNode(dagCtx.node.id, container)
  if (inserted) {
    // 如果当前节点在某个容器内，新容器也应继承该父容器的 parent_id 和 nesting_level
    const existingNode = findStepById(dagCtx.node.id)
    if (existingNode?.parent_id) {
      container.parent_id = existingNode.parent_id
      container.nesting_level = existingNode.nesting_level || 0
    }
    refreshEndDepends()
    // 记录快照以支持撤销
    pushSnapshot()
  }
}

// 右键 → 在当前节点后插入循环容器
function dagAddLoopAfter() {
  if (!dagCtx.node) return
  dagCtx.visible = false
  if (dagCtx.node.id === 'end') return
  const container = createContainerStep('loop')
  container.depends_on = [dagCtx.node.id]
  const inserted = dagCtx.node.id === 'start'
    ? (steps.value.unshift(container), true)
    : insertAfterNode(dagCtx.node.id, container)
  if (inserted) {
    const existingNode = findStepById(dagCtx.node.id)
    if (existingNode?.parent_id) {
      container.parent_id = existingNode.parent_id
      container.nesting_level = existingNode.nesting_level || 0
    }
    refreshEndDepends()
    // 记录快照以支持撤销
    pushSnapshot()
  }
}

// 右键 → 复制当前步骤到剪贴板
// P1-2: 复制步骤 —— 支持右键菜单（dagCtx.node）和 Ctrl+C（selectedNodeId）两种来源
function dagCopyStep(sourceId?: string) {
  // 优先用显式传入的 sourceId，其次右键菜单节点，最后选中节点
  const nodeId = sourceId || dagCtx.node?.id || selectedNodeId.value
  if (!nodeId) return
  dagCtx.visible = false
  if (nodeId === 'start' || nodeId === 'end') return
  const node = findStepById(nodeId)
  if (!node) return
  // 深拷贝当前节点（忽略 UI 状态字段），生成新的 step_id 避免冲突
  const clone = JSON.parse(JSON.stringify(node))
  clone.step_id = (node.type === 'api' ? 'step_' : `container_${node.type}_`) + Date.now()
  // P1-2: 子步骤 step_id 用时间戳+索引，避免 Math.random 撞 ID
  clone.children = clone.children ? clone.children.map((c: any, idx: number) => ({ ...c, step_id: 'step_' + Date.now() + '_' + idx })) : []
  clone.expanded = false
  clone.depends_on = [...(node.depends_on || [])] // 保留原始依赖引用
  stepClipboard.value = clone
  toast.info(t('scenario_detail.dag_step_copied', { id: node.step_id }))
}

// P1-2: 粘贴步骤 —— 支持右键菜单和 Ctrl+V（选中节点/末尾）两种触发
function dagPasteStep(targetId?: string) {
  dagCtx.visible = false
  if (!stepClipboard.value) {
    toast.warning(t('scenario_detail.dag_clipboard_empty'))
    return
  }
  const clone = JSON.parse(JSON.stringify(stepClipboard.value))
  clone.step_id = (clone.type === 'api' ? 'step_' : `container_${clone.type}_`) + Date.now()
  // P1-2: 粘贴策略优先级：显式传入 > 右键节点 > 选中节点 > 末尾追加
  const pasteAfter = targetId || dagCtx.node?.id || selectedNodeId.value
  if (pasteAfter && pasteAfter !== 'start' && pasteAfter !== 'end') {
    insertAfterNode(pasteAfter, clone)
  } else {
    steps.value.push(clone)
  }
  refreshEndDepends()
  // 记录快照以支持撤销
  pushSnapshot()
}

// 右键 → 清空所有 depends_on 连线（保留手动拖线后的重新连线能力）
function dagClearAllEdges() {
  dagCtx.visible = false
  function clear(list: ScenarioStepTree[]) {
    for (const node of list) {
      // 清空所有可执行 DAG 节点的依赖关系，保留 Start/End 特殊节点。
      if (node.type === 'api' || node.type === 'condition' || node.type === 'loop') node.depends_on = []
      if (node.children?.length) clear(node.children)
    }
  }
  clear(steps.value)
  refreshEndDepends()
  // 记录快照以支持撤销
  pushSnapshot()
  toast.info(t('scenario_detail.dag_edges_cleared'))
}

// ── Actions ────────────────────────────────────────────────
// 生成唯一 step_id
let stepIdCounter = 0
function genStepId(): string {
  stepIdCounter++
  return `step_${Date.now()}_${stepIdCounter}`
}

// 创建默认 API 步骤节点
function createApiStep(name?: string): ScenarioStepTree {
  const idx = steps.value.length + 1
  const id = genStepId()
  return {
    type: 'api',
    step_id: id,
    api_id: '',
    name: name || t('scenario_detail.step_default_name', { idx }),
    // 默认依赖 start 步骤，保证所有步骤从 Start 开始
    depends_on: ['start'],
    extract: {},
    override_params: {},
    override_headers: {},
    retry: 0,
    retry_delay_s: 1,
    timeout_s: 30,
    pos_x: 0,
    pos_y: 0,
    auth: {},
    pre_script: '',
    post_script: '',
    assertions: [],
    condition: null,
    loop: null,
    loop_var: null,
    loop_count: null,
    wait_ms: 0,
    data_template_id: '',
    children: [],
  }
}

// 创建容器步骤节点
function createContainerStep(type: 'condition' | 'loop'): ScenarioStepTree {
  const id = `container_${type}_${Date.now()}`
  const baseStep = createApiStep()
  return {
    ...baseStep,
    type,
    step_id: id,
    api_id: '',
    name: type === 'condition' ? t('scenario_detail.container_condition') : t('scenario_detail.container_loop'),
    condition: type === 'condition'
      ? { variable: '', operator: 'eq', value: '', on_false: 'skip' }
      : null,
    loop: type === 'loop'
      ? { mode: 'count', count: 1, list_ref: '', item_alias: 'item' }
      : null,
    loop_var: null,
    loop_count: null,
    children: [],
  }
}

// 创建开始步骤：固定 step_id='start'，绿色主题，仅出边，不可删除/拖拽
function createStartStep(): ScenarioStepTree {
  return {
    type: 'start',
    step_id: 'start',
    api_id: '',
    name: t('scenario_detail.start_step'),
    depends_on: [],
    extract: {},
    override_params: {},
    override_headers: {},
    retry: 0,
    retry_delay_s: 0,
    timeout_s: 0,
    pos_x: 0,
    pos_y: 0,
    auth: {},
    pre_script: '',
    post_script: '',
    assertions: [],
    condition: null,
    loop: null,
    loop_var: null,
    loop_count: null,
    wait_ms: 0,
    data_template_id: '',
    children: [],
    start_params: [], // 用户定义的可传入参数列表
  }
}

// 创建结束步骤：固定 step_id='end'，灰色主题，仅入边，不可删除/拖拽，depends_on 自动收集所有叶节点
function createEndStep(): ScenarioStepTree {
  return {
    type: 'end',
    step_id: 'end',
    api_id: '',
    name: t('scenario_detail.end_step'),
    depends_on: [],
    extract: {},
    override_params: {},
    override_headers: {},
    retry: 0,
    retry_delay_s: 0,
    timeout_s: 0,
    pos_x: 0,
    pos_y: 0,
    auth: {},
    pre_script: '',
    post_script: '',
    assertions: [],
    condition: null,
    loop: null,
    loop_var: null,
    loop_count: null,
    wait_ms: 0,
    data_template_id: '',
    children: [],
  }
}

// 确保场景包含 Start/End 步骤：加载时调用，不存在则自动创建
// 同时修复旧场景数据中根步骤缺少 start 依赖的问题
function ensureStartEnd() {
  if (!startStep.value) startStep.value = createStartStep()
  if (!endStep.value) endStep.value = createEndStep()
  // 确保所有真正入口步骤（没有任何上游依赖的步骤）连接到 Start。
  const allNodes = collectAllDagNodes(steps.value)
  for (const s of allNodes) {
    const upstream = (s.depends_on || []).filter(d => d !== 'end')
    if (!upstream.length && s.type !== 'start' && s.type !== 'end') {
      if (!(s.depends_on || []).includes('start')) {
        s.depends_on = [...(s.depends_on || []), 'start']
      }
    }
  }
  refreshEndDepends()
}

// 收集所有叶子 DAG 节点（无任何步骤依赖它的节点），End 步骤自动依赖它们。
function collectLeafSteps(tree: ScenarioStepTree[]): ScenarioStepTree[] {
  const allDagNodes = collectAllDagNodes(tree)
  // 找出所有被依赖的 step_id
  const hasDep = new Set<string>()
  for (const s of allDagNodes) {
    for (const d of (s.depends_on || [])) {
      hasDep.add(d)
    }
  }
  // 叶子：不被任何其他 DAG 节点依赖的非 start/end 节点。
  return allDagNodes.filter(s => !hasDep.has(s.step_id) && s.step_id !== 'start' && s.step_id !== 'end')
}

// 刷新 End 步骤依赖：自动收集所有叶节点并设置为 depends_on
function refreshEndDepends() {
  if (!endStep.value) return
  const leaves = collectLeafSteps(steps.value)
  endStep.value.depends_on = leaves.map(s => s.step_id)
}

// 拖拽排序回调：更新步骤列表并记录快照以支持撤销
function onStepsReorder(newSteps: ScenarioStepTree[]) {
  steps.value = newSteps
  pushSnapshot()
}

// 添加 API 步骤，自动连线到前一个步骤
// parent 存在时添加到容器内部，否则添加到顶层
function addStep(parent?: ScenarioStepTree) {
  const node = createApiStep()
  // 自动连线：找到前一个非容器 API 步骤，追加到 depends_on
  // createApiStep() 已预设 depends_on: ['start']，有前一步时替换为链式依赖
  const siblings = parent ? (parent.children || []) : steps.value
  const prevApiStep = [...siblings].reverse().find(s => s.type === 'api')
  if (prevApiStep) {
    node.depends_on = [prevApiStep.step_id]  // 前一步已通过链连接到 start
  }
  if (parent && (parent.type === 'condition' || parent.type === 'loop')) {
    // 标记父子关系，用于多层嵌套持久化
    node.parent_id = parent.step_id
    node.depends_on = [parent.step_id]
    node.nesting_level = (parent.nesting_level || 0) + 1
    if (!parent.children) parent.children = []
    parent.children.push(node)
  } else {
    steps.value.push(node)
  }
  // 步骤添加后刷新 End 依赖（叶节点可能变化）
  refreshEndDepends()
  // 记录快照以支持撤销
  pushSnapshot()
  // P1-1: 新增步骤后自动打开编辑器，聚焦 api_id 输入（此前需手动点编辑，多一次点击）
  openStepEditor(node.step_id)
}

// 在当前步骤同级添加并行步骤：新步骤复制源步骤的上游依赖（depends_on），使其与源步骤位于同一拓扑层级并行执行
function addParallelStep(source: ScenarioStepTree) {
  const node = createApiStep()
  // 复制源节点的上游依赖，使新节点与源节点并行
  node.depends_on = [...(source.depends_on || [])]
  // 继承源节点的父容器信息
  if (source.parent_id) {
    node.parent_id = source.parent_id
    node.nesting_level = source.nesting_level || 0
  }
  // 在源节点所在的兄弟列表中插入到源节点之后
  insertAfterNode(source.step_id, node)
  refreshEndDepends()
  // 记录快照以支持撤销
  pushSnapshot()
}

// 打开 StepEditor：记录当前步骤 ID，显示编辑器弹窗
function openStepEditor(stepId: string) {
  editingStepId.value = stepId
  editorVisible.value = true
}

function findDagNodePosition(stepId: string) {
  const node = vfElements.value.find((el: any) => el.id === stepId && el.position)
  if (!node) return null
  const width = stepId === 'start' || stepId === 'end' ? CONTAINER_W : NODE_W
  const height = stepId === 'start' || stepId === 'end' ? CONTAINER_H : NODE_H
  return {
    x: node.position.x + width / 2,
    y: node.position.y + height / 2,
  }
}

async function locateStep(stepId: string) {
  const step = stepId === 'start'
    ? startStep.value
    : stepId === 'end'
      ? endStep.value
      : findStepById(stepId)
  if (!step) {
    toast.warning(`未找到步骤 ${stepId}`)
    return
  }
  editorVisible.value = false
  containerDialog.visible = false
  selectedNodeId.value = stepId
  activeTab.value = 'dag'
  await nextTick()
  const pos = findDagNodePosition(stepId)
  if (pos) {
    setCenter(pos.x, pos.y, { zoom: 1, duration: 500 })
  }
  if (stepId === 'start') {
    openStartEditDialog()
  } else if ((step as any).type === 'condition' || (step as any).type === 'loop') {
    await openContainerDialog(step as ScenarioStepTree)
  }
  toast.info(t('scenario_detail.located_step', { id: stepId }))
}

function locateIssue(issue: any) {
  if (issue?.step_id) locateStep(issue.step_id)
}

async function validateScenarioRemote(showToast = true) {
  validating.value = true
  try {
    const res = await scenarioApi.validate(route.params.id, {
      steps: flattenSteps(steps.value),
      name: scenario.value.name,
      description: scenario.value.description,
      owner: scenario.value.owner || '',
    })
    serverIssues.value = res.issues || []
    if (showToast) {
      if (res.valid) toast.success(t('scenario_detail.server_validation_passed'))
      else toast.warning(t('scenario_detail.server_validation_issue_count', { count: serverIssues.value.length }))
    }
    return res.valid
  } catch (e) {
    toast.error(e.message || t('scenario_detail.validation_failed'))
    return false
  } finally {
    validating.value = false
  }
}

function quickFixDependencies() {
  let fixed = 0
  ensureStartEnd()
  const dagNodes = collectAllDagNodes(steps.value)
  const dagIds = new Set(dagNodes.map(s => s.step_id))
  for (const step of dagNodes) {
    const before = JSON.stringify(step.depends_on || [])
    const clean = (step.depends_on || []).filter(d => d === 'start' || dagIds.has(d))
    if (!clean.length) clean.push('start')
    step.depends_on = Array.from(new Set(clean))
    if (JSON.stringify(step.depends_on) !== before) fixed++
  }
  refreshEndDepends()
  pushSnapshot()
  toast[fixed ? 'success' : 'info'](fixed ? t('scenario_detail.quick_fix_done', { count: fixed }) : t('scenario_detail.quick_fix_none'))
}

// 从场景添加至数据工厂/巡检：通过 query param 跳转并预填表单
function handleAddTo(target) {
  const path = target === 'factory' ? '/factory' : '/monitor'
  router.push({ path, query: { scenario_id: scenario.value.id } })
}

// StepEditor 确认回调：将编辑器返回的步骤数据写回树中对应节点
function handleStepConfirm(updated: any) {
  const step = findStepById(editingStepId.value)
  if (step) {
    const oldStepId = step.step_id
    const pos_x = step.pos_x
    const pos_y = step.pos_y
    Object.assign(step, { ...updated, pos_x, pos_y, type: step.type, children: step.children })
    replaceDependencyRefs(oldStepId, step.step_id)
    editingStepId.value = step.step_id
  }
  // 步骤编辑后 depends_on 可能变化，刷新 End 依赖
  refreshEndDepends()
  // 记录快照以支持撤销
  pushSnapshot()
}

// 检测是否连接错误：匹配 ConnectError、Connection refused、Name resolution 等模式
function isConnectError(err) {
  if (!err) return false
  const s = String(err).toLowerCase()
  return s.includes('connecterror') || s.includes('connection refused') ||
    s.includes('name or service not known') || s.includes('getaddrinfo') ||
    s.includes('econnrefused') || s.includes('enotfound') ||
    s.includes('timed out') || s.includes('timeout')
}

// 解析 JSON 输入，成功则调用 setter 写入解析结果并清除错误标记，失败则在 obj 上设置 errKey 标记字段以显示视觉提示
function tryParseJson(str, setter, obj, errKey) {
  try {
    setter(JSON.parse(str))
    obj[errKey] = false
  } catch {
    // JSON 解析失败：设置错误标记，模板中据此显示红色边框 + 错误提示文字
    obj[errKey] = true
  }
}

async function saveScenario() {
  // 保存前校验步骤数据完整性
  const err = validateSteps()
  if (err) { toast.error(err); return }

  saving.value = true
  try {
    // 保存前将树形步骤扁平化为后端兼容格式
    const flatSteps = flattenSteps(steps.value)
    await scenarioApi.update(route.params.id, { steps: flatSteps, name: scenario.value.name, description: scenario.value.description, owner: scenario.value.owner || '' })
    toast.success(t('scenario_detail.saved'))
    // 保存成功后更新原始快照（以展平后的数据为基准），清除未保存标记
    originalSteps.value = JSON.parse(JSON.stringify(flatSteps))
    originalScenarioName.value = scenario.value.name || ''
    // 保存后重新加载，确保前端状态与服务器一致
    await load()
    await validateScenarioRemote(false)
  } catch (e) { toast.error(e.message) }
  finally { saving.value = false }
}

// 树→扁平：将前端树形步骤递归展开为后端统一的新 ScenarioStep[] 数组
// 所有节点显式保留 type，loop 节点写入 loop 对象，子步骤通过 parent_id 表达容器归属
// 每个步骤携带 parent_id 字段，用于从扁平数据还原多层嵌套结构
// 首尾分别追加 Start/End 步骤，保持 DAG 完整性
function flattenSteps(tree: ScenarioStepTree[], parentId?: string): ScenarioStep[] {
  const result: any[] = []

  // 序列化 Start 步骤（step_id='start'，type='start'，仅保存 start_params）
  if (!parentId && startStep.value) {
    const { type, children, expanded, nesting_level, ...step } = startStep.value
    result.push({ ...step, type: 'start' })
  }

  for (const node of tree) {
    const { type, children, expanded, nesting_level, ...step } = node
    // 容器步骤（condition/loop）：保留 type 字段供后端识别容器类型，并递归展开子步骤
    if (type === 'condition' || type === 'loop') {
      const flat: any = { ...step }
      if (parentId) flat.parent_id = parentId
      if (type === 'condition') {
        flat.type = 'condition'
        // condition 字段已在 step.condition 中保留
      } else {
        flat.type = 'loop'
        flat.loop = flat.loop || { mode: 'count', count: flat.loop_count || 1, list_ref: flat.loop_var || '', item_alias: 'item' }
      }
      result.push(flat)
      // 递归展开子步骤，传递当前容器 step_id 作为子步骤的 parent_id
      if (children && children.length) {
        result.push(...flattenSteps(children, node.step_id))
      }
    } else {
      // 普通 API 步骤：显式写入 type=api，设置 parent_id 标记所属容器
      const flatStep: any = { ...step }
      flatStep.type = 'api'
      if (parentId) flatStep.parent_id = parentId
      result.push(flatStep)
    }
  }

  // 序列化 End 步骤（step_id='end'，type='end'，仅保存 depends_on）
  if (!parentId && endStep.value) {
    const { type, children, expanded, nesting_level, ...step } = endStep.value
    result.push({ ...step, type: 'end' })
  }

  return result
}

// 扁平→树：从后端加载统一新 DSL，根据 parent_id 字段构建多层嵌套树形结构
// 提取 start/end 特殊步骤（step_id='start'/'end'）单独管理，不混入普通步骤树
function unflattenSteps(flat: ScenarioStep[]): ScenarioStepTree[] {
  // 提取 start/end 步骤（按 step_id 匹配，从扁平数组中分离）
  const startFlat = flat.find(s => s.step_id === 'start' && (s as any).type === 'start')
  const endFlat = flat.find(s => s.step_id === 'end' && (s as any).type === 'end')
  // 剩余普通步骤（不含 start/end）
  const restSteps = flat.filter(s => s.step_id !== 'start' && s.step_id !== 'end')

  // 恢复 Start 步骤
  if (startFlat) {
    startStep.value = {
      ...startFlat,
      type: 'start',
      children: [],
      start_params: (startFlat as any).start_params || [],
    } as ScenarioStepTree
  }

  // 恢复 End 步骤
  if (endFlat) {
    endStep.value = {
      ...endFlat,
      type: 'end',
      children: [],
    } as ScenarioStepTree
    // End 的 depends_on 以加载数据为准，后续 refreshEndDepends 会按叶节点重新计算
  }

  // 先检查是否有容器步骤（type 为 condition 或 loop），以及是否有 parent_id 字段
  const hasContainers = restSteps.some(s => (s as any).type === 'condition' || (s as any).type === 'loop')
  const hasParentId = restSteps.some(s => !!(s as any).parent_id)

  // 无容器：全部转为普通 api 节点（兼容旧数据，旧步骤无 type 字段）
  if (!hasContainers) {
    return restSteps.map(s => ({ ...s, type: 'api' as StepType, children: [] }))
  }

  // 有 parent_id 字段：使用 parent_id 直接重建树
  if (hasParentId) {
    return buildTreeFromParentId(restSteps)
  }

  // 无 parent_id 但有容器：回退到顺序扫描模式（兼容旧版 1 层嵌套数据）
  return buildTreeSequential(restSteps)
}

// 基于 parent_id 构建树：支持任意层嵌套
function buildTreeFromParentId(flat: ScenarioStep[]): ScenarioStepTree[] {
  // 第一步：将所有步骤转为树节点，建立 step_id → node 映射
  const nodeMap = new Map<string, ScenarioStepTree>()
  const roots: ScenarioStepTree[] = []

  for (const s of flat) {
    const stepType = (s as any).type || 'api'
    const node: ScenarioStepTree = {
      ...s,
      type: stepType,
      children: [],
      expanded: false,
      nesting_level: 0, // 将在第二步计算
    }
    nodeMap.set(s.step_id, node)
  }

  // 第二步：根据 parent_id 建立父子关系
  for (const s of flat) {
    const node = nodeMap.get(s.step_id)!
    const parentId = (s as any).parent_id as string | undefined

    if (parentId && nodeMap.has(parentId)) {
      // 有有效父节点：将当前节点加入父节点的 children
      const parent = nodeMap.get(parentId)!
      if (!parent.children) parent.children = []
      parent.children.push(node)
      // 计算嵌套深度：父节点深度 + 1
      node.nesting_level = (parent.nesting_level || 0) + 1
    } else if (!parentId) {
      // 无 parent_id：根节点
      node.nesting_level = 0
      roots.push(node)
    } else {
      // parent_id 指向不存在的节点（数据异常）：作为根节点处理
      node.nesting_level = 0
      roots.push(node)
    }
  }

  return roots
}

// 顺序扫描构建树：兼容旧版无 parent_id 的 1 层嵌套数据
// 逻辑：遇到容器后，后续步骤归入该容器，直到遇到下一个同层容器
function buildTreeSequential(flat: ScenarioStep[]): ScenarioStepTree[] {
  const containerIds = new Set<string>()
  for (const s of flat) {
    if ((s as any).type === 'condition' || (s as any).type === 'loop') {
      containerIds.add(s.step_id)
    }
  }

  const tree: ScenarioStepTree[] = []
  let currentContainer: ScenarioStepTree | null = null

  for (const s of flat) {
    const stepType = (s as any).type || 'api'
    if (stepType === 'condition' || stepType === 'loop') {
      // 遇到容器步骤：关闭上一个容器，开始新容器
      currentContainer = { ...s, type: stepType, children: [], nesting_level: 0 }
      tree.push(currentContainer)
    } else if (currentContainer) {
      // 普通步骤且当前在容器内：作为子步骤追加到当前容器
      currentContainer.children.push({ ...s, type: 'api', children: [], nesting_level: 1 })
    } else {
      // 顶层普通步骤
      tree.push({ ...s, type: 'api', children: [], nesting_level: 0 })
    }
  }
  return tree
}

// 递归收集树中所有 api 步骤（用于校验、DAG 等）
function collectApiSteps(tree: ScenarioStepTree[]): ScenarioStepTree[] {
  const result: ScenarioStepTree[] = []
  for (const node of tree) {
    if (node.type === 'api') {
      result.push(node)
    }
    if (node.children?.length) {
      result.push(...collectApiSteps(node.children))
    }
  }
  return result
}

// 步骤完整性校验：递归检查树中所有步骤的空 api_id、重复 step_id、循环依赖
function validateSteps() {
  const allApiSteps = collectApiSteps(steps.value)
  // 1. 检查空 api_id（仅 api 类型步骤需要）
  for (const s of allApiSteps) {
    if (!s.api_id) return t('scenario_detail.validate_empty_api', { id: s.step_id || '?' })
  }
  // 2. 检查重复 step_id（递归收集所有节点）
  function collectAll(tree: ScenarioStepTree[]): ScenarioStepTree[] {
    const r: ScenarioStepTree[] = []
    for (const n of tree) { r.push(n); if (n.children?.length) r.push(...collectAll(n.children)) }
    return r
  }
  const allNodes = collectAll(steps.value)
  const ids = allNodes.map(s => s.step_id)
  const dup = ids.find((id, i) => ids.indexOf(id) !== i)
  if (dup) return t('scenario_detail.validate_dup_step', { id: dup })
  // 3. 检查循环依赖（DFS 检测环，覆盖 API/条件/循环节点）
  const adj: Record<string, string[]> = {}
  const allDagNodes = collectAllDagNodes(steps.value)
  const allDagIds = new Set(allDagNodes.map(s => s.step_id))
  for (const s of allDagNodes) {
    // 过滤 depends_on：仅保留引用已存在的 DAG 节点，跳过 start/end 特殊节点
    adj[s.step_id] = (s.depends_on || []).filter(d => d !== 'start' && d !== 'end' && allDagIds.has(d))
  }
  const visited = new Set<string>()
  const inStack = new Set<string>()
  function dfs(id: string): boolean {
    // 当前递归栈中发现重复节点 → 存在环，返回 true
    if (inStack.has(id)) return true
    // 已访问过且确认无环 → 剪枝，跳过重复遍历
    if (visited.has(id)) return false
    visited.add(id)
    inStack.add(id)
    for (const dep of (adj[id] || [])) {
      // 仅递归遍历邻接表中存在的依赖节点，避免遍历不存在的节点
      if (adj[dep] !== undefined && dfs(dep)) return true
    }
    inStack.delete(id)
    return false
  }
  for (const s of allDagNodes) {
    if (dfs(s.step_id)) return t('scenario_detail.validate_cycle')
  }
  return null
}

async function runScenario() {
  // 执行前校验步骤数据完整性
  const err = validateSteps()
  if (err) { toast.error(err); return }
  const serverOk = await validateScenarioRemote(false)
  if (!serverOk) {
    toast.error(t('scenario_detail.server_validation_blocked'))
    return
  }
  if (hasUnsavedChanges.value) {
    await saveScenario()
    if (hasUnsavedChanges.value) {
      toast.error(t('scenario_detail.save_before_run_failed'))
      return
    }
  }

  running.value = true
  // P0-2: 运行时停留在 DAG 画布（此前强制切 result tab 导致节点高亮看不到）
  // 用户可实时观看节点 queued→running→passed/failed 变色，完成后再切 result
  if (activeTab.value !== 'dag') activeTab.value = 'dag'
  lastResult.value = null

  // 收集所有步骤 ID，初始化状态为 queued（画布节点显示淡黄待执行）
  const flatSteps = flattenSteps(steps.value)
  const states: Record<string, StepExecState> = {}
  for (const s of flatSteps) {
    if (s.step_id !== 'start' && s.step_id !== 'end') states[s.step_id] = 'queued'
  }
  stepExecStates.value = states

  // 断开上一次执行残留的 WebSocket 连接和定时器
  if (execWsTimer) { clearInterval(execWsTimer); execWsTimer = null }
  if (execWs) { try { execWs.terminate() } catch {} execWs = null }

  try {
    // P0-2: run 端点现在异步返回 {exec_id}，立即拿到 exec_id 后建立 WS 订阅实时进度
    const runRes = await scenarioApi.run(route.params.id)
    const execId = runRes.exec_id
    if (!execId) {
      // 兼容旧版同步返回（直接是 record）
      lastResult.value = runRes
      running.value = false
      return
    }

    // 建立 WS 订阅 exec:{exec_id} 频道，接收步骤级实时进度
    execWs = useWebSocket(`/execution/${execId}`, (msg: any) => {
      if (!msg) return
      // 步骤状态更新：running/passed/failed/skipped → 画布节点实时变色
      if (msg.type === 'step_status' && msg.step_id && msg.status) {
        stepExecStates.value[msg.step_id] = msg.status as StepExecState
      }
      // 执行完成：填充 result 并提示
      if (msg.type === 'done' && msg.record) {
        lastResult.value = msg.record
        // 根据最终记录校正所有步骤状态（WS 可能丢失部分事件，以最终记录为准）
        if (msg.record.steps) {
          for (const sr of msg.record.steps) {
            stepExecStates.value[sr.step_id] = sr.skipped ? 'skipped' : sr.passed ? 'passed' : 'failed'
          }
        }
        toast[msg.record.passed ? 'success' : 'error'](
          msg.record.passed
            ? t('scenarios.run_pass', { steps: msg.record.steps?.length })
            : t('scenarios.run_fail', { reason: msg.record.failure_reason || t('scenarios.unknown_error') })
        )
        running.value = false
        if (execWsTimer) { clearInterval(execWsTimer); execWsTimer = null }
        // 执行完成后自动切到 result tab 查看详情（用户可点 DAG tab 切回画布查看最终高亮）
        activeTab.value = 'result'
      }
      // 执行错误
      if (msg.type === 'error') {
        toast.error(msg.message || t('scenarios.unknown_error'))
        running.value = false
        if (execWsTimer) { clearInterval(execWsTimer); execWsTimer = null }
      }
    })

    // WS 兜底：长场景不再 30 秒误判超时，改为轮询执行记录直到有最终结果。
    execWsTimer = setInterval(async () => {
      if (!running.value) return
      try {
        const record = await executionApi.get(execId)
        if (record?.finished_at) {
          lastResult.value = record
          if (record.steps) {
            for (const sr of record.steps) {
              stepExecStates.value[sr.step_id] = sr.skipped ? 'skipped' : sr.passed ? 'passed' : 'failed'
            }
          }
          running.value = false
          activeTab.value = 'result'
          if (execWsTimer) { clearInterval(execWsTimer); execWsTimer = null }
        }
      } catch {
        // 执行记录尚未落库时继续等待，WS 仍是主路径。
      }
    }, 10000) as any
  } catch (e) {
    // 执行异常时标记所有步骤为 failed
    for (const sid of Object.keys(stepExecStates.value)) {
      stepExecStates.value[sid] = 'failed'
    }
    toast.error(e.message)
    running.value = false
    if (execWsTimer) { clearInterval(execWsTimer); execWsTimer = null }
  }
}

// ── 执行历史图表 ───────────────────────────────────────────
const historyChartOption = computed(() => {
  const data = (scenarioStats.value.recent || []).slice().reverse()
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', backgroundColor: '#1c2030', borderColor: '#2a2f45', textStyle: { color: '#e2e6f0', fontSize: 12 } },
    grid: { left: 40, right: 20, top: 10, bottom: 24 },
    xAxis: {
      type: 'category',
      data: data.map((_, i) => i + 1),
      axisLine: { lineStyle: { color: '#2a2f45' } },
      axisLabel: { color: '#555d78', fontSize: 10 },
    },
    yAxis: {
      type: 'value', min: 0, max: 1,
      axisLabel: { color: '#555d78', fontSize: 10, formatter: v => v === 1 ? 'PASS' : v === 0 ? 'FAIL' : '' },
      splitLine: { lineStyle: { color: '#1c2030' } },
    },
    series: [{
      type: 'bar',
      data: data.map(e => e.passed ? 1 : 0),
      itemStyle: { color: e => e.data === 1 ? '#3ecf8e' : '#f06060' },
      barMaxWidth: 24,
    }],
  }
})

async function loadScenarioStats() {
  statsLoading.value = true
  try {
    scenarioStats.value = await scenarioApi.stats(route.params.id, 20)
  } catch { /* 静默失败，历史数据为可选 */ }
  finally { statsLoading.value = false }
}

async function loadScenarioVersions() {
  versionsLoading.value = true
  try {
    const res = await scenarioApi.versions(route.params.id, 50)
    scenarioVersions.value = res.items || []
  } catch (e) {
    toast.error(e.message || t('scenario_detail.load_versions_failed'))
  } finally {
    versionsLoading.value = false
  }
}

async function restoreScenarioVersion(row) {
  if (!row?.id || restoringVersion.value) return
  try {
    await ElMessageBox.confirm(
      t('scenario_detail.restore_version_confirm', { version: row.version }),
      t('scenario_detail.restore_version'),
      { type: 'warning' }
    )
  } catch {
    return
  }
  restoringVersion.value = true
  try {
    await scenarioApi.restoreVersion(route.params.id, row.id)
    toast.success(t('scenario_detail.restore_version_done'))
    await load()
    await loadScenarioVersions()
  } catch (e) {
    toast.error(e.message || t('scenario_detail.restore_version_failed'))
  } finally {
    restoringVersion.value = false
  }
}

// 切换到执行历史 tab 时自动加载统计
watch(activeTab, tab => {
  if (tab === 'history') {
    loadScenarioStats()
    loadScenarioVersions()
  }
})

async function load() {
  try {
    const doc = await scenarioApi.get(route.params.id)
    scenario.value = doc
    // 从扁平数组构建树形结构供前端编辑（同时提取 start/end 特殊步骤）
    steps.value    = unflattenSteps(doc.steps || [])
    // 确保场景包含 Start/End 步骤：不存在则自动创建（兼容旧数据）
    ensureStartEnd()
    // 捕获初始快照（展平后的原始数据），用于未保存变更检测
    originalSteps.value = JSON.parse(JSON.stringify(flattenSteps(steps.value)))
    originalScenarioName.value = doc.name || ''
  } catch (e) {
    // 网络异常或权限不足时显示错误提示，页面保持可交互状态
    toast.error(e.message || t('scenario_detail.load_failed'))
  } finally { loading.value = false }
}

// 加载项目内全部 API（用于步骤编辑时显示 API 名称）
async function loadApis() {
  try {
    const res = await apiApi.list({ project_id: projectStore.current, limit: 500 })
    apiList.value = res.items || []
  } catch { /* 静默失败，API 名称为辅助展示 */ }
}

// api_id 变更时自动预填充步骤参数：从 API 定义中提取 query_params、headers、body 填入 override 字段
// 仅在字段为空时自动填充，避免覆盖用户已手动编辑的内容
function onApiIdChange(step) {
  if (!step || !step.api_id) return
  const api = apiList.value.find(a => a.id === step.api_id)
  if (!api) return
  // 步骤名称为空时自动使用 API 名称
  if (!step.name) step.name = api.name || ''
  // override_params 为空时预填充 query_params + body（JSON 类型时合并 body 字段）
  if (!step.override_params || Object.keys(step.override_params).length === 0) {
    const params = { ...(api.request?.query_params || {}) }
    if (api.request?.body && api.request?.body_type === 'json' && typeof api.request.body === 'object' && !Array.isArray(api.request.body)) {
      Object.assign(params, api.request.body)
    }
    step.override_params = params
  }
  // override_headers 为空时预填充请求头
  if (!step.override_headers || Object.keys(step.override_headers).length === 0) {
    step.override_headers = { ...(api.request?.headers || {}) }
  }
  // 断言为空时自动从 API 填充（仅首次自动填充，用户清空后不再填充）
  if (!step.assertions || !step.assertions.length) {
    apiApi.getAsserts(step.api_id).then(asserts => {
      if (asserts && Array.isArray(asserts)) {
        step.assertions = asserts.map(a => ({
          source: (a.field || '').startsWith('$') ? 'performance' : 'response',
          path: a.field || '',
          operator: a.operator || 'eq',
          expected: a.expected ?? '',
        }))
      }
    }).catch(() => { /* 忽略断言加载失败 */ })
  }
}

// 路由离开守卫：存在未保存更改时弹窗确认，防止误操作丢失编辑内容
onBeforeRouteLeave(async (_to, _from, next) => {
  if (!hasUnsavedChanges.value) { next(); return }
  try {
    await ElMessageBox.confirm(
      t('scenario_detail.unsaved_confirm'),
      t('scenario_detail.unsaved_title'),
      { confirmButtonText: t('scenario_detail.save_and_leave'), cancelButtonText: t('scenario_detail.leave_without_save'), type: 'warning', distinguishCancelAndClose: true }
    )
    // 用户点击「保存并离开」：先保存再导航
    await saveScenario()
    next()
  } catch (action) {
    // distinguishCancelAndClose 模式下：cancel=不保存直接离开，close=点X关闭取消导航
    if (action === 'cancel') {
      // 用户点击「不保存」：直接导航，丢弃更改
      next()
    }
    // action === 'close'（点 X 关闭弹窗）：不调用 next()，取消导航，留在当前页
  }
})

async function loadPendingGens() {
  try {
    // P2-3: 传 project_id 项目隔离（此前显示全局待审核数，跨项目误导）
    const res = await generationApi.list({ type: 'scenario', status: 'pending_review', project_id: projectStore.current, limit: 1 })
    pendingGenCount.value = res.total || 0
  } catch {
    // 接口异常时静默降级，不阻塞页面渲染
    pendingGenCount.value = 0
  }
}

onMounted(() => { load(); loadApis(); loadPendingGens() })

// 键盘快捷键：Ctrl+Z 撤销，Ctrl+Shift+Z / Ctrl+Y 重做
// 仅在 steps/dag tab 下生效，避免与浏览器默认行为冲突（如地址栏、文本输入框等）
function onKeydown(e: KeyboardEvent) {
  // 仅在 step 编辑相关 tab 下启用快捷键
  if (activeTab.value !== 'steps' && activeTab.value !== 'dag') return
  // 避免在输入框中触发（保留默认的 Ctrl+Z 行为）
  const tag = (e.target as HTMLElement)?.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (e.target as HTMLElement)?.isContentEditable) return
  if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key === 'z') {
    e.preventDefault()
    undo()
  } else if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'Z') {
    e.preventDefault()
    redo()
  } else if ((e.ctrlKey || e.metaKey) && e.key === 'y') {
    e.preventDefault()
    redo()
  // P1-2: Ctrl+C/V 复制粘贴步骤（用户本能操作，此前仅支持右键菜单）
  } else if ((e.ctrlKey || e.metaKey) && !e.shiftKey && (e.key === 'c' || e.key === 'C')) {
    // 仅在有选中节点时复制（避免与文本复制冲突）
    if (selectedNodeId.value && selectedNodeId.value !== 'start' && selectedNodeId.value !== 'end') {
      e.preventDefault()
      dagCopyStep(selectedNodeId.value)
    }
  } else if ((e.ctrlKey || e.metaKey) && !e.shiftKey && (e.key === 'v' || e.key === 'V')) {
    if (stepClipboard.value) {
      e.preventDefault()
      dagPasteStep()
    }
  }
}
onMounted(() => document.addEventListener('keydown', onKeydown))
onUnmounted(() => document.removeEventListener('keydown', onKeydown))

// 组件卸载时清理执行状态 WebSocket 连接和定时器，避免回调操作已销毁的响应式数据
onUnmounted(() => {
  if (execWsTimer) { clearInterval(execWsTimer); execWsTimer = null }
  if (execWs) { try { execWs.terminate() } catch {} execWs = null }
})
</script>

<style scoped>
/* 待审核场景提示徽章内链接：去掉下划线，继承徽章颜色 */
.review-hint-link { color: inherit; text-decoration: none; }
.review-hint-badge { cursor: pointer; }
.validation-issue-list { display:flex; flex-direction:column; gap:6px; margin-top:6px; }
.validation-issue-row { display:flex; align-items:center; gap:8px; font-size:12px; flex-wrap:wrap; }

.dag-card    { overflow: hidden; }
.dag-canvas  { overflow-x: auto; padding: 8px 0; min-height: 120px; }

.step-card {
  border: 1px solid var(--border); border-radius: var(--radius);
  margin-bottom: 10px; overflow: hidden;
}
/* vuedraggable 拖拽中占位符样式：虚线边框 + 半透明背景 */
.step-ghost {
  opacity: 0.4;
  border: 2px dashed var(--brand, #4f8ef7) !important;
}
.step-header {
  display: flex; align-items: center; gap: 8px;
  background: var(--bg-3); padding: 8px 12px; border-bottom: 1px solid var(--border);
}
.step-index  { font-size: 11px; color: var(--text-3); font-weight: 700; min-width: 20px; }
.step-id-input   { width: 120px; }
.step-name-input { flex: 1; }
.step-body   { padding: 12px 16px; }
/* 紧凑摘要：步骤卡片收起时显示 API 名称 + 参数/请求头/断言计数 */
.step-body-compact {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 16px;
}
/* DAG 右侧面板摘要区域 */
.panel-summary { padding: 4px 0; }

.result-step {
  border: 1px solid var(--border); border-radius: var(--radius);
  margin-bottom: 8px; overflow: hidden;
}
.result-step-header {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; background: var(--bg-3); border-bottom: 1px solid var(--border);
}
.result-error {
  padding: 8px 12px; font-size: 12px; color: var(--red);
  font-family: var(--font-mono); background: rgba(240,96,96,.05);
}
.assert-list { padding: 6px 12px; }
.assert-row  { display: flex; align-items: center; gap: 8px; padding: 3px 0; flex-wrap: wrap; }
/* 失败断言 diff 视图：高亮期望值 vs 实际值对比 */
.assert-row-failed {
  background: rgba(245,108,108,.06); border-radius: 4px; padding: 6px 8px;
  border-left: 3px solid var(--red, #f56c6c);
}
.assert-diff-expected { font-size: 10px; color: var(--text-2); background: rgba(62,207,142,.15); border-radius: 3px; padding: 1px 6px; }
.assert-diff-arrow { font-size: 10px; margin: 0 2px; }
.assert-diff-actual { font-size: 10px; background: rgba(245,108,108,.12); border-radius: 3px; padding: 1px 6px; }
.assert-diff-error { font-size: 10px; color: var(--red); background: rgba(245,108,108,.12); border-radius: 3px; padding: 1px 6px; }

/* 提取的变量展示（数据流） */
.extracted-vars {
  display: flex; align-items: center; flex-wrap: wrap; gap: 4px;
  padding: 6px 12px; background: rgba(79,142,247,.04); border-top: 1px solid var(--border);
}
.extracted-var-tag { font-size: 10px; }

/* 执行汇总面板 */
.result-summary-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px;
}
.summary-item { padding: 8px 0; }
.summary-durations { grid-column: 1 / -1; }
/* 通过/失败/跳过比例条 */
.summary-bar {
  display: flex; height: 8px; border-radius: 4px; overflow: hidden; background: var(--bg-3);
}
.summary-bar-pass { background: var(--green, #3ecf8e); transition: width .3s ease; }
.summary-bar-fail { background: var(--red, #f56c6c); transition: width .3s ease; }
.summary-bar-skip { background: var(--text-4, #ccc); transition: width .3s ease; }
/* 步骤耗时分布条 */
.duration-row {
  display: flex; align-items: center; gap: 6px; margin-bottom: 3px;
}
.duration-bar-track {
  flex: 1; height: 6px; border-radius: 3px; background: var(--bg-3); overflow: hidden;
}
.duration-bar-fill {
  height: 100%; border-radius: 3px; transition: width .3s ease; min-width: 2px;
}

.resp-detail { padding: 8px 12px; }
.form-label { font-size: 11px; font-weight: 600; color: var(--text-3); text-transform: uppercase; letter-spacing: .05em; margin-bottom: 6px; }
.form-row { display: flex; gap: 12px; margin-bottom: 12px; }
.input-error :deep(.el-textarea__inner) { border-color: var(--red) !important; background: rgba(240,96,96,.04); }
.field-error { font-size: 11px; color: var(--red); display: block; margin-top: 4px; }

/* DAG 连线删除确认高亮 */
.vf-edge-selected .vue-flow__edge-path {
  stroke: var(--red, #f56c6c) !important; stroke-width: 3px !important;
  filter: drop-shadow(0 0 4px rgba(245, 108, 108, 0.5));
}

/* DAG 右键菜单 */
.dag-ctx-menu {
  position: absolute; z-index: 1000;
  background: var(--bg-2); border: 1px solid var(--border);
  border-radius: var(--radius); box-shadow: 0 4px 16px rgba(0,0,0,.3);
  min-width: 140px; padding: 4px 0;
}
.dag-ctx-item {
  padding: 8px 16px; font-size: 12px; cursor: pointer;
  color: var(--text-2); user-select: none;
}
.dag-ctx-item:hover { background: var(--bg-hover); color: var(--text); }
.dag-ctx-readonly { cursor: default; color: var(--text-3, #888); font-style: italic; }
.dag-ctx-readonly:hover { background: transparent; color: var(--text-3, #888); }
.dag-ctx-divider { border-top: 1px solid var(--border); margin: 2px 0; padding: 0; cursor: default; }
.dag-ctx-divider:hover { background: transparent; }
/* DAG VueFlow 自定义节点样式 */
.dag-vueflow-wrapper { height: 520px; width: 100%; position: relative; }
/* P1-5: 自动排列按钮浮在画布右上角 */
.dag-auto-layout-btn {
  position: absolute; top: 10px; right: 10px; z-index: 10;
  padding: 4px 10px; font-size: 12px; cursor: pointer;
  background: var(--bg-0, #fff); border: 1px solid var(--border, #ddd);
  border-radius: 4px; color: var(--text-2, #555);
  transition: all .15s;
}
.dag-auto-layout-btn:hover { border-color: var(--brand, #4f8ef7); color: var(--brand, #4f8ef7); }
.vf-step-node {
  width: 100%; height: 100%;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  border: 2px solid var(--border); border-radius: 8px;
  cursor: grab; position: relative; user-select: none;
  font-size: 11px; text-align: center; overflow: hidden; padding: 4px 8px;
}
.vf-step-node:hover { box-shadow: 0 2px 12px rgba(0,0,0,.15); }
.vf-step-node:active { cursor: grabbing; }
/* 条件节点：菱形标记边框 */
.vf-step-node.has-condition { border-style: dashed; }
/* 循环节点：不同颜色边框（在节点数据中通过 borderColor 控制） */
.vf-step-node.has-loop { border-width: 2.5px; }
.node-line1 { font-family: var(--font-mono); font-size: 11px; font-weight: 600; color: var(--text); line-height: 1.3; }
.node-line2 { font-size: 10px; color: var(--text-2); line-height: 1.4; margin-top: 2px; }
.node-badge {
  position: absolute; top: 2px; right: 4px;
  font-size: 12px; line-height: 1; z-index: 2;
}
.node-badge-cond { color: var(--brand, #4f8ef7); }
.node-badge-loop { color: var(--yellow, #f0a040); }

.dag-ctx-divider { height: 1px; background: var(--border); margin: 2px 8px; padding: 0; cursor: default; }
.dag-ctx-overlay { position: fixed; inset: 0; z-index: 999; }

/* 连线 tooltip：鼠标悬停时显示完整依赖关系 */
.dag-edge-tooltip {
  position: fixed;
  z-index: 1001;
  min-width: 180px;
  max-width: 320px;
  background: var(--bg-1, #fff);
  border: 1px solid var(--border, #e0e0e0);
  border-radius: 6px;
  box-shadow: 0 2px 12px rgba(0,0,0,.12);
  padding: 10px 12px;
  pointer-events: none;
  .edge-tooltip-header {
    font-size: 10px; font-weight: 600; color: var(--text-2); margin-bottom: 6px;
    border-bottom: 1px solid var(--border); padding-bottom: 4px;
  }
  .edge-tooltip-row {
    font-size: 10px; color: var(--text-3); margin-bottom: 2px;
    .edge-tooltip-label { color: var(--text-2); font-weight: 500; }
  }
  .edge-tooltip-flows {
    display: flex; flex-direction: column; gap: 2px; margin-top: 4px;
    .edge-tooltip-flow {
      font-size: 10px; color: var(--brand, #4f8ef7); background: rgba(79,142,247,.08);
      border-radius: 3px; padding: 2px 6px;
    }
  }
}
.edge-tooltip-fade-enter-active { transition: opacity .12s ease; }
.edge-tooltip-fade-leave-active { transition: opacity .08s ease; }
.edge-tooltip-fade-enter-from,
.edge-tooltip-fade-leave-to { opacity: 0; }

/* Handle 连接点样式 */
.vf-handle { width: 10px; height: 10px; border: 2px solid var(--border); background: var(--bg-2); }
.vf-handle-target { border-radius: 2px; }
.vf-handle-source { border-radius: 50%; }
.vf-handle:hover { border-color: var(--brand, #4f8ef7); background: var(--brand, #4f8ef7); }

/* 节点选中高亮 */
.vf-step-node.is-selected { box-shadow: 0 0 0 2px var(--brand, #4f8ef7); }

/* 右侧节点配置面板 */
.dag-node-panel {
  position: absolute; top: 0; right: 0; bottom: 0; width: 320px;
  background: var(--bg-2); border-left: 1px solid var(--border);
  display: flex; flex-direction: column; z-index: 10;
  box-shadow: -4px 0 16px rgba(0,0,0,.15);
}
.panel-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 12px; border-bottom: 1px solid var(--border);
}
.panel-title { font-size: 12px; font-weight: 600; color: var(--text); }
.panel-body {
  flex: 1; overflow-y: auto; padding: 12px;
  display: flex; flex-direction: column; gap: 10px;
}
.panel-field { display: flex; flex-direction: column; gap: 4px; }
.panel-row { display: flex; gap: 10px; }
.panel-actions {
  display: flex; gap: 8px; padding-top: 4px;
  border-top: 1px solid var(--border); margin-top: 4px;
}
.panel-info {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 8px; border-radius: var(--radius);
  font-size: 10px;
  border-left: 3px solid transparent;
}
.panel-info-cond {
  background: rgba(79, 142, 247, 0.06); /* 淡蓝色背景 */
  border-left-color: var(--brand, #4f8ef7);
}
.panel-info-loop {
  background: rgba(103, 194, 58, 0.06); /* 淡绿色背景 */
  border-left-color: var(--green, #67c23a);
}
.panel-info-dot {
  width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0;
}
.dot-cond { background: var(--brand, #4f8ef7); }
.dot-loop { background: var(--green, #67c23a); }

/* 面板滑入/滑出动画 */
.panel-slide-enter-active, .panel-slide-leave-active {
  transition: transform 0.2s ease, opacity 0.2s ease;
}
.panel-slide-enter-from, .panel-slide-leave-to {
  transform: translateX(100%); opacity: 0;
}


/* ConnectError 排查建议 */
.connect-hint {
  margin-top: 8px; padding: 8px 10px;
  background: rgba(240,160,64,.08); border-radius: var(--radius);
  font-size: 11px; color: var(--text-2);
}
.connect-hint strong { color: var(--yellow, #f0a040); }
.connect-hint ul { margin: 4px 0 0 16px; padding: 0; }
.connect-hint li { margin: 2px 0; }

/* ── 容器卡片样式（条件/循环） ── */
.container-card {
  border-radius: var(--radius); margin-bottom: 8px;
  overflow: hidden;
}
.container-card.card-cond {
  border: 1px solid var(--brand, #4f8ef7);
  background: rgba(79, 142, 247, 0.03);
}
.container-card.card-loop {
  border: 1px solid var(--green, #67c23a);
  background: rgba(103, 194, 58, 0.03);
}
.container-header {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 10px; cursor: pointer;
  user-select: none;
}
.container-summary {
  flex: 1; font-size: 12px; font-weight: 600;
  color: var(--text); min-width: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.container-chevron {
  font-size: 12px; color: var(--text-2);
  transition: transform 0.2s ease;
  flex-shrink: 0;
}
.container-chevron.expanded {
  transform: rotate(90deg); /* ArrowDown → ArrowUp 效果 */
}
.container-icon-diamond {
  width: 18px; height: 18px; flex-shrink: 0;
  background: var(--brand, #4f8ef7); color: #fff;
  transform: rotate(45deg); display: flex; align-items: center; justify-content: center;
  font-size: 8px; border-radius: 2px;
}
.container-icon-diamond::after {
  content: ''; /* 菱形图标占位 */;
}
.container-icon-loop {
  width: 18px; height: 18px; flex-shrink: 0;
  background: var(--green, #67c23a); color: #fff;
  display: flex; align-items: center; justify-content: center;
  font-size: 10px; border-radius: 50%;
}
.container-children {
  padding: 0 0 8px 24px; /* 子步骤缩进 */
  display: flex; flex-direction: column;
  gap: 4px;
}
.step-card-child {
  border-left: 2px solid var(--border);
  padding-left: 8px;
}
.drag-handle-sub {
  cursor: grab; color: var(--text-3);
  font-size: 14px; padding: 0 4px;
  flex-shrink: 0;
}

/* ── DAG 容器节点样式 ── */
.vf-container-node {
  padding: 10px 14px; border-radius: var(--radius);
  font-size: 12px; min-width: 160px; max-width: 220px;
  cursor: pointer;
}
.vf-node-cond {
  background: rgba(79, 142, 247, 0.08);
  border: 2px dashed var(--brand, #4f8ef7);
}
.vf-node-loop {
  background: rgba(103, 194, 58, 0.08);
  border: 2px solid var(--green, #67c23a);
  border-radius: 16px; /* 圆角更大 */
}
.loop-child-list {
  margin-top: 6px;
  padding: 4px 6px;
  background: rgba(103, 194, 58, 0.06);
  border-radius: 6px;
  border: 1px dashed rgba(103, 194, 58, 0.3);
  max-height: 100px;
  overflow-y: auto;
}
.loop-child-step {
  font-size: 11px;
  color: var(--text-2);
  padding: 1px 0;
}

.variable-graph-card {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface-1);
  padding: 12px;
  margin-bottom: 14px;
}
.variable-graph-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
  margin-bottom: 10px;
}
.variable-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.variable-consumers {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 12px;
}
.consumer-fields {
  margin-left: 4px;
  font-size: 11px;
}

/* Start 节点：绿色主题 */
.vf-node-start {
  background: rgba(62, 207, 142, 0.08);
  border: 2px solid var(--green, #3ecf8e);
  border-radius: 20px 4px 4px 20px; /* 左侧圆角更大 */
}
/* End 节点：灰色主题 */
.vf-node-end {
  background: rgba(160, 160, 180, 0.08);
  border: 2px solid var(--text-3, #888);
  border-radius: 4px 20px 20px 4px; /* 右侧圆角更大 */
}

/* 执行状态高亮：queued — 淡黄虚线边框 */
.vf-step-node.exec-state-queued, .vf-container-node.exec-state-queued {
  border-style: dashed;
  border-color: var(--warning, #e6a23c);
}
/* 执行状态高亮：running — 蓝色呼吸脉冲动画 */
.vf-step-node.exec-state-running, .vf-container-node.exec-state-running {
  border-color: var(--brand, #4f8ef7);
  animation: exec-pulse 1s ease-in-out infinite;
}
@keyframes exec-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(79, 142, 247, 0.4); }
  50%      { box-shadow: 0 0 8px 2px rgba(79, 142, 247, 0.6); }
}
/* 执行状态高亮：passed — 绿色实心边框 */
.vf-step-node.exec-state-passed, .vf-container-node.exec-state-passed {
  border-color: var(--green, #3ecf8e);
  border-width: 2px;
}
/* 执行状态高亮：failed — 红色加粗边框 */
.vf-step-node.exec-state-failed, .vf-container-node.exec-state-failed {
  border-color: var(--red, #f06060);
  border-width: 2px;
}
/* 执行状态高亮：skipped — 灰色虚线边框 */
.vf-step-node.exec-state-skipped, .vf-container-node.exec-state-skipped {
  border-color: var(--text-3, #888);
  border-style: dashed;
  opacity: 0.6;
}

/* 步骤卡片执行状态样式：Steps 编辑 tab 中步骤卡片的状态边框 */
.step-card.exec-state-queued { border-left: 3px solid var(--warning, #e6a23c); }
.step-card.exec-state-running {
  border-left: 3px solid var(--brand, #4f8ef7);
  animation: exec-pulse 1s ease-in-out infinite;
}
.step-card.exec-state-passed { border-left: 3px solid var(--green, #3ecf8e); }
.step-card.exec-state-failed { border-left: 3px solid var(--red, #f06060); }
.step-card.exec-state-skipped {
  border-left: 3px solid var(--text-3, #888);
  opacity: 0.6;
}

/* Start/End 卡片样式（Steps 编辑 tab） */
.card-start {
  border: 1px solid var(--green, #3ecf8e);
  background: rgba(62, 207, 142, 0.04);
}
.card-end {
  border: 1px solid var(--text-3, #888);
  background: rgba(160, 160, 180, 0.04);
}
.container-icon-start {
  width: 18px; height: 18px; flex-shrink: 0;
  background: var(--green, #3ecf8e); color: #fff;
  display: flex; align-items: center; justify-content: center;
  font-size: 8px; border-radius: 2px;
}
.container-icon-end {
  width: 18px; height: 18px; flex-shrink: 0;
  background: var(--text-3, #888); color: #fff;
  display: flex; align-items: center; justify-content: center;
  font-size: 8px; border-radius: 2px;
}
</style>
