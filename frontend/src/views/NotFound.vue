<template>
  <el-result icon="warning" :title="$t('notFound.title')" :sub-title="$t('notFound.subtitle', { path: $route.fullPath })">
    <template #extra>
      <el-button type="primary" @click="$router.push('/dashboard')">{{ $t('notFound.back') }}</el-button>
    </template>
  </el-result>
</template>
