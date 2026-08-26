from pathlib import Path


COMPONENT = Path(__file__).resolve().parents[1] / "frontend/src/components/AiAssistant.vue"


def _source() -> str:
    return COMPONENT.read_text(encoding="utf-8")


def test_ai_assistant_persists_sessions_per_project():
    """AI 助手本地会话键应带 project_id，避免刷新后串项目。"""
    source = _source()

    assert "const currentProjectId = () => projectStore.current || 'default'" in source
    assert "ai_assistant_session_id:${projectId || 'default'}" in source
    assert "ai-chat-messages:${projectId || 'default'}" in source
    assert "localStorage.removeItem(LEGACY_SESSION_KEY)" in source
    assert "sessionStorage.removeItem(LEGACY_MESSAGES_KEY)" in source


def test_ai_assistant_resets_session_when_project_changes():
    """项目变化必须进入 watch key，触发清空 session_id 与消息。"""
    source = _source()

    assert "context.value.project_id || 'default'" in source
    assert "messages.value = []" in source
    assert "sessionId.value = ''" in source


def test_ai_assistant_clear_history_sends_project_id():
    """清空历史时后端需要当前项目用于 L3 归档。"""
    source = _source()

    assert "?project_id=${encodeURIComponent(projectId)}" in source
    assert "encodeURIComponent(clearingSessionId)" in source
