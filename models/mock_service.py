from __future__ import annotations

from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)


class MockServiceSource(str, Enum):
    MANUAL = "manual"
    CAPTURED = "captured"
    IMPORTED = "imported"


class MockRouteMode(str, Enum):
    STATIC = "static"
    RULE_BASED = "rule_based"
    PROXY_FALLBACK = "proxy_fallback"


class TrafficSourceType(str, Enum):
    SERVICE_FORWARD = "service_forward"
    APP_CAPTURE = "app_capture"
    MITMPROXY = "mitmproxy"


class TrafficDirection(str, Enum):
    REQUEST = "request"
    RESPONSE = "response"
    BOTH = "both"


class TrafficAction(str, Enum):
    RECORD = "record"
    PASS_THROUGH = "pass_through"
    DROP = "drop"
    MODIFY_REQUEST = "modify_request"
    MODIFY_RESPONSE = "modify_response"
    MOCK_RESPONSE = "mock_response"


class MockCondition(BaseModel):
    target: str = "query"  # query/header/cookie/body_field/jsonpath/method/host/path/status_code
    key: str = ""
    operator: str = "equals"  # equals/contains/regex/exists/in/gt/gte/lt/lte
    value: Any = None


class TrafficPatch(BaseModel):
    target: str = "header"  # header/query/body_jsonpath/body/status_code/response_body
    key: str = ""
    value: Any = None


class MockMatch(BaseModel):
    conditions: list[MockCondition] = Field(default_factory=list)
    query: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, Any] = Field(default_factory=dict)
    body_jsonpath: dict[str, Any] = Field(default_factory=dict)
    body_fields: dict[str, Any] = Field(default_factory=dict)
    cookies: dict[str, Any] = Field(default_factory=dict)


class MockResponseConfig(BaseModel):
    status_code: int = 200
    headers: dict[str, str] = Field(default_factory=lambda: {"content-type": "application/json"})
    body_type: str = "json"
    body: Any = Field(default_factory=lambda: {"message": "mock response"})
    body_template: str = ""
    latency_ms: int = 0
    source: str = "static"  # static / template / mock_data
    mock_data: dict[str, Any] = Field(default_factory=dict)
    sql_queries: list[dict[str, Any]] = Field(default_factory=list)


class MockResponseCase(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = ""
    enabled: bool = True
    priority: int = 100
    conditions: list[MockCondition] = Field(default_factory=list)
    response: MockResponseConfig = Field(default_factory=MockResponseConfig)


class MockService(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str = "default"
    name: str
    slug: str
    description: str = ""
    enabled: bool = True
    base_path: str = ""
    public_enabled: bool = False
    access_key: str = ""
    source: MockServiceSource = MockServiceSource.MANUAL
    default_response: MockResponseConfig = Field(default_factory=MockResponseConfig)
    last_called_at: datetime | None = None
    route_count: int = 0
    error_count: int = 0
    created_by: str = ""
    updated_by: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class MockRoute(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str = "default"
    service_id: str
    name: str = ""
    enabled: bool = True
    priority: int = 100
    method: str = "GET"
    path: str = "/"
    match: MockMatch = Field(default_factory=MockMatch)
    response: MockResponseConfig = Field(default_factory=MockResponseConfig)
    response_cases: list[MockResponseCase] = Field(default_factory=list)
    mode: MockRouteMode = MockRouteMode.STATIC
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class MockCallLog(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str = "default"
    service_id: str
    route_id: str = ""
    method: str
    path: str
    matched: bool = False
    request_summary: dict[str, Any] = Field(default_factory=dict)
    response_summary: dict[str, Any] = Field(default_factory=dict)
    case_id: str = ""
    case_name: str = ""
    sql_refs: list[str] = Field(default_factory=list)
    mock_data_summary: dict[str, Any] = Field(default_factory=dict)
    final_response_summary: dict[str, Any] = Field(default_factory=dict)
    status_code: int = 0
    duration_ms: int = 0
    client_ip: str = ""
    created_at: datetime = Field(default_factory=_now)


class TrafficSource(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str = "default"
    name: str
    type: TrafficSourceType = TrafficSourceType.MITMPROXY
    enabled: bool = True
    access_key: str = ""
    record_mode: str = "asset"
    filter_host: str = ""
    filter_url: str = ""
    tags: list[str] = Field(default_factory=list)
    created_by: str = ""
    updated_by: str = ""
    updated_at: datetime = Field(default_factory=_now)


class TrafficRule(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str = "default"
    source_id: str = ""
    name: str = ""
    enabled: bool = True
    priority: int = 100
    direction: TrafficDirection = TrafficDirection.BOTH
    conditions: list[MockCondition] = Field(default_factory=list)
    match: dict[str, Any] = Field(default_factory=dict)
    action: TrafficAction = TrafficAction.PASS_THROUGH
    patch_list: list[TrafficPatch] = Field(default_factory=list)
    patches: dict[str, Any] = Field(default_factory=dict)
    record: bool = True
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class TrafficRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str = "default"
    source_id: str = ""
    source_type: str = ""
    method: str = "GET"
    url: str = ""
    path: str = "/"
    request: dict[str, Any] = Field(default_factory=dict)
    response: dict[str, Any] = Field(default_factory=dict)
    trace_id: str = ""
    session_id: str = ""
    tags: list[str] = Field(default_factory=list)
    api_id: str = ""
    created_at: datetime = Field(default_factory=_now)
