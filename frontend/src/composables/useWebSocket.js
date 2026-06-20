// WebSocket 生命周期管理 composable —— 自动在 onUnmounted 时清理连接
import { onUnmounted } from 'vue'
import { openWs } from '@/api'

export function useWebSocket(path, onMessage) {
  const ws = openWs(path, onMessage)

  onUnmounted(() => {
    // terminate() 禁用自动重连后关闭连接，避免组件卸载后回调操作已销毁的响应式数据
    if (ws?.terminate) ws.terminate()
  })

  return ws
}
