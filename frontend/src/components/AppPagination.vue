<!-- 通用分页组件 —— 4 个列表页 (apis/scenarios/executions/monitor) 共用 -->
<template>
  <div v-if="total > pageSize" class="app-pagination">
    <el-pagination
      :model-value="page"
      :page-size="pageSize"
      :total="total"
      layout="prev, pager, next, total"
      background
      small
      @update:model-value="onPageChange"
    />
  </div>
</template>

<script setup>
const props = defineProps({
  page: { type: Number, required: true },
  pageSize: { type: Number, required: true },
  total: { type: Number, required: true },
})

const emit = defineEmits(['update:page', 'page-change'])

// 先 emit update:page 更新父组件 v-model，再 emit page-change 触发 load
function onPageChange(newPage) {
  emit('update:page', newPage)
  emit('page-change', newPage)
}
</script>

<style scoped>
.app-pagination {
  padding: 12px 0;
  display: flex;
  justify-content: center;
}
</style>
