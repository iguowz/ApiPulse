"""
WebSocket 连接管理器 —— 并发广播，互不阻塞
"""
from __future__ import annotations

import asyncio

from fastapi import WebSocket


class _WsManager:
    """WebSocket 连接管理器：按 key 分组管理连接，支持并发广播"""

    def __init__(self):
        self._subs: dict[str, list[WebSocket]] = {}

    async def connect(self, key: str, ws: WebSocket):
        await ws.accept()
        self._subs.setdefault(key, []).append(ws)

    def disconnect(self, key: str, ws: WebSocket):
        if key in self._subs:
            remaining = [w for w in self._subs[key] if w is not ws]
            if remaining:
                self._subs[key] = remaining
            else:
                # 清理空 key，避免 _subs 字典无限膨胀
                del self._subs[key]

    async def broadcast(self, key: str, data: dict):
        keys = [key]
        project_id = data.get("project_id") if isinstance(data, dict) else None
        if project_id:
            project_key = f"{key}:{project_id}"
            if project_key not in keys:
                keys.append(project_key)
        # 记录每个连接来自哪个 key，确保断连时能正确定位并清理
        conns: list[tuple[WebSocket, str]] = []
        for k in keys:
            for ws in self._subs.get(k, []):
                conns.append((ws, k))
        if not conns:
            return

        async def _send(ws: WebSocket, sub_key: str):
            try:
                await ws.send_json(data)
            except Exception:
                # 使用连接注册时的 key 断开，而非外层 key，避免多 key 广播时
                # 死 socket 残留在 project_key 列表中导致后续广播持续失败
                self.disconnect(sub_key, ws)

        await asyncio.gather(*[_send(ws, k) for ws, k in conns], return_exceptions=True)


_ws = _WsManager()
