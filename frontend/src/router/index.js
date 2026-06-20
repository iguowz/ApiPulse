import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/',              redirect: '/dashboard' },
  { path: '/login',         name: 'Login',        component: () => import('@/views/Login.vue'), meta: { guest: true } },
  { path: '/dashboard',     name: 'Dashboard',   component: () => import('@/views/Index.vue') },
  { path: '/apis',          name: 'Apis',         component: () => import('@/views/apis/Index.vue') },
  { path: '/apis/:id',      name: 'ApiDetail',    component: () => import('@/views/Detail.vue') },
  { path: '/mock-services', name: 'MockServices', component: () => import('@/views/mock-services/Index.vue') },
  { path: '/mock-services/:id', name: 'MockServiceDetail', component: () => import('@/views/mock-services/Index.vue') },
  { path: '/scenarios',     name: 'Scenarios',    component: () => import('@/views/scenarios/Index.vue') },
  { path: '/scenarios/:id', name: 'ScenarioDetail', component: () => import('@/views/scenarios/Detail.vue') },
  { path: '/executions',    name: 'Executions',   component: () => import('@/views/executions/Index.vue') },
  { path: '/executions/:id',name: 'ExecutionDetail', component: () => import('@/views/executions/Detail.vue') },
  { path: '/import-diffs',  name: 'ImportDiffs',   component: () => import('@/views/import-diffs/Index.vue') },
  { path: '/factory',       name: 'Factory',      component: () => import('@/views/factory/Index.vue') },
  { path: '/generations',   name: 'Generations',   component: () => import('@/views/generations/Index.vue') },
  { path: '/coverage',      name: 'Coverage',      component: () => import('@/views/coverage/Index.vue') },
  { path: '/knowledge',     name: 'Knowledge',     component: () => import('@/views/knowledge/Index.vue') },
  // 记忆页面已迁移至知识库 Tab（/knowledge?tab=memory 或直接切换标签页）
  { path: '/memory',        redirect: '/knowledge' },
  // 文档说明页：新标签页打开，无侧边栏全屏展示，需登录认证
  { path: '/docs',          name: 'Docs',          component: () => import('@/views/docs/Index.vue'), meta: { plain: true } },

  { path: '/monitor',       name: 'Monitor',      component: () => import('@/views/monitor/Index.vue') },
  // P2-7: 告警渠道管理已迁移至设置页面 (?tab=alert-channels)
  { path: '/admin/users',   name: 'AdminUsers',   component: () => import('@/views/admin/Users.vue'), meta: { admin: true } },
  { path: '/settings',      name: 'Settings',     component: () => import('@/views/settings/Index.vue') },
  { path: '/:pathMatch(.*)*', name: 'NotFound',    component: () => import('@/views/NotFound.vue') },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 路由守卫：未登录用户重定向到 /login（guest 页面无需登录）
// admin 路由额外要求管理员角色，非管理员重定向到 dashboard
router.beforeEach((to, _from, next) => {
  // 检查本地 token 是否存在（简化的认证判断，不含过期校验）
  const token = localStorage.getItem('aqp_token')

  if (to.meta.guest) {
    // 已登录用户访问 /login → 重定向到 dashboard
    if (token) return next('/dashboard')
    return next()
  }

  // 非 guest 页面：未登录时跳转 /login
  if (!token) {
    return next({ path: '/login', query: { redirect: to.fullPath } })
  }

  // admin 路由守卫：检查用户角色是否为 admin，非管理员重定向到 dashboard
  if (to.meta.admin) {
    try {
      const raw = localStorage.getItem('aqp_user')
      const user = raw ? JSON.parse(raw) : null
      if (!user || user.role !== 'admin') {
        return next('/dashboard')
      }
    } catch {
      // aqp_user 解析失败（数据损坏或格式异常）时安全降级，拒绝访问 admin 页面
      return next('/dashboard')
    }
  }

  next()
})

export default router
