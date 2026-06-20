<template>
  <!-- 登录/注册等 guest 页面全屏展示，无侧边栏 -->
  <div v-if="route.meta.guest" class="guest-layout">
    <RouterView />
  </div>

  <!-- plain 页面（如文档页）：全屏展示，无侧边栏，需登录 -->
  <div v-else-if="route.meta.plain" class="plain-layout">
    <RouterView />
  </div>

  <div v-else class="app-layout">
    <aside class="sidebar" @focusout="onSidebarFocusOut">
      <!-- 隐藏焦点锚点：设置中关闭"导航栏自动收起"时获取焦点，触发 :focus-within 保持侧边栏展开 -->
      <div ref="sidebarFocusAnchor" tabindex="-1" aria-hidden="true" class="focus-anchor"></div>
      <div class="sidebar-logo">
        <span class="logo-mark">▸</span>
        <span class="logo-text">AQP</span>
        <!-- 版本号点击新标签页打开文档说明页 -->
        <a href="/docs" target="_blank" rel="noopener" class="sidebar-version" :title="$t('docs.title')">{{ $t('app.version') }}</a>
      </div>

      <!-- Element Plus 菜单：vertical + router 模式，自动根据路由高亮 -->
      <el-menu
        :default-active="route.path"
        router
        :background-color="'transparent'"
        :text-color="'var(--text-2)'"
        :active-text-color="'var(--accent)'"
        class="sidebar-menu"
      >
        <el-menu-item v-for="item in navItems" :key="item.to" :index="item.to">
          <el-tooltip :content="item.desc" placement="right" :show-after="500">
            <template #default>
              <div class="nav-row">
                <span class="nav-icon" v-html="item.icon"></span>
                <span class="nav-label">{{ item.label }}</span>
                <span v-if="item.to === '/import-diffs' && pendingDiffCount > 0" class="nav-badge">{{ pendingDiffCount }}</span>
              </div>
            </template>
          </el-tooltip>
        </el-menu-item>
      </el-menu>

      <div class="sidebar-bottom">
        <!-- 项目选择器 -->
        <div class="project-selector">
          <div class="project-label">
            {{ $t('common.project') }}
            <!-- 当前项目启用了域名过滤时显示状态徽章 -->
            <span v-if="currentProjectFilterActive" class="filter-badge" :title="$t('common.domainFilterHint')">
              <svg viewBox="0 0 16 16" fill="currentColor" width="10" height="10"><path d="M1 2h14l-5.5 7.5V13l-3-1.5v-2L1 2z"/></svg>
              {{ $t('common.filtering') }}
            </span>
          </div>
          <div class="project-select-row">
            <el-select
              :model-value="projectStore.current"
              @update:model-value="projectStore.select"
              size="small"
              class="project-select"
            >
              <el-option v-for="p in projectStore.projects" :key="p.id" :value="p.id" :label="p.name" />
            </el-select>
            <el-button size="small" @click="router.push('/settings?tab=projects')" :title="$t('common.newProject')">
              <el-icon><Plus /></el-icon>
            </el-button>
          </div>
          <!-- 项目列表为空时引导用户创建项目，使用 i18n-t 组件插值 link 插槽 -->
          <i18n-t v-if="!projectStore.projects.length" keypath="common.noProject" tag="div" class="project-empty-hint">
            <template #link>
              <a href="#" @click.prevent="router.push('/settings?tab=projects')">{{ $t('common.newProjectLink') }}</a>
            </template>
          </i18n-t>
        </div>
        <!-- 执行环境选择器：用户可快速切换 API/场景执行时的目标环境，侧边栏收起时隐藏 -->
        <div class="sidebar-ctrl-row sidebar-ctrl-env">
          <span class="sidebar-ctrl-label">{{ $t('settings.environments') }}</span>
          <el-select v-model="selectedEnvId" size="small" class="project-select"
            :placeholder="$t('settings.exec_env_placeholder')"
            @change="onEnvChange">
            <el-option value="" :label="$t('settings.exec_env_placeholder')" />
            <el-option v-for="env in environments" :key="env.id" :value="env.id" :label="env.name" />
          </el-select>
        </div>
        <!-- 日夜主题切换：收起时显示纯图标，展开时显示 el-switch -->
        <div class="sidebar-ctrl-row">
          <span class="sidebar-ctrl-label">{{ $t('common.theme') }}</span>
          <!-- 收起时只显示当前主题图标，点击切换 -->
          <span class="sidebar-theme-icon" @click="onThemeIconClick" :title="$t('common.theme')">
            <el-icon :size="16"><component :is="theme === 'dark' ? Moon : Sunny" /></el-icon>
          </span>
          <!-- 展开时显示完整开关 -->
          <el-switch
            class="sidebar-theme-switch"
            v-model="isDark"
            size="small"
            @change="onThemeChange"
            :active-icon="Moon"
            :inactive-icon="Sunny"
          />
        </div>
        <!-- 当前登录用户 -->
        <div v-if="authStore.isLoggedIn" class="sidebar-user">
          <div class="sidebar-user-info">
            <span class="sidebar-user-avatar">{{ (authStore.user?.username || 'U')[0].toUpperCase() }}</span>
            <div class="sidebar-user-meta">
              <span class="sidebar-user-name">{{ authStore.user?.username }}</span>
              <span v-if="authStore.isAdmin" class="sidebar-user-role">{{ $t('auth.role_admin') }}</span>
            </div>
          </div>
          <el-button size="small" text @click="handleLogout" :title="$t('auth.logout')">
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" width="14" height="14"><path d="M6 2H3a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3M11 11l4-3-4-3M15 8H6"/></svg>
          </el-button>
        </div>

      </div>
    </aside>

    <main class="main-area">
      <RouterView />
    </main>

    <!-- P1-3: 全局 AI 助手浮窗（Cmd/Ctrl+K 唤起） -->
    <AiAssistant />

    <!-- 需求1: 项目创建/编辑弹窗已移至 Settings > 项目管理 -->
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, provide, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Plus, Sunny, Moon } from '@element-plus/icons-vue'
import { useProjectStore, useToastStore } from '@/stores'
import { useAuthStore } from '@/stores/auth'
import { environmentApi, importDiffApi, openWs } from '@/api'
import AiAssistant from '@/components/AiAssistant.vue'  // P1-3: AI 助手浮窗

const { t } = useI18n()

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()
const toastStore   = useToastStore()
const authStore    = useAuthStore()

// 当前项目是否启用了域名过滤（白名单或黑名单至少一个非空）
// 同时检查两个列表：任一非空即认为过滤生效，侧边栏显示过滤状态徽章
const currentProjectFilterActive = computed(() => {
  const proj = projectStore.projects.find(p => p.id === projectStore.current)
  if (!proj) return false // 项目不存在时无过滤状态
  const allow = proj.domain_allowlist || []
  const block = proj.domain_blocklist || []
  return allow.length > 0 || block.length > 0
})

// ── 日夜主题切换：默认暗色，偏好存入 localStorage ──
const theme = ref(localStorage.getItem('apipulse-theme') || 'dark')

function applyTheme(t) {
  document.documentElement.setAttribute('data-theme', t)
  localStorage.setItem('apipulse-theme', t)
}

// el-switch v-model 双向绑定：getter 从 theme 读取，setter 写入并应用主题
const isDark = computed({
  get: () => theme.value === 'dark',
  set: (v) => { theme.value = v ? 'dark' : 'light'; applyTheme(theme.value) }
})

// el-switch 切换主题时的回调（v-model 已通过 computed setter 处理，此处为安全冗余）
function onThemeChange(val) {
  applyTheme(val ? 'dark' : 'light')
}

// 收起时点击主题图标直接切换：取反当前 dark/light 状态并应用
function onThemeIconClick() {
  const next = theme.value === 'dark' ? 'light' : 'dark'
  theme.value = next
  applyTheme(next)
}

// ── 执行环境选择器 ────────────────────────────────
const environments = ref([])
const selectedEnvId = ref(localStorage.getItem('apipulse-env') || '')
// 未处理差异数量（侧边栏徽章用）
const pendingDiffCount = ref(0)

async function loadPendingDiffCount() {
  try {
    const res = await importDiffApi.count({ project_id: projectStore.current, status: 'pending' })
    pendingDiffCount.value = res.total || 0
  } catch {
    // 接口异常时静默降级为 0，不阻塞侧边栏渲染
    pendingDiffCount.value = 0
  }
}

// 通过 provide 让子页面感知当前选中的环境
provide('selectedEnvId', selectedEnvId)

async function loadEnvironments(projectId) {
  try {
    environments.value = await environmentApi.list(projectId)
  } catch (e) {
    console.error('Failed to load environments:', e)
    toastStore.error('加载环境列表失败: ' + (e.message || 'environments'))
    environments.value = []
  }
}

function onEnvChange(val) {
  localStorage.setItem('apipulse-env', val || '')
}

// 项目切换时重新加载环境列表
watch(() => projectStore.current, (newProjectId) => {
  loadEnvironments(newProjectId)
  loadPendingDiffCount()
})

// ── 导航栏自动收起开关（设置 → 通用）──
// 开启（默认）：导航后 blur 焦点，侧边栏自动收起
// 关闭：侧边栏保持展开（通过隐藏焦点锚点持续触发 :focus-within）
const sidebarFocusAnchor = ref(null)
const autoCollapseNav = ref(localStorage.getItem('apipulse-nav-auto-collapse') !== 'false')

// 导航切换后移除侧边栏内焦点，使 :focus-within 失效，侧边栏自动收起
watch(() => route.path, () => {
  if (!autoCollapseNav.value) return
  const el = document.activeElement
  if (el && el.closest('.sidebar')) el.blur()
})

// 关闭"自动收起"时给隐藏锚点焦点，触发 :focus-within 保持侧边栏展开
function pinSidebar() {
  nextTick(() => sidebarFocusAnchor.value?.focus())
}
function unpinSidebar() {
  sidebarFocusAnchor.value?.blur()
}
watch(autoCollapseNav, (enabled) => {
  if (enabled) unpinSidebar()
  else pinSidebar()
}, { immediate: true })

// 侧边栏 pinned 时，焦点离开则重新聚焦锚点以保持 :focus-within
function onSidebarFocusOut() {
  if (autoCollapseNav.value) return
  nextTick(() => {
    // 焦点已完全离开侧边栏 → 重新聚焦锚点
    if (!autoCollapseNav.value) {
      const active = document.activeElement
      if (!active || !active.closest('.sidebar')) {
        sidebarFocusAnchor.value?.focus()
      }
    }
  })
}

// 需求1: 项目创建/编辑功能已移至 Settings > 项目管理

// 巡检告警 WebSocket 引用 —— 在 setup 阶段同步注册 onUnmounted 清理，避免
// await 之后注册生命周期钩子导致 Vue 警告（onUnmounted is called without active instance）
const monitorWs = ref(null)
onUnmounted(() => {
  if (monitorWs.value?.terminate) monitorWs.value.terminate()
})

onMounted(async () => {
  // 页面加载时应用保存的主题偏好
  applyTheme(theme.value)
  await projectStore.load()
  // 需求1: 无项目时引导用户前往 Settings > 项目管理创建项目
  if (!projectStore.current) {
    if (projectStore.projects.length) {
      projectStore.select(projectStore.projects[0].id)
    } else {
      // 无项目时导航到设置页面的项目管理标签页，引导用户创建
      router.push('/settings?tab=projects')
    }
  }
  loadEnvironments(projectStore.current)
  loadPendingDiffCount()
  // 连接巡检告警 WebSocket，实时推送告警通知（清理已在 setup 阶段通过 onUnmounted 注册）
  monitorWs.value = openWs(`/monitor?project_id=${encodeURIComponent(projectStore.current || 'default')}`, (data) => {
    // 仅对真实告警记录（有 id + monitor_id）显示 toast 通知，
    // 过滤 WS 控制消息（monitor_generation/monitor_run/alert_assessment 等，无 title 字段，不应触发 toast）
    if (data.id && data.monitor_id && data.title) {
      if (data.is_recovery) {
        toastStore.success(`${t('alert.recovery')} ${data.title}`)
      } else {
        toastStore.info(`${t('alert.alert')} ${data.title}`)
      }
    }
  })
})

// 侧边栏导航定义：按用户工作流排列（总览 → 资产 → 编排/造数/模拟 → 执行/巡检 → 审核/变更/知识 → 配置）
// 覆盖度已作为 Dashboard 标签页嵌入，不再单独出现在导航中
const nav = [
  { to: '/dashboard',   labelKey: 'nav.dashboard',   descKey: 'navDesc.dashboard',   icon: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="1" y="1" width="6" height="6" rx="1"/><rect x="9" y="1" width="6" height="6" rx="1"/><rect x="1" y="9" width="6" height="6" rx="1"/><rect x="9" y="9" width="6" height="6" rx="1"/></svg>' },
  { to: '/apis',        labelKey: 'nav.apis',        descKey: 'navDesc.apis',        icon: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2 4h12M2 8h8M2 12h5"/></svg>' },
  { to: '/scenarios',   labelKey: 'nav.scenarios',   descKey: 'navDesc.scenarios',   icon: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="3" cy="8" r="2"/><circle cx="13" cy="4" r="2"/><circle cx="13" cy="12" r="2"/><path d="M5 8h3l3-4M8 8h2l3 4"/></svg>' },
  { to: '/factory',     labelKey: 'nav.factory',     descKey: 'navDesc.factory',     icon: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M1 12V8l4-2 4 2 4-2v4l-4 2-4-2-4 2z"/></svg>' },
  { to: '/mock-services', labelKey: 'nav.mockServices', descKey: 'navDesc.mockServices', icon: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="3" width="12" height="10" rx="2"/><path d="M5 7h6M5 10h3"/><path d="M11 1v3M5 1v3"/></svg>' },
  { to: '/executions',  labelKey: 'nav.executions',  descKey: 'navDesc.executions',  icon: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="1" y="2" width="14" height="12" rx="2"/><path d="M5 6h6M5 9h4"/></svg>' },
  { to: '/monitor',     labelKey: 'nav.monitor',     descKey: 'navDesc.monitor',     icon: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M1 8h2l2-4 3 8 2-5 2 3 1-2h2"/></svg>' },
  // P2-7: 告警渠道管理已迁移至设置页面 (?tab=alert-channels)
  { to: '/generations', labelKey: 'nav.generations', descKey: 'navDesc.generations', icon: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="6"/><path d="M6 8l1.5 1.5L10 7"/></svg>' },
  { to: '/import-diffs', labelKey: 'nav.importDiffs', descKey: 'navDesc.importDiffs', icon: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="6"/><path d="M8 4v4l2.5 2.5"/></svg>' },
  { to: '/knowledge',   labelKey: 'nav.knowledge',  descKey: 'navDesc.knowledge',  icon: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2 14V6l4-4 3 3 5-3v12H2z"/><rect x="5" y="9" width="2" height="5"/><rect x="9" y="7" width="2" height="7"/></svg>' },
  // 管理员专属：用户管理导航项，仅 admin 角色可见
  { to: '/admin/users', labelKey: 'nav.adminUsers', adminOnly: true, descKey: 'navDesc.adminUsers', icon: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="6" cy="5" r="2.5"/><path d="M1 14c0-3 2.2-5 5-5s5 2 5 5"/><circle cx="12" cy="7" r="1.5"/><path d="M9 14c0-2 1.3-3.5 3-3.5"/></svg>' },
  { to: '/settings',    labelKey: 'nav.settings',    descKey: 'navDesc.settings',    icon: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="2.5"/><path d="M8 1v2M8 13v2M3.05 3.05l1.41 1.41M11.54 11.54l1.41 1.41M1 8h2M13 8h2M3.05 12.95l1.41-1.41M11.54 4.46l1.41-1.41"/></svg>' },
]

// 根据当前语言动态生成导航标签与提示文案，adminOnly 菜单项仅对管理员可见
const navItems = computed(() =>
  nav
    .filter(item => !item.adminOnly || authStore.isAdmin)
    .map(item => ({ ...item, label: t(item.labelKey), desc: t(item.descKey) }))
)

// 退出登录：清除 auth store → 跳转登录页
function handleLogout() {
  authStore.logout()
  router.push('/login')
}
</script>

<style scoped>
/* 隐藏焦点锚点：不可见、不可交互，仅用于触发 :focus-within */
.focus-anchor {
  position: absolute;
  width: 0; height: 0;
  opacity: 0;
  pointer-events: none;
  overflow: hidden;
  outline: none;
}

/* guest 页面（登录/注册）全屏布局 */
.guest-layout {
  width: 100vw;
  height: 100vh;
}

/* plain 页面（文档页等）全屏布局，无侧边栏 */
.plain-layout {
  width: 100vw;
  min-height: 100vh;
}

/* ElMenu 在暗色/亮色主题下适配 */
.sidebar-menu {
  flex: 1;
  overflow-y: auto;
  border-right: none !important;
  padding: 8px;
}
.sidebar-menu :deep(.el-menu-item) {
  border-radius: var(--radius);
  margin-bottom: 2px;
  height: 38px;
  line-height: 38px;
  padding: 0 14px !important;
}
.sidebar-menu :deep(.el-menu-item:hover) {
  background: var(--bg-hover) !important;
}
.sidebar-menu :deep(.el-menu-item.is-active) {
  background: rgba(79,142,247,.12) !important;
}

.nav-row {
  display: flex; align-items: center; width: 100%;
  min-width: 176px;
}
.nav-icon {
  width: 16px; height: 16px; flex-shrink: 0;
  display: inline-flex; align-items: center;
  justify-content: center;
}
.nav-label {
  margin-left: 10px;
  white-space: nowrap;
  opacity: 0;
  transform: translateX(-4px);
  transition: opacity .12s ease, transform .12s ease;
}
.sidebar:hover .nav-label,
.sidebar:focus-within .nav-label {
  opacity: 1;
  transform: translateX(0);
}

/* 导入差异未读数量徽章 */
.nav-badge {
  margin-left: auto;
  background: var(--accent);
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  min-width: 18px;
  height: 18px;
  line-height: 18px;
  text-align: center;
  border-radius: 9px;
  padding: 0 5px;
}
.nav-icon :deep(svg) { width: 16px; height: 16px; }

/* 版本号链接样式：可点击跳转文档页 */
.sidebar-version {
  color: var(--text-3);
  text-decoration: none;
  cursor: pointer;
  transition: color .15s ease;
}
.sidebar-version:hover {
  color: var(--accent);
  text-decoration: underline;
}

/* 侧边栏收起时，大块容器折叠高度，小元素仅透明度隐藏，保证 avatar/主题图标始终可见 */

/* 大块容器：折叠高度不占空间 */
.project-selector,
.sidebar-ctrl-env {
  opacity: 0;
  pointer-events: none;
  max-height: 0;
  overflow: hidden;
  margin-top: 0;
  margin-bottom: 0;
  transition: opacity .12s ease, max-height .12s ease, margin .12s ease;
}
.sidebar:hover .project-selector,
.sidebar:hover .sidebar-ctrl-env,
.sidebar:focus-within .project-selector,
.sidebar:focus-within .sidebar-ctrl-env {
  opacity: 1;
  pointer-events: auto;
  max-height: 120px;
  margin-top: revert;
  margin-bottom: revert;
}

/* 小元素：折叠时宽高归零不占空间，保证 avatar/主题图标始终可见 */
.logo-text,
.sidebar-version,
.sidebar-ctrl-label,
.sidebar-user-meta,
.sidebar-user .el-button {
  opacity: 0;
  pointer-events: none;
  max-width: 0;           /* 折叠时不占 flex 空间 */
  max-height: 0;          /* 折叠时不占垂直空间，避免文字换行撑高容器 */
  overflow: hidden;
  transition: opacity .12s ease, max-width .12s ease;
}
/* el-button 收起时必须归零内边距，否则 min-content 仍会抢占 flex 空间挤压头像 */
.sidebar-user .el-button {
  padding: 0; margin: 0; min-width: 0;
}
.sidebar:hover .logo-text,
.sidebar:hover .sidebar-version,
.sidebar:hover .sidebar-ctrl-label,
.sidebar:hover .sidebar-user-meta,
.sidebar:hover .sidebar-user .el-button,
.sidebar:focus-within .logo-text,
.sidebar:focus-within .sidebar-version,
.sidebar:focus-within .sidebar-ctrl-label,
.sidebar:focus-within .sidebar-user-meta,
.sidebar:focus-within .sidebar-user .el-button {
  opacity: 1;
  pointer-events: auto;
  max-width: 200px;        /* 展开时给足够宽度 */
  max-height: 48px;        /* 展开时恢复垂直空间 */
}
/* 展开时恢复 el-button 默认内边距 */
.sidebar:hover .sidebar-user .el-button,
.sidebar:focus-within .sidebar-user .el-button {
  padding: revert; margin: revert;
}

.project-label {
  font-size: 10px; color: var(--text-3);
  text-transform: uppercase; letter-spacing: .08em; margin-bottom: 6px;
}
.project-select-row { display: flex; gap: 4px; align-items: center; margin-bottom: 6px; }
.project-select-row .project-select { flex: 1; min-width: 0; }
.project-select {
  --el-select-width: 100%;
}

/* 项目列表为空时的引导提示 */
.project-empty-hint { font-size: 11px; color: var(--text-3); margin-top: 6px; text-align: center; }
.project-empty-hint a { color: var(--accent); cursor: pointer; text-decoration: none; }
.project-empty-hint a:hover { text-decoration: underline; }

/* 侧边栏控制行（语言/主题/环境） */
.sidebar-ctrl-row {
  display: flex; align-items: center; justify-content: center;  /* 收起时居中，保证主题开关可见 */
  margin-bottom: 6px;
}
.sidebar:hover .sidebar-ctrl-row,
.sidebar:focus-within .sidebar-ctrl-row {
  justify-content: space-between;   /* 展开时两端对齐 */
}
.sidebar-ctrl-label {
  font-size: 10px; color: var(--text-3);
  text-transform: uppercase; letter-spacing: .06em;
  white-space: nowrap; /* 防止中文标签换行（如"执行环境"4字） */
  flex-shrink: 0; /* 标签不被压缩，select 占剩余空间 */
}

/* 收起时显示纯主题图标，点击可切换 */
.sidebar-theme-icon {
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; color: var(--text-3); flex-shrink: 0;
  transition: opacity .12s ease, max-width .12s ease;
  max-width: 24px; overflow: hidden;
}
.sidebar-theme-icon:hover { color: var(--accent); }

/* 展开时隐藏纯图标，显示开关 */
.sidebar:hover .sidebar-theme-icon,
.sidebar:focus-within .sidebar-theme-icon {
  opacity: 0; max-width: 0; pointer-events: none;
}

/* 展开时显示开关，收起时隐藏 */
.sidebar-theme-switch {
  opacity: 0; max-width: 0; overflow: hidden; flex-shrink: 0;
  transition: opacity .12s ease, max-width .12s ease;
}
.sidebar:hover .sidebar-theme-switch,
.sidebar:focus-within .sidebar-theme-switch {
  opacity: 1; max-width: 100px;
}

/* 域名过滤状态徽章 */
.filter-badge {
  display: inline-flex; align-items: center; gap: 3px;
  font-size: 9px; font-weight: 600; color: var(--green);
  background: rgba(62,207,142,.12); border-radius: 3px;
  padding: 1px 5px; margin-left: 4px; vertical-align: middle; cursor: help;
}
.filter-badge svg { flex-shrink: 0; }

/* 侧边栏底部容器：收起时减小横向留白，为头像/图标腾出更多空间 */
.sidebar-bottom {
  padding: 8px 6px;          /* 收起：6*2=12px padding, 48-12=36px 内容区 */
  transition: padding .18s ease;
}
.sidebar:hover .sidebar-bottom,
.sidebar:focus-within .sidebar-bottom {
  padding: 12px 10px;        /* 展开恢复正常内边距 */
}

/* 侧边栏底部当前登录用户 */
.sidebar-user {
  display: flex; align-items: center; justify-content: space-between;
  width: 36px;              /* 收起：48侧边栏 - 12px padding = 36px */
  padding: 4px; margin-bottom: 6px;
  border-radius: var(--radius);
  background: var(--bg-hover);
  transition: width .18s ease, padding .18s ease;
}
.sidebar:hover .sidebar-user,
.sidebar:focus-within .sidebar-user {
  width: calc(var(--sidebar-expanded) - 20px);
  padding: 8px 10px;
}
/* sidebar-user-info：不使用 gap，通过 margin 控制间距，折叠时 meta 宽度归零不占用空间 */
.sidebar-user-info { display: flex; align-items: center; overflow: hidden; }
.sidebar-user-avatar {
  width: 28px; height: 28px; flex-shrink: 0;
  border-radius: 50%;
  background: var(--accent);
  color: #fff; font-size: 12px; font-weight: 700;
  display: inline-flex; align-items: center; justify-content: center;
  transition: width .18s ease, height .18s ease;
}
/* 收起时缩小头像，确保在 36px 容器内完整可见 */
.sidebar .sidebar-user-avatar {
  width: 24px; height: 24px;
}
.sidebar:hover .sidebar-user-avatar,
.sidebar:focus-within .sidebar-user-avatar {
  width: 28px; height: 28px;
}
.sidebar-user-meta { display: flex; flex-direction: column; overflow: hidden; margin-left: 8px; }
.sidebar-user-name {
  font-size: 13px; color: var(--text-1); font-weight: 500;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.sidebar-user-role {
  font-size: 10px; color: var(--accent); font-weight: 600;
  text-transform: uppercase; letter-spacing: .04em;
}

/* Dialog 适配暗色主题 */
:deep(.el-dialog) {
  --el-dialog-bg-color: var(--bg-2);
  --el-dialog-border-color: var(--border-2);
  border-radius: var(--radius-lg);
}
</style>
