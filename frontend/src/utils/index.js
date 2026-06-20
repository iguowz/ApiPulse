// ── Formatters ──────────────────────────────────────────────
export const fmt = {
  duration(ms) {
    if (ms == null) return '—'
    if (ms < 1000) return `${ms}ms`
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
    const m = Math.floor(ms / 60000)
    const s = Math.round((ms % 60000) / 1000)
    return `${m}m ${s}s`
  },

  fromNow(date) {
    if (!date) return '—'
    const diff = Date.now() - new Date(date).getTime()
    const sec = Math.floor(diff / 1000)
    if (sec < 60) return `${sec}s ago`
    const min = Math.floor(sec / 60)
    if (min < 60) return `${min}m ago`
    const hr = Math.floor(min / 60)
    if (hr < 24) return `${hr}h ago`
    const d = Math.floor(hr / 24)
    return `${d}d ago`
  },

  // 执行类型 → i18n key，由模板 `$t()` 解析以支持中英文
  // 修复：未知 type 返回空字符串，避免 '—' 被当作 i18n key 传入 $t() 触发 intlify 警告
  typeLabel(type) {
    const map = { single: 'executions.type_single', scenario: 'executions.type_scenario', monitor: 'executions.type_monitor' }
    return map[type] || type || ''
  },

  // 触发方式 → i18n key，由模板 `$t()` 解析以支持中英文
  // 修复：未知 trigger 返回空字符串，避免 '—' 被当作 i18n key 传入 $t() 触发 intlify 警告
  triggerLabel(trigger) {
    const map = { manual: 'executions.trigger_manual', monitor: 'executions.trigger_monitor', scheduler: 'executions.trigger_scheduler' }
    return map[trigger] || trigger || ''
  },

  // 格式化日期时间为本地可读字符串，用于执行记录时间展示
  time(date) {
    if (!date) return '—'
    const d = new Date(date)
    if (isNaN(d.getTime())) return '—'
    const pad = (n) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  },
}

// ── Method badge class ─────────────────────────────────────
export function methodClass(method) {
  return `method method-${(method || 'GET').toUpperCase()}`
}

// ── JSON pretty print ──────────────────────────────────────
export function jsonPretty(obj) {
  if (obj == null) return '{}'
  try {
    return JSON.stringify(obj, null, 2)
  } catch {
    // JSON.stringify 失败（如循环引用）时回退为原始字符串展示
    return String(obj)
  }
}

// ── Status / risk tag type helpers ────────────────────────
// HTTP 状态码 → ElTag type 映射：2xx→success, 3xx→warning, 4xx/5xx→danger, 无→info
export function statusTagType(code) {
  if (!code) return 'info'
  if (code < 300) return 'success'
  if (code < 400) return 'warning'
  return 'danger'
}

// 风险等级 → ElTag type 映射: low→info, medium→warning, high→danger, critical→danger
export function riskTagType(r) {
  const map = { low: 'info', medium: 'warning', high: 'danger', critical: 'danger' }
  return map[r] || 'info'
}

// 场景状态 → ElTag type 映射：draft→info, ready→info, running→warning, done→success, failed→danger
export function scenarioStatusTagType(s) {
  const map = { draft: 'info', ready: 'info', running: 'warning', done: 'success', failed: 'danger' }
  return map[s] || 'info'
}

// ── Copy to clipboard ──────────────────────────────────────
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // Fallback for older browsers
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
    return true
  }
}
