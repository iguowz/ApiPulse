<template>
  <VueDraggable
    :model-value="steps"
    @update:model-value="emit('update:modelValue', $event)"
    :animation="200"
    item-key="step_id"
    ghost-class="step-ghost"
    :handle="nestingLevel === 0 ? '.drag-handle' : '.drag-handle-sub'"
  >
    <template #item="{ element: node, index: i }">
      <!-- === API 步骤节点 === -->
      <!-- exec-state-* 由父组件通过 stepExecStates prop 注入执行状态 -->
      <div v-if="node.type === 'api'" :class="['step-card', 'exec-state-' + (stepExecStates?.[node.step_id] || 'idle')]">
        <div class="step-header">
          <span :class="nestingLevel === 0 ? 'drag-handle' : 'drag-handle-sub'" mono :title="$t('scenario_detail.drag_to_reorder')" style="cursor:grab;font-size:14px;line-height:1;user-select:none;padding:0 2px">⋮⋮</span>
          <span class="step-index mono">{{ String(i+1).padStart(2,'0') }}</span>
          <!-- 重复 step_id 警告：橙色图标 + tooltip -->
          <el-tooltip v-if="stepErrors?.[node.step_id]?.includes('duplicate_id')" :content="$t('scenario_detail.validate_dup_step_id', { id: node.step_id })" placement="top">
            <span style="color:var(--el-color-warning);font-size:14px;cursor:help;flex-shrink:0" title="">⚠</span>
          </el-tooltip>
          <el-input v-model="node.step_id" placeholder="step_id" size="small" class="step-id-input mono" :class="{ 'step-id-error': stepErrors?.[node.step_id]?.includes('duplicate_id') }" :style="{ fontSize: nestingLevel === 0 ? '12px' : '11px' }" />
          <el-input v-model="node.name" :placeholder="$t('scenario_detail.step_name_placeholder')" size="small" class="step-name-input" />
          <el-button size="small" :title="$t('scenario_detail.dag_add_parallel_step')" @click="emit('addParallel', node)">+∥</el-button>
          <el-button size="small" @click="emit('editStep', node.step_id)">{{ $t('common.edit') }}</el-button>
          <el-button type="danger" size="small" :icon="Close" @click="emit('deleteStep', node)" />
        </div>
        <div class="step-body-compact">
          <!-- 空 api_id 警告：红色图标 + tooltip -->
          <el-tooltip v-if="stepErrors?.[node.step_id]?.includes('empty_api')" :content="$t('scenario_detail.validate_empty_api_id')" placement="top">
            <span style="color:var(--el-color-danger);font-size:13px;cursor:help;flex-shrink:0;margin-right:4px" title="">⚠</span>
          </el-tooltip>
          <span class="text-3" :class="{ 'text-danger': stepErrors?.[node.step_id]?.includes('empty_api') }" :style="{ fontSize: nestingLevel === 0 ? '11px' : '10px' }">
            {{ apiNameMap[node.api_id] || node.api_id || $t('step_editor.no_api') }}
          </span>
          <span class="text-3" style="font-size:10px;margin-left:auto">
            {{ Object.keys(node.override_params || {}).filter(k => k !== '_body' && k !== '_body_type').length }} {{ $t('step_editor.params_count') }}
            &middot; {{ Object.keys(node.override_headers || {}).length }} {{ $t('step_editor.headers_count') }}
            &middot; {{ (node.assertions || []).length }} {{ $t('step_editor.assertions_count') }}
          </span>
        </div>
      </div>

      <!-- === 条件容器节点 === -->
      <div v-else-if="node.type === 'condition'" :class="['container-card', 'card-cond', 'exec-state-' + (stepExecStates?.[node.step_id] || 'idle')]">
        <div class="container-header" @click="node.expanded = !node.expanded">
          <span :class="nestingLevel === 0 ? 'drag-handle' : 'drag-handle-sub'" mono :title="$t('scenario_detail.drag_to_reorder')" style="cursor:grab;font-size:14px;line-height:1;user-select:none;padding:0 2px" @click.stop>⋮⋮</span>
          <span class="container-icon-diamond">&#9671;</span>
          <span class="step-index mono" style="min-width:20px">{{ String(i+1).padStart(2,'0') }}</span>
          <el-input v-model="node.step_id" placeholder="step_id" size="small" class="step-id-input mono" style="font-size:12px" @click.stop />
          <span class="container-summary">
            {{ node.condition?.variable ? $t('scenario_detail.container_cond_summary', { var: node.condition.variable, op: node.condition.operator, val: node.condition.value, action: node.condition.on_false }) : $t('scenario_detail.dag_no_condition') }}
          </span>
          <span class="text-3" style="font-size:10px;margin-left:auto;white-space:nowrap">{{ (node.children || []).length }} {{ $t('scenario_detail.steps_count', { n: (node.children || []).length }) }}</span>
          <el-button size="small" @click.stop="emit('editContainer', node)">{{ $t('common.edit') }}</el-button>
          <el-button type="danger" size="small" :icon="Close" @click.stop="emit('deleteContainer', node)" />
          <el-icon class="container-chevron" style="margin-left:4px"><component :is="node.expanded ? ArrowUp : ArrowDown" /></el-icon>
        </div>
        <!-- 容器展开：递归渲染子步骤 -->
        <div v-show="node.expanded" class="container-children">
          <el-empty v-if="!node.children?.length" :description="$t('scenario_detail.no_children')" :image-size="32" style="padding:16px" />
          <StepTreeChildren
            v-else
            :steps="node.children"
            :nesting-level="nestingLevel + 1"
            :api-name-map="apiNameMap"
            :step-exec-states="stepExecStates"
            :step-errors="stepErrors"
            @update:model-value="node.children = $event"
            @edit-step="emit('editStep', $event)"
            @delete-step="emit('deleteStep', $event)"
            @edit-container="emit('editContainer', $event)"
            @delete-container="emit('deleteContainer', $event)"
            @add-step="emit('addStep', node)"
            @add-parallel="emit('addParallel', $event)"
            @add-container="emit('addContainer', $event)"
          />
          <!-- 子步骤操作按钮：仅当嵌套深度未达到上限时显示容器添加按钮 -->
          <el-button size="small" @click="emit('addStep', node)" style="margin-top:8px;width:100%">{{ $t('scenario_detail.add_child_step') }}</el-button>
          <div v-if="(node.nesting_level || 0) + 1 < maxNesting" style="display:flex;gap:8px;margin-top:6px">
            <el-button size="small" @click="emit('addContainer', { parent: node, type: 'condition' })" style="flex:1">{{ $t('scenario_detail.add_condition') }}</el-button>
            <el-button size="small" @click="emit('addContainer', { parent: node, type: 'loop' })" style="flex:1">{{ $t('scenario_detail.add_loop') }}</el-button>
          </div>
        </div>
      </div>

      <!-- === 循环容器节点 === -->
      <div v-else-if="node.type === 'loop'" :class="['container-card', 'card-loop', 'exec-state-' + (stepExecStates?.[node.step_id] || 'idle')]">
        <div class="container-header" @click="node.expanded = !node.expanded">
          <span :class="nestingLevel === 0 ? 'drag-handle' : 'drag-handle-sub'" mono :title="$t('scenario_detail.drag_to_reorder')" style="cursor:grab;font-size:14px;line-height:1;user-select:none;padding:0 2px" @click.stop>⋮⋮</span>
          <span class="container-icon-loop">&#8635;</span>
          <span class="step-index mono" style="min-width:20px">{{ String(i+1).padStart(2,'0') }}</span>
          <el-input v-model="node.step_id" placeholder="step_id" size="small" class="step-id-input mono" style="font-size:12px" @click.stop />
          <span class="container-summary">
            {{ node.loop_var ? $t('scenario_detail.container_loop_var_summary', { var: node.loop_var }) : node.loop_count ? $t('scenario_detail.container_loop_count_summary', { count: node.loop_count }) : $t('scenario_detail.dag_no_loop') }}
          </span>
          <span class="text-3" style="font-size:10px;margin-left:auto;white-space:nowrap">{{ (node.children || []).length }} {{ $t('scenario_detail.steps_count', { n: (node.children || []).length }) }}</span>
          <el-button size="small" @click.stop="emit('editContainer', node)">{{ $t('common.edit') }}</el-button>
          <el-button type="danger" size="small" :icon="Close" @click.stop="emit('deleteContainer', node)" />
          <el-icon class="container-chevron" style="margin-left:4px"><component :is="node.expanded ? ArrowUp : ArrowDown" /></el-icon>
        </div>
        <!-- 容器展开：递归渲染子步骤 -->
        <div v-show="node.expanded" class="container-children">
          <el-empty v-if="!node.children?.length" :description="$t('scenario_detail.no_children')" :image-size="32" style="padding:16px" />
          <StepTreeChildren
            v-else
            :steps="node.children"
            :nesting-level="nestingLevel + 1"
            :api-name-map="apiNameMap"
            :step-exec-states="stepExecStates"
            :step-errors="stepErrors"
            @update:model-value="node.children = $event"
            @edit-step="emit('editStep', $event)"
            @delete-step="emit('deleteStep', $event)"
            @edit-container="emit('editContainer', $event)"
            @delete-container="emit('deleteContainer', $event)"
            @add-step="emit('addStep', node)"
            @add-parallel="emit('addParallel', $event)"
            @add-container="emit('addContainer', $event)"
          />
          <!-- 子步骤操作按钮 -->
          <el-button size="small" @click="emit('addStep', node)" style="margin-top:8px;width:100%">{{ $t('scenario_detail.add_child_step') }}</el-button>
          <div v-if="(node.nesting_level || 0) + 1 < maxNesting" style="display:flex;gap:8px;margin-top:6px">
            <el-button size="small" @click="emit('addContainer', { parent: node, type: 'condition' })" style="flex:1">{{ $t('scenario_detail.add_condition') }}</el-button>
            <el-button size="small" @click="emit('addContainer', { parent: node, type: 'loop' })" style="flex:1">{{ $t('scenario_detail.add_loop') }}</el-button>
          </div>
        </div>
      </div>
    </template>
  </VueDraggable>
</template>

<script setup lang="ts">
import { Close, ArrowUp, ArrowDown } from '@element-plus/icons-vue'
import VueDraggable from 'vuedraggable'
import type { ScenarioStepTree, StepExecState } from '@/types'

const props = defineProps<{
  steps: ScenarioStepTree[]
  nestingLevel: number
  apiNameMap: Record<string, string>
  stepExecStates?: Record<string, StepExecState>
  stepErrors?: Record<string, string[]>  // step_id → 错误类型列表，驱动内联校验警告图标
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: ScenarioStepTree[]): void
  (e: 'editStep', stepId: string): void
  (e: 'deleteStep', step: ScenarioStepTree): void
  (e: 'editContainer', container: ScenarioStepTree): void
  (e: 'deleteContainer', container: ScenarioStepTree): void
  (e: 'addStep', parent: ScenarioStepTree | void): void
  (e: 'addParallel', source: ScenarioStepTree): void
  (e: 'addContainer', arg: { parent: ScenarioStepTree; type: 'condition' | 'loop' }): void
}>()

// 最大嵌套深度限制
const maxNesting = 5
</script>

<style scoped>
/* 步骤 ID 重复时输入框橙色边框 */
.step-id-input.step-id-error :deep(.el-input__wrapper) {
  border-color: var(--el-color-warning);
  box-shadow: 0 0 0 1px var(--el-color-warning) inset;
}
/* 空 api_id 时文字变红 */
.text-danger {
  color: var(--el-color-danger);
}
</style>
