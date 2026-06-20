"""
环境鉴权模板模型回归。

保证 Environment 可以保存项目级 auth_templates，供设置页和 StepEditor 复用。
"""
from __future__ import annotations

from models.dsl import Environment


def test_environment_auth_templates_default_empty():
    """未配置时默认空数组，避免前端遍历 auth_templates 报错。"""
    env = Environment(name="dev")
    assert env.auth_templates == []


def test_environment_auth_templates_roundtrip():
    """鉴权模板应按原结构序列化，保持与 ScenarioStep.auth 一致。"""
    env = Environment(
        name="dev",
        auth_templates=[
            {"name": "登录 Token", "type": "bearer", "token": "{{steps.login.token}}"},
            {"name": "API Key", "type": "apikey", "key": "X-API-Key", "value": "{{env.API_KEY}}", "in": "header"},
        ],
    )
    data = env.model_dump()
    assert data["auth_templates"][0]["token"] == "{{steps.login.token}}"
    assert data["auth_templates"][1]["key"] == "X-API-Key"
