<!-- 通用 API 分析状态标签 —— apis/Index, Detail 共用 -->
<template>
  <!-- failed 状态特殊处理：悬浮展示具体错误信息，帮助用户快速定位失败原因 -->
  <el-tag v-if="status === 'failed'" :type="tagType" :size="size">
    <el-tooltip :content="error || t(`${prefix}.status_failed`)" placement="top" :disabled="!error">
      <span>{{ t(`${prefix}.status_failed`) }}</span>
    </el-tooltip>
  </el-tag>
  <el-tag v-else :type="tagType" :size="size">
    {{ t(`${prefix}.status_${status || 'idle'}`) }}
  </el-tag>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  status: { type: String, default: 'idle' },
  error: { type: String, default: '' },
  size: { type: String, default: 'small' },
  // i18n key 前缀：Detail.vue 用 'analysis', apis/Index.vue 用 'apis'
  prefix: { type: String, default: 'analysis' },
})

const { t } = useI18n()

// 状态 → el-tag type 映射：applied/done=success, pending_review=warning, running=warning, queued=info, failed=danger
const tagType = computed(() => {
  const map = { applied: 'success', done: 'success', pending_review: 'warning', running: 'warning', queued: 'info', failed: 'danger' }
  return map[props.status] || 'info'
})
</script>
