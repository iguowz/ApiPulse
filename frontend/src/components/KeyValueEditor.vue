<template>
  <div class="kv-editor">
    <div v-for="(row, i) in rows" :key="i" class="kv-row">
      <el-input
        v-model="row.key"
        :placeholder="keyPlaceholder || $t('kv_editor.key_placeholder')"
        size="small"
        class="kv-key"
        @change="emitChange"
      />
      <el-input
        v-model="row.value"
        :placeholder="valuePlaceholder || $t('kv_editor.value_placeholder')"
        size="small"
        class="kv-value"
        @change="emitChange"
      />
      <el-button
        size="small"
        :icon="Delete"
        circle
        class="kv-delete"
        @click="removeRow(i)"
      />
    </div>
    <el-button size="small" :icon="Plus" @click="addRow" class="kv-add">
      {{ addText || $t('kv_editor.add_row') }}
    </el-button>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { Delete, Plus } from '@element-plus/icons-vue'

const { t } = useI18n()

interface KvRow { key: string; value: string }

const props = withDefaults(defineProps<{
  modelValue: Record<string, any>
  keyPlaceholder?: string
  valuePlaceholder?: string
  addText?: string
}>(), {
  modelValue: () => ({}),
})

const emit = defineEmits<{
  (e: 'update:modelValue', value: Record<string, any>): void
}>()

// 将对象转换为行数组，过滤掉非字符串值
function objToRows(obj: Record<string, any>): KvRow[] {
  const result: KvRow[] = []
  for (const [k, v] of Object.entries(obj || {})) {
    // 仅展示字符串/数字/布尔类型的简单键值对
    if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') {
      result.push({ key: k, value: String(v) })
    }
  }
  // 确保编辑器始终至少有一行空行，避免空白编辑界面
  return result.length ? result : [{ key: '', value: '' }]
}

// 将行数组转换回对象，跳过空键
function rowsToObj(rows: KvRow[]): Record<string, any> {
  const obj: Record<string, any> = {}
  for (const row of rows) {
    if (row.key.trim()) {
      obj[row.key.trim()] = row.value
    }
  }
  return obj
}

const rows = ref<KvRow[]>(objToRows(props.modelValue))

function emitChange() {
  emit('update:modelValue', rowsToObj(rows.value))
}

function addRow() {
  rows.value.push({ key: '', value: '' })
}

function removeRow(i: number) {
  rows.value.splice(i, 1)
  // 删除后若无剩余行，补齐一个空行以保持编辑器可用
  if (rows.value.length === 0) {
    rows.value.push({ key: '', value: '' })
  }
  emitChange()
}

// 外部 modelValue 变化时同步回 rows
watch(() => props.modelValue, (val) => {
  // 避免自触发循环：仅当外部值确实不同时才同步
  const newRows = objToRows(val || {})
  const currentObj = rowsToObj(rows.value)
  const newObj = rowsToObj(newRows)
  if (JSON.stringify(currentObj) !== JSON.stringify(newObj)) {
    rows.value = newRows
  }
}, { deep: true })
</script>

<style scoped>
.kv-editor { display: flex; flex-direction: column; gap: 6px; }
.kv-row   { display: flex; gap: 6px; align-items: center; }
.kv-key   { flex: 2; }
.kv-value { flex: 3; }
.kv-delete { flex-shrink: 0; }
.kv-add   { align-self: flex-start; margin-top: 4px; }
</style>
