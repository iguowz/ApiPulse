import { ref, computed, type Ref } from 'vue'
import type { ScenarioStepTree } from '@/types'

/**
 * 场景步骤编辑的撤销/重做管理
 * 基于 steps 数组的 JSON 快照实现，最大 30 条历史记录
 *
 * 使用方式：
 *   const { canUndo, canRedo, pushSnapshot, undo, redo } = useScenarioHistory(steps)
 *   在每次修改 steps.value 后调用 pushSnapshot()
 */
export function useScenarioHistory(steps: Ref<ScenarioStepTree[]>) {
  const undoStack = ref<string[]>([])
  const redoStack = ref<string[]>([])
  const MAX_HISTORY = 30

  const canUndo = computed(() => undoStack.value.length > 0)
  const canRedo = computed(() => redoStack.value.length > 0)

  /** 保存当前 steps 快照到撤销栈，并清空重做栈 */
  function pushSnapshot() {
    const snapshot = JSON.stringify(steps.value)
    undoStack.value.push(snapshot)
    // 超过最大记录数时移除最旧的快照
    if (undoStack.value.length > MAX_HISTORY) {
      undoStack.value.shift()
    }
    // 新操作后清空重做栈（新操作使旧的重做路径失效）
    redoStack.value = []
  }

  /** 撤销：恢复上一个快照 */
  function undo() {
    if (!canUndo.value) return
    // 保存当前状态到重做栈
    redoStack.value.push(JSON.stringify(steps.value))
    // 恢复上一个快照
    const snapshot = undoStack.value.pop()!
    const restored = JSON.parse(snapshot) as ScenarioStepTree[]
    // 使用 splice 替换数组内容以保持响应式引用
    steps.value.splice(0, steps.value.length, ...restored)
  }

  /** 重做：恢复下一个快照 */
  function redo() {
    if (!canRedo.value) return
    // 保存当前状态到撤销栈
    undoStack.value.push(JSON.stringify(steps.value))
    // 恢复下一个快照
    const snapshot = redoStack.value.pop()!
    const restored = JSON.parse(snapshot) as ScenarioStepTree[]
    steps.value.splice(0, steps.value.length, ...restored)
  }

  return { canUndo, canRedo, pushSnapshot, undo, redo, undoStack, redoStack }
}
