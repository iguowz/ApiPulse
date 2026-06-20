"""P2-4: API Mock 生成 + 契约校验测试"""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock

from api.routers.apis import create_mock_case, get_mock_response, list_mock_cases
from services.api_service import ApiService
from models.dsl import ApiDSL, ApiDoc, ParamDoc, HttpMethod, RequestDSL, ResponseDSL


def _rf(name, type="string", example=None):
    return ParamDoc(name=name, location="body", type=type, example=example)


def _make_api(response_fields=None):
    return ApiDSL(
        id="api-1", name="test",
        request=RequestDSL(method=HttpMethod.GET, url="http://x.test/a"),
        response=ResponseDSL(status_code=200),
        doc=ApiDoc(response_fields=response_fields or []),
    )


def test_generate_mock_uses_example_values():
    api = _make_api([_rf("code", "integer", 0), _rf("message", "string", "success")])
    mock = ApiService.generate_mock(api)
    assert mock["body"]["code"] == 0
    assert mock["body"]["message"] == "success"
    assert mock["status_code"] == 200


def test_generate_mock_uses_type_defaults_when_no_example():
    api = _make_api([_rf("count", "integer"), _rf("name", "string"), _rf("active", "boolean"), _rf("items", "array")])
    mock = ApiService.generate_mock(api)
    assert mock["body"]["count"] == 0
    assert mock["body"]["name"] == ""
    assert mock["body"]["active"] is False
    assert mock["body"]["items"] == []


def test_generate_mock_handles_nested_fields():
    api = _make_api([_rf("data.user.id", "integer", 42), _rf("data.user.name", "string", "alice")])
    mock = ApiService.generate_mock(api)
    assert mock["body"]["data"]["user"]["id"] == 42
    assert mock["body"]["data"]["user"]["name"] == "alice"


def test_generate_mock_empty_response_fields():
    api = _make_api([])
    mock = ApiService.generate_mock(api)
    assert "message" in mock["body"]


def test_generate_mock_override_status_code():
    api = _make_api([_rf("code", "integer")])
    assert ApiService.generate_mock(api, status_code=404)["status_code"] == 404


def test_check_contract_detects_missing_fields():
    api = _make_api([_rf("code", "integer"), _rf("data.token", "string")])
    result = ApiService.check_contract({"code": 0}, api)
    assert result["passed"] is False
    assert "data.token" in result["missing_fields"]


def test_check_contract_detects_type_mismatch():
    api = _make_api([_rf("code", "integer")])
    result = ApiService.check_contract({"code": "zero"}, api)
    assert result["passed"] is False
    assert result["type_mismatches"][0]["field"] == "code"


def test_check_contract_detects_extra_fields():
    api = _make_api([_rf("code", "integer")])
    result = ApiService.check_contract({"code": 0, "extra": "x"}, api)
    assert "extra" in result["extra_fields"]


def test_check_contract_all_match_passes():
    api = _make_api([_rf("code", "integer"), _rf("message", "string")])
    result = ApiService.check_contract({"code": 0, "message": "ok"}, api)
    assert result["passed"] is True
    assert "通过" in result["summary"]


def test_type_matches_bool_not_integer():
    assert ApiService._type_matches(True, "boolean") is True
    assert ApiService._type_matches(True, "integer") is False
    assert ApiService._type_matches(42, "integer") is True


def test_flatten_body_nested():
    flat = ApiService._flatten_body({"data": {"user": {"id": 1}}, "code": 0})
    assert flat["data.user.id"] == 1
    assert flat["code"] == 0


def test_flatten_body_array_first_element():
    flat = ApiService._flatten_body({"items": [{"id": 1, "name": "a"}, {"id": 2}]})
    assert flat["items[0].id"] == 1
    assert flat["items[0].name"] == "a"


def test_check_contract_summary_format():
    api = _make_api([_rf("code", "integer"), _rf("missing_field", "string")])
    result = ApiService.check_contract({"code": "wrong"}, api)
    assert "缺失" in result["summary"]
    assert "类型不匹配" in result["summary"]


class _FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, *_args, **_kwargs):
        return self

    async def to_list(self, length=None):
        return self.docs[:length] if length else self.docs


@pytest.mark.asyncio
async def test_api_mock_case_create_list_and_use_saved_response():
    """API mock case 可保存多个版本，并通过 case_id 优先返回保存响应。"""
    api_doc = _make_api([_rf("code", "integer", 0)])
    api_doc.project_id = "default"
    api_col = AsyncMock()
    api_col.find_one = AsyncMock(return_value=api_doc.model_dump())
    inserted = {}

    class CaseCol:
        async def insert_one(self, doc):
            inserted.update(doc)

        def find(self, *_args, **_kwargs):
            return _FakeCursor([inserted])

        async def find_one(self, *_args, **_kwargs):
            return inserted

    db = MagicMock()
    db.__getitem__ = MagicMock(side_effect=lambda key: {"api_dsls": api_col, "api_mock_cases": CaseCol()}[key])
    user = {"username": "tester", "role": "admin", "project_id": "default"}

    created = await create_mock_case(
        "api-1",
        body={"name": "成功响应", "response": {"status_code": 201, "body": {"ok": True}}},
        current_user=user,
        db=db,
    )
    listed = await list_mock_cases("api-1", current_user=user, db=db)
    response = await get_mock_response("api-1", case_id=created["id"], current_user=user, db=db)

    assert created["name"] == "成功响应"
    assert listed["items"][0]["id"] == created["id"]
    assert response["case_id"] == created["id"]
    assert response["status_code"] == 201
    assert response["body"] == {"ok": True}
