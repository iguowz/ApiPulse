import { ref, h } from 'vue'
import { defineStore } from 'pinia'
import { ElMessage } from 'element-plus'
import { projectApi } from '@/api'

// 从 auth.js 重新导出 useAuthStore，供组件从 @/stores 统一引入
export { useAuthStore } from './auth'

// ── Project store ──────────────────────────────────────────
export const useProjectStore = defineStore('project', () => {
  const projects = ref([])
  const current = ref(localStorage.getItem('aqp_project') || '')

  async function load() {
    try {
      projects.value = await projectApi.list()
      // 校验 current 是否有效：当 current 为空或不在项目列表中时，自动选中第一个项目
      if (projects.value.length && (!current.value || !projects.value.find(p => p.id === current.value))) {
        current.value = projects.value[0].id
        localStorage.setItem('aqp_project', current.value)
      }
    } catch (e) {
      console.error('Failed to load projects:', e)
      ElMessage.error(e.message || '加载项目列表失败')
      projects.value = []
    }
  }

  function select(id) {
    current.value = id
    localStorage.setItem('aqp_project', id)
  }

  async function create(data) {
    const p = await projectApi.create(data)
    await load()
    select(p.id)
    return p
  }

  // 根据 project_id 查找项目名称，未找到时返回空字符串
  function getName(id) {
    if (!id) return ''
    const p = projects.value.find(x => x.id === id)
    return p ? p.name : ''
  }

  return { projects, current, load, select, create, getName }
})

// Toast store —— 内部使用 Element Plus ElMessage，保持 API 兼容
// 需要 ElMessage 的全局样式已在 main.js 中通过 element-plus/dist/index.css 引入
export const useToastStore = defineStore('toast', () => {
  function success(msg) { ElMessage.success(msg) }
  function error(msg)   { ElMessage.error(msg) }
  function info(msg)    { ElMessage.info(msg) }

  // 带操作按钮的 toast：消息 + 可点击的操作链接（延长显示时间 10s）
  function successAction(msg, actionLabel, onClick) {
    ElMessage({
      type: 'success',
      duration: 10000,
      message: h('div', { style: 'display:flex;align-items:center;gap:8px' }, [
        h('span', msg),
        h('span', {
          style: 'color:var(--el-color-primary);cursor:pointer;font-weight:600;white-space:nowrap',
          onClick: (e) => { e.stopPropagation(); onClick() }
        }, actionLabel)
      ])
    })
  }

  return { success, error, info, successAction }
})
