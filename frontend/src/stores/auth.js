import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { authApi } from '@/api'
import { USER_ROLES, hasPermission as roleHasPermission, normalizeRole, normalizeUser } from '@/utils/rbac'

// 判断 JWT 是否已过期（base64url 解码 exp；非标准/无法解析则视为未过期，交由后端 401 兜底）
export function isTokenExpired(t) {
  if (!t) return true
  const parts = t.split('.')
  if (parts.length < 3) return false
  try {
    const payload = JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/')))
    if (!payload.exp) return false
    return payload.exp * 1000 < Date.now()
  } catch {
    return false
  }
}

// ── Auth store ──────────────────────────────────────────────
// 管理 JWT token、当前用户信息、登录状态
export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('aqp_token') || '')
  // 安全解析 localStorage 用户数据：数据损坏时回退为 null，避免应用启动崩溃
  let _parsedUser = null
  try { _parsedUser = JSON.parse(localStorage.getItem('aqp_user') || 'null') } catch { localStorage.removeItem('aqp_user') }
  _parsedUser = normalizeUser(_parsedUser)
  if (_parsedUser) localStorage.setItem('aqp_user', JSON.stringify(_parsedUser))
  const user = ref(_parsedUser)

  const isLoggedIn = computed(() => !!token.value && !!user.value && !isTokenExpired(token.value))
  const role = computed(() => normalizeRole(user.value?.role))
  const isAdmin = computed(() => role.value === USER_ROLES.ADMIN)
  const canManageUsers = computed(() => roleHasPermission(role.value, 'user:manage'))
  const currentProjectId = computed(() => user.value?.project_id || 'default')

  function _save(t, u) {
    const normalizedUser = normalizeUser(u)
    token.value = t
    user.value = normalizedUser
    localStorage.setItem('aqp_token', t)
    localStorage.setItem('aqp_user', JSON.stringify(normalizedUser))
  }

  function _clear() {
    token.value = ''
    user.value = null
    localStorage.removeItem('aqp_token')
    localStorage.removeItem('aqp_user')
  }

  async function login(username, password) {
    const res = await authApi.login(username, password)
    _save(res.access_token, res.user)
    return user.value
  }

  async function register(data) {
    return await authApi.register(data)
  }

  function logout() {
    _clear()
  }

  async function fetchMe() {
    try {
      const u = await authApi.me()
      const normalizedUser = normalizeUser(u)
      user.value = normalizedUser
      localStorage.setItem('aqp_user', JSON.stringify(normalizedUser))
      return normalizedUser
    } catch {
      // token 过期或无效时清除本地凭证，防止后续请求携带过期 token
      _clear()
      return null
    }
  }

  async function listUsers() {
    return await authApi.listUsers()
  }

  async function updateUser(id, data) {
    return await authApi.updateUser(id, data)
  }

  async function deleteUser(id) {
    return await authApi.deleteUser(id)
  }

  async function changePassword(oldPassword, newPassword) {
    return await authApi.changePassword(oldPassword, newPassword)
  }

  function hasPermission(permission) {
    return roleHasPermission(role.value, permission)
  }

  return { token, user, isLoggedIn, role, isAdmin, canManageUsers, currentProjectId, hasPermission,
           login, register, logout, fetchMe, listUsers, updateUser, deleteUser, changePassword }
})
