<!-- 通用通过/失败标签 —— 多页面共用 (apis/Detail, scenarios/Detail, executions/Detail, executions/Index, Dashboard) -->
<template>
  <el-tag :type="passed ? 'success' : 'danger'" :size="size">
    {{ passed ? t('common.pass') : t('common.fail') }}
  </el-tag>
</template>

<script setup>
import { useI18n } from 'vue-i18n'

defineProps({
  passed: { type: Boolean, required: true },
  size: { type: String, default: 'small' },
})

const { t } = useI18n()
</script>
