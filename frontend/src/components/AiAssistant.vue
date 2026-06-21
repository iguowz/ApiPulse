<template>
  <!-- P1-3: AI 助手对话面板 —— Cmd/Ctrl+K 唤起的全局浮窗 -->
  <!-- 收起态：右下角圆形浮动按钮，仅展示 logo -->
  <button
    v-if="!visible"
    class="ai-float-btn"
    :title="$t('ai_assistant.shortcut_hint')"
    @click="open"
  >
    <span class="ai-float-icon">✨</span>
  </button>

  <div v-if="visible" class="ai-assistant-overlay" @click.self="close">
    <div class="ai-assistant-panel">
      <!-- 头部 -->
      <div class="aa-header">
        <div class="aa-title">
          <span class="aa-icon">✨</span>
          <span>{{ $t('ai_assistant.title') }}</span>
          <span v-if="contextLabel" class="aa-context-tag">{{ contextLabel }}</span>
        </div>
        <div class="aa-actions">
          <button class="aa-btn-icon" @click="confirmClearHistory" :title="$t('ai_assistant.clear_title')">🗑</button>
          <button class="aa-btn-icon" @click="close" title="关闭 (Esc)">✕</button>
        </div>
      </div>

      <!-- 消息列表 -->
      <div ref="messagesRef" class="aa-messages" @scroll="onMessagesScroll">
        <div v-if="!messages.length" class="aa-empty">
          <div class="aa-empty-icon">💬</div>
          <div class="aa-empty-title">{{ $t('ai_assistant.empty_title') }}</div>
          <!-- 页面上下文提示：根据当前路由显示特定界面的引导文案 -->
          <div v-if="contextEmptyHint" class="aa-context-hint">{{ contextEmptyHint }}</div>
          <div class="aa-suggestions">
            <button v-for="s in suggestions" :key="s" class="aa-suggestion" @click="sendMessage(s)">
              {{ s }}
            </button>
          </div>
        </div>
        <div v-for="(msg, i) in messages" :key="i" :class="['aa-msg', `aa-msg-${msg.role}`]">
          <div class="aa-msg-role">{{ msg.role === 'user' ? '🧑' : '✨' }}</div>
          <div class="aa-msg-content">
            <!-- UX 1：Markdown 渲染替代纯文本 <pre> -->
            <div v-if="msg.content" class="aa-msg-text" v-html="renderMarkdown(msg.content)"></div>
            <div v-if="msg.tools?.length" class="aa-tool-list">
              <div v-for="(tool, ti) in msg.tools" :key="ti" class="aa-tool-item">
                <span class="aa-tool-dot"></span>
                <span>{{ tool.label }}</span>
              </div>
            </div>
            <div v-if="msg.references?.length" class="aa-ref-list">
              <button v-for="ref in msg.references" :key="ref.type + ref.id" class="aa-ref" @click="goRef(ref)">
                {{ refTitle(ref) }}
              </button>
            </div>
            <!-- AI 助手对话不进入审核中心 -->
            <span v-if="msg.streaming" class="aa-cursor">▋</span>
          </div>
        </div>
      </div>

      <!-- UX 2：用户上滚后显示"回到底部"浮动按钮 -->
      <button v-if="showScrollBtn" class="aa-scroll-bottom-btn" @click="scrollToBottom(true)">{{ $t('ai_assistant.scroll_to_bottom') }}</button>


      <!-- 输入区 -->
      <div class="aa-input-area">
        <textarea
          ref="inputRef"
          v-model="inputText"
          class="aa-input"
          :placeholder="$t('ai_assistant.input_placeholder')"
          rows="2"
          @keydown.enter.exact.prevent="sendMessage()"
          @keydown.enter.shift.exact="inputText += '\n'"
        />
        <button v-if="streaming" class="aa-send-btn aa-stop-btn" @click="stopStreaming">
          {{ $t('ai_assistant.stop') }}
        </button>
        <button v-else class="aa-send-btn" @click="sendMessage()" :disabled="!inputText.trim()">
          ➤
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'
import { useAuthStore, useProjectStore, useToastStore } from '@/stores'
// UX 1：引入 marked 实现 Markdown 渲染
import { marked } from 'marked'
// P1-2: XSS 防护——使用 DOMPurify 清理 marked 输出的 HTML
import DOMPurify from 'dompurify'
// vue-i18n v9 legacy:false 无法通过 t() 解析嵌套数组值，直接导入 JSON 读取建议文案
import zhCN from '@/locales/zh-CN.json'
import en from '@/locales/en.json'

const route = useRoute()
const router = useRouter()
const { t, locale } = useI18n()
const authStore = useAuthStore()
const toast = useToastStore()

const visible = ref(false)
const inputText = ref('')
const messages = ref([])
const sessionId = ref('')
const streaming = ref(false)
const messagesRef = ref(null)
const inputRef = ref(null)
const abortController = ref(null)
// UX 2：用户滚动锁定状态
const showScrollBtn = ref(false)
const userScrolledUp = ref(false)

// 上下文感知：根据当前路由推断页面上下文，不同页面携带不同的上下文信息和提示问题
// 包含 project_id 以确保 L3 会话记忆与当前项目关联
const projectStore = useProjectStore()
const context = computed(() => {
  const p = route.path
  const project_id = projectStore.current || 'default'
  // 详情页（携带 ID，触发后端预取和精准回答）
  if (p.startsWith('/apis/') && route.params.id) {
    return { type: 'api', id: route.params.id, page: 'API 详情', project_id }
  }
  if (p.startsWith('/scenarios/') && route.params.id) {
    return { type: 'scenario', id: route.params.id, page: '场景详情', project_id }
  }
  if (p.startsWith('/executions/') && route.params.id) {
    return { type: 'execution', id: route.params.id, page: '执行详情', project_id }
  }
  // 列表/管理页（无 ID，仅页面标识）
  if (p === '/apis' || p === '/apis/') return { type: 'api_list', page: 'API 管理', project_id }
  if (p === '/scenarios' || p === '/scenarios/') return { type: 'scenario_list', page: '场景管理', project_id }
  if (p === '/executions' || p === '/executions/') return { type: 'execution_list', page: '执行记录', project_id }
  if (p === '/dashboard' || p === '/dashboard/') return { type: 'dashboard', page: '仪表盘', project_id }
  if (p === '/monitor' || p === '/monitor/') return { type: 'monitor', page: '巡检监控', project_id }
  if (p === '/factory' || p === '/factory/') return { type: 'factory', page: '数据工厂', project_id }
  if (p === '/generations' || p === '/generations/') return { type: 'generations', page: '审核中心', project_id }
  if (p === '/coverage' || p === '/coverage/') return { type: 'coverage', page: '覆盖率', project_id }
  if (p === '/knowledge' || p === '/knowledge/') return { type: 'knowledge', page: '知识库', project_id }
  if (p === '/settings' || p === '/settings/') return { type: 'settings', page: '系统设置', project_id }
  if (p === '/import-diffs' || p === '/import-diffs/') return { type: 'import_diffs', page: '差异对比', project_id }
  if (p.startsWith('/mock-services')) return { type: 'mock_services', page: 'Mock 服务', project_id }
  if (p === '/docs' || p === '/docs/') return { type: 'docs', page: '接口文档', project_id }
  if (p === '/admin/users' || p === '/admin/users/') return { type: 'admin_users', page: '用户管理', project_id }
  if (p === '/memory' || p === '/memory/') return { type: 'memory', page: '记忆管理', project_id }
  return { project_id }
})

const contextLabel = computed(() => {
  const c = context.value
  if (!c.page) return ''
  // 详情页：页面名 + ID 前缀
  if (c.id) return `${c.page}: ${c.id.slice(0, 8)}...`
  return c.page
})

// 页面上下文提示：根据当前路由展示对应界面的引导文案
const contextEmptyHint = computed(() => {
  const c = context.value
  if (!c.page) return ''
  if (c.id) return t('ai_assistant.context_hint_detail', { page: c.page, id: c.id.slice(0, 8) })
  return t('ai_assistant.context_hint_page', { page: c.page })
})

// 建议问题（按页面类型动态调整，直接读取 JSON 绕过 vue-i18n 对嵌套数组的解析缺陷）
const messagesMap = { 'zh-CN': zhCN, en, }
const suggestions = computed(() => {
  const msgs = messagesMap[locale.value] || zhCN
  const suggestionsMap = msgs?.ai_assistant?.suggestions || {}
  const type = context.value.type
  if (type && Array.isArray(suggestionsMap[type]) && suggestionsMap[type].length) {
    return suggestionsMap[type]
  }
  return suggestionsMap.default || []
})

// 全局快捷键 Cmd/Ctrl+K 唤起
function onKeydown(e) {
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
    e.preventDefault()
    toggle()
  }
  if (e.key === 'Escape' && visible.value) {
    close()
  }
}

function toggle() {
  visible.value ? close() : open()
}

function open() {
  visible.value = true
  nextTick(() => inputRef.value?.focus())
}

function close() {
  visible.value = false
}

// 发送消息（SSE 流式接收）
async function sendMessage(text) {
  const content = (text || inputText.value).trim()
  if (!content || streaming.value) {
    if (!content) toast.info(t('ai_assistant.emptyInputHint'))
    return
  }

  // 未登录则提示
  if (!authStore.token) {
    messages.value.push({ role: 'assistant', content: t('ai_assistant.error_login_required') })
    return
  }

  inputText.value = ''
  messages.value.push({ role: 'user', content })
  const assistantMsg = { role: 'assistant', content: '', streaming: true, tools: [], references: [] }
  messages.value.push(assistantMsg)
  streaming.value = true
  await scrollToBottom()

  // H2: SSE 重连逻辑——网络错误时最多重试3次（指数退避），HTTP 4xx/5xx 不重试
  const maxRetries = 3
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    // 重试时清空 assistant 消息内容，避免残留上次失败的错误信息
    if (attempt > 0) assistantMsg.content = ''
    try {
      // 用 fetch + ReadableStream 接收 SSE（EventSource 不支持 POST + headers）
      abortController.value = new AbortController()
      const resp = await fetch('/api/ai/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${authStore.token}`,
        },
        body: JSON.stringify({
          message: content,
          session_id: sessionId.value,
          context: context.value,
        }),
        signal: abortController.value.signal,
      })

      if (!resp.ok) {
        // HTTP 4xx/5xx 不重试，直接显示错误
        assistantMsg.content = t('ai_assistant.error_request_failed', { status: resp.status })
        break
      }

      // 解析 SSE 流
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        // SSE 以 \n\n 分隔事件
        const parts = buffer.split('\n\n')
        buffer = parts.pop() || ''
        for (const part of parts) {
          const line = part.trim()
          if (!line.startsWith('data: ')) continue
          try {
            const data = JSON.parse(line.slice(6))
            if (data.type === 'session') {
              sessionId.value = data.session_id
            } else if (data.type === 'delta') {
              assistantMsg.content += data.content
              await scrollToBottom()
            } else if (data.type === 'tool_call') {
              assistantMsg.tools.push({ name: data.tool, label: `查询 ${toolLabel(data.tool)}...` })
              await scrollToBottom()
            } else if (data.type === 'tool_result') {
              const last = [...assistantMsg.tools].reverse().find(t => t.name === data.tool && t.label.endsWith('...'))
              if (last) last.label = `${toolLabel(data.tool)} 已返回`
              const refs = data.result?.references || []
              assistantMsg.references = mergeRefs(assistantMsg.references, refs)
              if (data.result?.error && !assistantMsg.content) assistantMsg.content = `⚠️ ${data.result.error}`
              await scrollToBottom()
            } else if (data.type === 'generation_created') {
              assistantMsg.generation = data
              assistantMsg.references = mergeRefs(assistantMsg.references, data.references || [])
              await scrollToBottom()
            } else if (data.type === 'done') {
              sessionId.value = data.session_id
            } else if (data.type === 'error') {
              assistantMsg.content = `⚠️ ${data.message}`
            } else if (data.type === 'confirm_required') {
              // 写操作需要用户确认：使用 ElMessageBox.confirm 弹窗（M4）
              assistantMsg.content += `\n\n> 🔧 **${t('ai_assistant.confirm_title')}**：${data.summary || data.tool}`
              await scrollToBottom()
              let confirmed = false
              try {
                await ElMessageBox.confirm(
                  data.summary || data.params_preview ? JSON.stringify(data.params_preview, null, 2) : '',
                  t('ai_assistant.confirm_title'),
                  { confirmButtonText: t('ai_assistant.confirm_execute'), cancelButtonText: t('ai_assistant.confirm_cancel'), type: 'warning', zIndex: 10000 }
                )
                confirmed = true
              } catch (_) {
                // 用户取消或关闭弹窗
              }
              if (confirmed) {
                try {
                  const resp = await fetch('/api/ai/chat/confirm', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${authStore.token}` },
                    body: JSON.stringify({ action_id: data.action_id, approved: true }),
                  })
                  const result = await resp.json()
                  if (!resp.ok || !result.success) {
                    assistantMsg.content += `\n\n❌ ${result.message || t('ai_assistant.write_failed')}`
                  } else {
                    assistantMsg.content += `\n\n✅ ${result.result?.message || t('ai_assistant.write_success')}`
                  }
                } catch (e) {
                  assistantMsg.content += `\n\n❌ ${t('ai_assistant.write_failed')}: ${e.message}`
                }
              }
              await scrollToBottom()
            }
            // write_executed / write_failed 事件由 confirm_required 中的 POST 请求处理，不再重复追加
            // UX 5：SSE 解析失败记录 debug 日志，方便排查，生产环境不暴露
          } catch (e) { console.debug('[AiAssistant] SSE parse error:', e, part.slice(0, 100)) }
        }
      }
      // 流正常结束，退出重试循环
      break
    } catch (e) {
      if (e.name === 'AbortError') {
        // 用户主动停止生成，不重试
        assistantMsg.content = t('ai_assistant.stopped')
        break
      }
      // 网络错误（TypeError 通常表示 fetch/连接失败）才重试
      const isNetworkError = e instanceof TypeError
      if (isNetworkError && attempt < maxRetries) {
        const delay = 1000 * Math.pow(2, attempt) // 指数退避: 1s, 2s, 4s
        assistantMsg.content = t('ai_assistant.retrying', { attempt: attempt + 1, max: maxRetries })
        await new Promise(r => setTimeout(r, delay))
        continue
      }
      // 非网络错误或重试耗尽：显示错误信息
      assistantMsg.content = t('ai_assistant.error_network', { message: e.message })
      if (attempt >= maxRetries) {
        toast.error(t('ai_assistant.error_max_retries'))
      }
      break
    }
  }
  assistantMsg.streaming = false
  streaming.value = false
  abortController.value = null
}

function stopStreaming() {
  abortController.value?.abort()
}

// P1-4: 工具标签名称改用 i18n，消除硬编码中文
function toolLabel(name) {
  const key = `ai_assistant.tool_${name}`
  const translated = t(key)
  return translated !== key ? translated : name
}

function mergeRefs(oldRefs = [], newRefs = []) {
  const map = new Map()
  for (const r of [...oldRefs, ...newRefs]) {
    if (r?.id && r?.route) map.set(`${r.type}:${r.id}`, r)
  }
  return Array.from(map.values()).slice(0, 8)
}

// P1-4: 引用类型名改用 i18n，消除硬编码中文
function refTitle(ref) {
  const typeKey = `ai_assistant.ref_${ref.type}`
  const label = t(typeKey)
  const typeName = label !== typeKey ? label : (ref.type || '')
  return `${typeName}: ${ref.title || ref.id}`
}

function goRef(ref) {
  if (ref?.route) router.push(ref.route)
  close()
}

// UX 1 + P1-2：用 marked 解析 Markdown，DOMPurify 清除 XSS 脚本后通过 v-html 渲染
function renderMarkdown(text) {
  if (!text) return ''
  const raw = marked.parse(text, { breaks: true, gfm: true })
  return DOMPurify.sanitize(raw)
}

// UX 2：滚动锁定 —— 仅当用户未手动上滚时才自动滚底
async function scrollToBottom(force = false) {
  await nextTick()
  if (!messagesRef.value) return
  const el = messagesRef.value
  const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
  // 用户向上滚动超过50px则锁定，不自动回滚
  if (!force && distFromBottom > 50) {
    showScrollBtn.value = true
    userScrolledUp.value = true
    return
  }
  el.scrollTop = el.scrollHeight
  showScrollBtn.value = false
  userScrolledUp.value = false
}

// UX 2：监听消息区滚动事件，回到底部时自动隐藏按钮
function onMessagesScroll() {
  if (!messagesRef.value) return
  const el = messagesRef.value
  const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
  if (distFromBottom <= 5) {
    showScrollBtn.value = false
    userScrolledUp.value = false
  }
}

// UX 3：确认后清空对话（由 ElMessageBox 确认触发），避免误操作丢失对话
async function confirmClearHistory() {
  try {
    await ElMessageBox.confirm(
      t('ai_assistant.clear_confirm'),
      t('ai_assistant.clear_title'),
      { confirmButtonText: t('common.confirm'), cancelButtonText: t('common.cancel'), type: 'warning', zIndex: 10000 }
    )
    await doClearHistory()
  } catch (_) {
    // 用户取消
  }
}
async function doClearHistory() {
  // H3：清除持久化的聊天消息
  sessionStorage.removeItem('ai-chat-messages')
  if (sessionId.value) {
    try {
      await fetch(`/api/ai/chat/history/${sessionId.value}`, {
        method: 'DELETE',
        headers: { 'Authorization': `Bearer ${authStore.token}` },
      })
    } catch (_) { /* 忽略清除失败 */ }
  }
  messages.value = []
  sessionId.value = ''
}


// UX 4：sessionId 持久化到 localStorage，刷新后保持同一会话
// H3：聊天消息持久化到 sessionStorage，刷新后恢复最近50条
onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  const saved = localStorage.getItem('ai_assistant_session_id')
  if (saved) sessionId.value = saved
  try {
    const savedMessages = sessionStorage.getItem('ai-chat-messages')
    if (savedMessages) messages.value = JSON.parse(savedMessages)
  } catch (_) { /* sessionStorage 解析失败则忽略 */ }
})
// UX 4：sessionId 变化时自动写入 localStorage
watch(sessionId, (val) => {
  if (val) localStorage.setItem('ai_assistant_session_id', val)
  else localStorage.removeItem('ai_assistant_session_id')
})
// H3：消息变化时持久化到 sessionStorage（保留最近 50 条，含工具调用和引用）
watch(messages, (val) => {
  const toSave = val.slice(-50).map(m => ({
    role: m.role,
    content: m.content,
    tools: m.tools,
    references: m.references,
  }))
  sessionStorage.setItem('ai-chat-messages', JSON.stringify(toSave))
}, { deep: true })
// 页面切换时重置会话：context.type 变化说明用户已导航到不同页面，需清空旧对话
watch(
  () => `${context.value.type || ''}:${context.value.id || ''}`,
  (newKey, oldKey) => {
    if (oldKey && newKey !== oldKey) {
      messages.value = []
      sessionId.value = ''
    }
  }
)
onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
})
</script>

<style scoped>
/* ── 收起态：右下角浮动按钮 ── */
.ai-float-btn {
  position: fixed; bottom: 24px; right: 24px; z-index: 1998;
  /* 纯圆形浮窗：宽高相等 + border-radius:50% */
  width: 48px; height: 48px;
  display: flex; align-items: center; justify-content: center;
  padding: 0;
  border: none; border-radius: 50%;
  background: var(--accent, #4F8EF7); color: #fff;
  box-shadow: 0 4px 16px rgba(79,142,247,.35);
  cursor: pointer;
  transition: transform .15s, box-shadow .15s;
  user-select: none;
}
.ai-float-btn:hover {
  transform: scale(1.12);
  box-shadow: 0 6px 20px rgba(79,142,247,.45);
}
.ai-float-icon { font-size: 22px; line-height: 1; }

.ai-assistant-overlay {
  position: fixed; inset: 0; z-index: 1999;
  background: rgba(0,0,0,.3);
  display: flex; align-items: flex-start; justify-content: center;
  padding-top: 8vh;
}
.ai-assistant-panel {
  width: 640px; max-width: 92vw; max-height: 80vh;
  /* 使用主题变量 --bg-2：暗色 #141720 / 亮色 #ffffff，随 data-theme 切换 */
  background: var(--bg-2);
  border-radius: 12px;
  box-shadow: 0 20px 60px rgba(0,0,0,.3);
  display: flex; flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--border);
  color: var(--text);
}
.aa-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 14px 18px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-3);
  color: var(--text);
}
.aa-title { display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: 14px; }
.aa-icon { font-size: 18px; }
.aa-context-tag {
  font-size: 10px; padding: 2px 8px; border-radius: 10px;
  background: var(--accent, #4F8EF7); color: #fff; font-weight: 400;
}
.aa-actions { display: flex; gap: 6px; }
.aa-btn-icon {
  border: none; background: transparent; cursor: pointer;
  font-size: 14px; padding: 4px 8px; border-radius: 4px;
  color: var(--text-2);
}
.aa-btn-icon:hover { background: var(--bg-hover); }
.aa-messages {
  flex: 1; overflow-y: auto; padding: 16px;
  min-height: 200px; max-height: 50vh;
}
.aa-empty {
  text-align: center; padding: 30px 0; color: var(--text-3);
}
.aa-empty-icon { font-size: 36px; margin-bottom: 8px; }
.aa-empty-title { font-size: 15px; margin-bottom: 8px; color: var(--text); }
.aa-context-hint {
  font-size: 12px; color: var(--text-3); margin-bottom: 16px;
  padding: 4px 12px; border-radius: 6px; background: var(--bg-3);
}
.aa-suggestions { display: flex; flex-direction: column; gap: 6px; align-items: center; }
.aa-suggestion {
  border: 1px solid var(--border); background: var(--bg-2);
  padding: 8px 16px; border-radius: 6px; cursor: pointer;
  font-size: 12px; color: var(--text-2);
  transition: all .15s;
}
.aa-suggestion:hover { border-color: var(--accent, #4F8EF7); color: var(--accent, #4F8EF7); }
.aa-msg { display: flex; gap: 10px; margin-bottom: 14px; }
.aa-msg-role { font-size: 18px; flex-shrink: 0; }
.aa-msg-content { flex: 1; min-width: 0; }
.aa-msg-text {
  margin: 0; padding: 10px 14px; border-radius: 8px;
  font-family: inherit; font-size: 13px; line-height: 1.6;
  white-space: pre-wrap; word-break: break-word;
}
.aa-msg-user .aa-msg-text { background: var(--accent, #4F8EF7); color: #fff; }
/* assistant 消息：bg-3 在暗色主题为 #1c2030（略亮于面板），亮色为 #eef0f6（浅灰），两主题均可用 */
.aa-msg-assistant .aa-msg-text { background: var(--bg-3); color: var(--text); }
.aa-tool-list {
  display: flex; flex-direction: column; gap: 4px; margin-top: 6px;
}
.aa-tool-item {
  display: flex; align-items: center; gap: 6px;
  font-size: 11px; color: var(--text-3);
}
.aa-tool-dot {
  width: 6px; height: 6px; border-radius: 50%; background: var(--accent, #4F8EF7);
}
.aa-ref-list {
  display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px;
}
.aa-ref {
  border: 1px solid var(--border); background: var(--bg-2);
  color: var(--accent); border-radius: 6px; padding: 4px 8px;
  font-size: 11px; cursor: pointer;
}
.aa-ref:hover { border-color: var(--accent, #4F8EF7); background: rgba(79,142,247,.08); }
.aa-generation {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  margin-top: 8px; padding: 8px 10px; border-radius: 6px;
  background: rgba(224,175,54,.08); border: 1px solid rgba(224,175,54,.24);
  color: var(--text-2); font-size: 12px;
}
.aa-cursor { animation: aa-blink 1s step-end infinite; color: var(--accent, #4F8EF7); }
@keyframes aa-blink { 0%,50%{opacity:1} 51%,100%{opacity:0} }
.aa-input-area {
  display: flex; gap: 8px; padding: 12px;
  border-top: 1px solid var(--border);
  background: var(--bg-3);
}
.aa-input {
  flex: 1; resize: none; border: 1px solid var(--border);
  border-radius: 6px; padding: 8px 12px; font-size: 13px;
  font-family: inherit; outline: none;
  background: var(--bg-2); color: var(--text);
}
.aa-input:focus { border-color: var(--accent, #4F8EF7); }
.aa-send-btn {
  border: none; background: var(--accent, #4F8EF7); color: #fff;
  padding: 0 16px; border-radius: 6px; cursor: pointer; font-size: 16px;
}
.aa-stop-btn { background: var(--red); font-size: 13px; }
.aa-send-btn:disabled { opacity: .5; cursor: not-allowed; }
/* UX 2：回到底部浮动按钮 */
.aa-scroll-bottom-btn {
  position: absolute; bottom: 72px; left: 50%; transform: translateX(-50%); z-index: 10;
  padding: 4px 14px; border-radius: 14px; border: 1px solid var(--border);
  background: var(--bg-2); color: var(--accent, #4F8EF7);
  font-size: 12px; cursor: pointer; box-shadow: 0 2px 8px rgba(0,0,0,.12);
}
.aa-scroll-bottom-btn:hover { background: var(--bg-hover); }
/* UX 1：Markdown 渲染样式 */
.aa-msg-text :deep(p) { margin: 0 0 6px; }
.aa-msg-text :deep(p:last-child) { margin-bottom: 0; }
.aa-msg-text :deep(code) { background: rgba(128,128,128,.15); padding: 1px 5px; border-radius: 3px; font-size: 12px; }
.aa-msg-text :deep(pre) { background: rgba(128,128,128,.12); padding: 8px 12px; border-radius: 6px; overflow-x: auto; margin: 6px 0; }
.aa-msg-text :deep(pre code) { background: none; padding: 0; }
.aa-msg-text :deep(ul), .aa-msg-text :deep(ol) { padding-left: 18px; margin: 4px 0; }
.aa-msg-text :deep(blockquote) { border-left: 3px solid var(--accent, #4F8EF7); padding-left: 10px; margin: 6px 0; opacity: .8; }
</style>
