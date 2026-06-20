<template>
  <!-- P1-3: AI 助手对话面板 —— Cmd/Ctrl+K 唤起的全局浮窗 -->
  <div v-if="visible" class="ai-assistant-overlay" @click.self="close">
    <div class="ai-assistant-panel">
      <!-- 头部 -->
      <div class="aa-header">
        <div class="aa-title">
          <span class="aa-icon">✨</span>
          <span>AI 助手</span>
          <span v-if="contextLabel" class="aa-context-tag">{{ contextLabel }}</span>
        </div>
        <div class="aa-actions">
          <button class="aa-btn-icon" @click="clearHistory" title="清空对话">🗑</button>
          <button class="aa-btn-icon" @click="close" title="关闭 (Esc)">✕</button>
        </div>
      </div>

      <!-- 消息列表 -->
      <div ref="messagesRef" class="aa-messages">
        <div v-if="!messages.length" class="aa-empty">
          <div class="aa-empty-icon">💬</div>
          <div class="aa-empty-title">有什么可以帮你？</div>
          <div class="aa-suggestions">
            <button v-for="s in suggestions" :key="s" class="aa-suggestion" @click="sendMessage(s)">
              {{ s }}
            </button>
          </div>
        </div>
        <div v-for="(msg, i) in messages" :key="i" :class="['aa-msg', `aa-msg-${msg.role}`]">
          <div class="aa-msg-role">{{ msg.role === 'user' ? '🧑' : '✨' }}</div>
          <div class="aa-msg-content">
            <pre v-if="msg.content" class="aa-msg-text">{{ msg.content }}</pre>
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
            <div v-if="msg.generation" class="aa-generation">
              <span>{{ msg.generation.summary || $t('ai_assistant.generation_created_default') }}</span>
              <button class="aa-ref" @click="router.push('/generations?status=pending_review')">{{ $t('ai_assistant.go_review') }}</button>
            </div>
            <span v-if="msg.streaming" class="aa-cursor">▋</span>
          </div>
        </div>
      </div>

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
          停止
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
import { useAuthStore } from '@/stores'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

const visible = ref(false)
const inputText = ref('')
const messages = ref([])
const sessionId = ref('')
const streaming = ref(false)
const messagesRef = ref(null)
const inputRef = ref(null)
const abortController = ref(null)

// 上下文感知：根据当前路由推断页面上下文
const context = computed(() => {
  if (route.path.startsWith('/apis/') && route.params.id) {
    return { type: 'api', id: route.params.id }
  }
  if (route.path.startsWith('/scenarios/') && route.params.id) {
    return { type: 'scenario', id: route.params.id }
  }
  if (route.path.startsWith('/executions/') && route.params.id) {
    return { type: 'execution', id: route.params.id }
  }
  return {}
})

const contextLabel = computed(() => {
  const c = context.value
  if (!c.type) return ''
  return `${c.type}: ${c.id?.slice(0, 8)}...`
})

// 建议问题（按上下文动态调整）
const suggestions = computed(() => {
  const c = context.value
  if (c.type === 'execution') {
    return ['这次执行为什么失败？', '如何修复这个错误？']
  }
  if (c.type === 'api') {
    return ['这个接口的断言该怎么写？', '帮我分析这个接口的质量']
  }
  return ['如何创建测试场景？', '巡检告警怎么配置？', '数据工厂怎么用？']
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
  if (!content || streaming.value) return

  // 未登录则提示
  if (!authStore.token) {
    messages.value.push({ role: 'assistant', content: '请先登录后使用 AI 助手' })
    return
  }

  inputText.value = ''
  messages.value.push({ role: 'user', content })
  const assistantMsg = { role: 'assistant', content: '', streaming: true, tools: [], references: [] }
  messages.value.push(assistantMsg)
  streaming.value = true
  await scrollToBottom()

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
      assistantMsg.content = `请求失败 (${resp.status})`
      assistantMsg.streaming = false
      streaming.value = false
      return
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
          }
        } catch { /* 忽略解析失败的 chunk */ }
      }
    }
  } catch (e) {
    assistantMsg.content = e.name === 'AbortError' ? '已停止生成。' : `⚠️ 网络错误: ${e.message}`
  } finally {
    assistantMsg.streaming = false
    streaming.value = false
    abortController.value = null
  }
}

function stopStreaming() {
  abortController.value?.abort()
}

function toolLabel(name) {
  const map = {
    search_apis: '接口搜索',
    get_api: '接口详情',
    get_execution: '执行记录',
    list_scenarios: '场景列表',
    get_monitor_stats: '监控统计',
    get_pending_generations: '待审核内容',
  }
  return map[name] || name
}

function mergeRefs(oldRefs = [], newRefs = []) {
  const map = new Map()
  for (const r of [...oldRefs, ...newRefs]) {
    if (r?.id && r?.route) map.set(`${r.type}:${r.id}`, r)
  }
  return Array.from(map.values()).slice(0, 8)
}

function refTitle(ref) {
  const labelMap = { api: 'API', execution: '执行', scenario: '场景', monitor: '监控', generation: '审核' }
  return `${labelMap[ref.type] || ref.type}: ${ref.title || ref.id}`
}

function goRef(ref) {
  if (ref?.route) router.push(ref.route)
  close()
}

async function scrollToBottom() {
  await nextTick()
  if (messagesRef.value) {
    messagesRef.value.scrollTop = messagesRef.value.scrollHeight
  }
}

async function clearHistory() {
  if (sessionId.value) {
    try {
      await fetch(`/api/ai/chat/history/${sessionId.value}`, {
        method: 'DELETE',
        headers: { 'Authorization': `Bearer ${authStore.token}` },
      })
    } catch { /* 忽略清除失败 */ }
  }
  messages.value = []
  sessionId.value = ''
}

// 监听上下文变化时清空（避免跨页面上下文混淆）
watch(() => route.path, () => {
  if (visible.value && messages.value.length) {
    // 不自动清空，保留对话连续性
  }
})

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
})
onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
})
</script>

<style scoped>
.ai-assistant-overlay {
  position: fixed; inset: 0; z-index: 9999;
  background: rgba(0,0,0,.3);
  display: flex; align-items: flex-start; justify-content: center;
  padding-top: 8vh;
}
.ai-assistant-panel {
  width: 640px; max-width: 92vw; max-height: 80vh;
  background: var(--bg-0, #fff);
  border-radius: 12px;
  box-shadow: 0 20px 60px rgba(0,0,0,.3);
  display: flex; flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--border, #e0e0e0);
}
.aa-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 14px 18px;
  border-bottom: 1px solid var(--border, #eee);
  background: var(--bg-1, #f8f9fa);
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
  color: var(--text-2, #666);
}
.aa-btn-icon:hover { background: var(--bg-2, #eee); }
.aa-messages {
  flex: 1; overflow-y: auto; padding: 16px;
  min-height: 200px; max-height: 50vh;
}
.aa-empty {
  text-align: center; padding: 30px 0; color: var(--text-3, #999);
}
.aa-empty-icon { font-size: 36px; margin-bottom: 8px; }
.aa-empty-title { font-size: 15px; margin-bottom: 16px; color: var(--text-1, #333); }
.aa-suggestions { display: flex; flex-direction: column; gap: 6px; align-items: center; }
.aa-suggestion {
  border: 1px solid var(--border, #ddd); background: var(--bg-0, #fff);
  padding: 8px 16px; border-radius: 6px; cursor: pointer;
  font-size: 12px; color: var(--text-2, #555);
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
.aa-msg-assistant .aa-msg-text { background: var(--bg-2, #f0f0f0); color: var(--text-1, #333); }
.aa-tool-list {
  display: flex; flex-direction: column; gap: 4px; margin-top: 6px;
}
.aa-tool-item {
  display: flex; align-items: center; gap: 6px;
  font-size: 11px; color: var(--text-3, #888);
}
.aa-tool-dot {
  width: 6px; height: 6px; border-radius: 50%; background: var(--accent, #4F8EF7);
}
.aa-ref-list {
  display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px;
}
.aa-ref {
  border: 1px solid var(--border, #ddd); background: var(--bg-0, #fff);
  color: var(--accent, #4F8EF7); border-radius: 6px; padding: 4px 8px;
  font-size: 11px; cursor: pointer;
}
.aa-ref:hover { border-color: var(--accent, #4F8EF7); background: rgba(79,142,247,.08); }
.aa-generation {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  margin-top: 8px; padding: 8px 10px; border-radius: 6px;
  background: rgba(224,175,54,.08); border: 1px solid rgba(224,175,54,.24);
  color: var(--text-2, #555); font-size: 12px;
}
.aa-cursor { animation: aa-blink 1s step-end infinite; color: var(--accent, #4F8EF7); }
@keyframes aa-blink { 0%,50%{opacity:1} 51%,100%{opacity:0} }
.aa-input-area {
  display: flex; gap: 8px; padding: 12px;
  border-top: 1px solid var(--border, #eee);
  background: var(--bg-1, #f8f9fa);
}
.aa-input {
  flex: 1; resize: none; border: 1px solid var(--border, #ddd);
  border-radius: 6px; padding: 8px 12px; font-size: 13px;
  font-family: inherit; outline: none;
  background: var(--bg-0, #fff); color: var(--text-1, #333);
}
.aa-input:focus { border-color: var(--accent, #4F8EF7); }
.aa-send-btn {
  border: none; background: var(--accent, #4F8EF7); color: #fff;
  padding: 0 16px; border-radius: 6px; cursor: pointer; font-size: 16px;
}
.aa-stop-btn { background: var(--red, #f06060); font-size: 13px; }
.aa-send-btn:disabled { opacity: .5; cursor: not-allowed; }
</style>
