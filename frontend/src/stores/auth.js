import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { authApi } from '@/api'

// ── Auth store ──────────────────────────────────────────────
// 管理 JWT token、当前用户信息、登录状态
export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('aqp_token') || '')
  // 安全解析 localStorage 用户数据：数据损坏时回退为 null，避免应用启动崩溃
  let _parsedUser = null
  try { _parsedUser = JSON.parse(localStorage.getItem('aqp_user') || 'null') } catch { localStorage.removeItem('aqp_user') }
  const user = ref(_parsedUser)

  const isLoggedIn = computed(() => !!token.value && !!user.value)
  const isAdmin = computed(() => user.value?.role === 'admin')
  const currentProjectId = computed(() => user.value?.project_id || 'default')

  function _save(t, u) {
    token.value = t
    user.value = u
    localStorage.setItem('aqp_token', t)
    localStorage.setItem('aqp_user', JSON.stringify(u))
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
    return res.user
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
      user.value = u
      localStorage.setItem('aqp_user', JSON.stringify(u))
      return u
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

  return { token, user, isLoggedIn, isAdmin, currentProjectId,
           login, register, logout, fetchMe, listUsers, updateUser, deleteUser, changePassword }
})
