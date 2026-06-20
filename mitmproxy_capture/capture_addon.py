"""
mitmproxy 抓包插件 —— 实时捕获 API 流量并推送到 ApiPulse 后端。

用法：
    mitmproxy -s mitmproxy_capture/capture_addon.py --set api_pulse_url=http://localhost:8000

或带项目过滤：
    mitmproxy -s mitmproxy_capture/capture_addon.py --set api_pulse_url=http://localhost:8000 --set project_id=my-project

mitmproxy 命令：
    :api_pulse.toggle_capture    # 切换捕获开关
"""
from __future__ import annotations

import json
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from mitmproxy import ctx, http

# 静态资源后缀过滤（与 har_parser/parser.py 保持一致）
_SKIP_EXTENSIONS = {
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".ico", ".woff", ".woff2", ".ttf", ".map", ".webp",
}
_SKIP_CONTENT_TYPES = {
    "text/html", "text/css", "application/javascript",
    "image/", "font/", "video/", "audio/",
}


def _should_skip(flow: http.HTTPFlow) -> bool:
    """过滤静态资源请求"""
    parsed = urlparse(flow.request.pretty_url)
    # 过滤静态扩展名
    if "." in parsed.path:
        ext = "." + parsed.path.rsplit(".", 1)[-1].lower()
        if ext in _SKIP_EXTENSIONS:
            return True
    # 过滤静态 Content-Type
    # flow.response 可能在请求阶段为 None（如连接失败），此时不跳过
    if flow.response is None:
        return False
    content_type = ""
    for k, v in flow.response.headers.items(multi=True):
        if k.lower() == "content-type":
            content_type = v.lower()
            break
    for skip_ct in _SKIP_CONTENT_TYPES:
        if content_type.startswith(skip_ct):
            return True
    return False


def _flow_to_payload(flow: http.HTTPFlow, project_id: str, source_id: str = "", access_key: str = "") -> dict | None:
    """将 mitmproxy flow 转为后端 ingest 接口所需的 payload"""
    try:
        req = flow.request
        resp = flow.response
        if not resp:
            return None

        # 请求体处理
        body = None
        body_type = "none"
        if req.content:
            ct = req.headers.get("content-type", "")
            if "json" in ct:
                try:
                    body = json.loads(req.content.decode("utf-8", errors="replace"))
                    body_type = "json"
                except (json.JSONDecodeError, UnicodeDecodeError):
                    body = req.content.decode("utf-8", errors="replace")
                    body_type = "text"
            elif "x-www-form-urlencoded" in ct:
                from urllib.parse import parse_qs
                parsed = parse_qs(req.content.decode("utf-8", errors="replace"))
                body = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}
                body_type = "form"
            elif "multipart" in ct:
                body_type = "multipart"
            elif "xml" in ct:
                body = req.content.decode("utf-8", errors="replace")
                body_type = "xml"
            else:
                body = req.content.decode("utf-8", errors="replace")
                body_type = "text"

        # 响应体处理
        resp_content_type = resp.headers.get("content-type", "")
        resp_body = None
        if resp.content:
            if "json" in resp_content_type:
                try:
                    resp_body = json.loads(resp.content.decode("utf-8", errors="replace"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    resp_body = resp.content.decode("utf-8", errors="replace")
            else:
                resp_body = resp.content.decode("utf-8", errors="replace")

        return {
            "project_id": project_id,
            "source_id": source_id,
            "source_type": "mitmproxy",
            "access_key": access_key,
            "method": req.method,
            "url": req.pretty_url,
            "path": urlparse(req.pretty_url).path,
            "record_mode": "asset",
            "request": {
                "method": req.method,
                "url": req.pretty_url,
                "path": urlparse(req.pretty_url).path,
                "headers": dict(req.headers),
                "body": body,
                "body_type": body_type,
            },
            "response": {
                "status_code": resp.status_code,
                "headers": dict(resp.headers),
                "body": resp_body,
                "latency_ms": int((resp.timestamp_end - req.timestamp_start) * 1000),
            },
        }
    except Exception as e:
        ctx.log.warn(f"Failed to convert flow: {e}")
        return None


def _send_payload(base_url: str, payload: dict):
    """后台线程发送 payload 到后端（不阻塞代理流量）"""
    try:
        import urllib.request
        data = json.dumps(payload, default=str).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/traffic/ingest",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        # 记录失败原因但不阻塞代理流量
        import logging
        logging.getLogger("mitmproxy.ApiPulseCapture").debug(f"send_payload failed: {e}")


def _fetch_proxy_config(base_url: str, project_id: str, source_id: str, access_key: str) -> dict:
    try:
        import urllib.parse
        import urllib.request
        qs = urllib.parse.urlencode({"project_id": project_id, "source_id": source_id, "access_key": access_key})
        req = urllib.request.Request(f"{base_url}/traffic/proxy-config?{qs}", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data
    except Exception as e:
        import logging
        logging.getLogger("mitmproxy.ApiPulseCapture").debug(f"fetch_proxy_config failed: {e}")
        return {"rules": [], "error": str(e)}


def _json_body(req_or_resp) -> object | None:
    try:
        ct = req_or_resp.headers.get("content-type", "")
        if "json" not in ct or not req_or_resp.content:
            return None
        return json.loads(req_or_resp.content.decode("utf-8", errors="replace"))
    except Exception:
        return None


def _set_json_path(data: object, path: str, value):
    if not isinstance(data, dict) or not path:
        return
    parts = path.replace("$.", "").split(".")
    cur = data
    for part in parts[:-1]:
        if not isinstance(cur, dict):
            return
        cur = cur.setdefault(part, {})
    if isinstance(cur, dict) and parts[-1]:
        cur[parts[-1]] = value


def _matches_rule(rule: dict, flow: http.HTTPFlow, phase: str) -> bool:
    if not rule.get("enabled", True):
        return False
    direction = rule.get("direction", "both")
    if direction not in {"both", phase}:
        return False
    req = flow.request
    parsed = urlparse(req.pretty_url)
    match = rule.get("match") or {}
    conditions = list(rule.get("conditions") or [])
    if not conditions:
        if match.get("method"):
            conditions.append({"target": "method", "operator": "equals", "value": match["method"]})
        if match.get("host"):
            conditions.append({"target": "host", "operator": "contains", "value": match["host"]})
        if match.get("path"):
            conditions.append({"target": "path", "operator": "contains", "value": match["path"]})
        for key, val in (match.get("headers") or {}).items():
            conditions.append({"target": "header", "key": key, "operator": "equals", "value": val})
        for key, val in (match.get("query") or {}).items():
            conditions.append({"target": "query", "key": key, "operator": "equals", "value": val})
        if match.get("status_code"):
            conditions.append({"target": "status_code", "operator": "equals", "value": match["status_code"]})
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for condition in conditions:
        target = str(condition.get("target") or "")
        key = str(condition.get("key") or "")
        operator = str(condition.get("operator") or "equals")
        expected = condition.get("value")
        if target == "method":
            actual = req.method.upper()
        elif target == "host":
            actual = parsed.netloc
        elif target == "path":
            actual = parsed.path
        elif target == "query":
            actual = query.get(key)
        elif target == "header":
            actual = req.headers.get(key)
        elif target == "jsonpath":
            actual = None
            body = _json_body(req)
            if isinstance(body, dict):
                cur = body
                for part in key.replace("$.", "").split("."):
                    cur = cur.get(part) if isinstance(cur, dict) else None
                actual = cur
        elif target == "status_code" and flow.response:
            actual = flow.response.status_code
        else:
            actual = None
        if operator == "exists":
            passed = actual is not None
        elif operator == "contains":
            passed = str(expected) in str(actual or "")
        elif operator == "regex":
            import re
            try:
                passed = re.search(str(expected), str(actual or "")) is not None
            except re.error:
                passed = False
        elif operator == "in":
            values = expected if isinstance(expected, list) else [v.strip() for v in str(expected).split(",")]
            passed = str(actual) in {str(v) for v in values}
        else:
            passed = str(actual) == str(expected)
        if not passed:
            return False
    return True


def _apply_request_patches(flow: http.HTTPFlow, patches: dict):
    req = flow.request
    for key, val in (patches.get("headers") or {}).items():
        if val is None:
            req.headers.pop(key, None)
        else:
            req.headers[key] = str(val)
    parsed = urlparse(req.pretty_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key, val in (patches.get("query") or {}).items():
        if val is None:
            query.pop(key, None)
        else:
            query[key] = str(val)
    req.url = urlunparse(parsed._replace(query=urlencode(query)))
    body = _json_body(req)
    if body is not None:
        for path, val in (patches.get("body_jsonpath") or {}).items():
            _set_json_path(body, path, val)
            req.text = json.dumps(body, ensure_ascii=False)


def _merge_patch_list(patches: dict, patch_list: list[dict]) -> dict:
    merged = {
        "headers": dict(patches.get("headers") or {}),
        "query": dict(patches.get("query") or {}),
        "body_jsonpath": dict(patches.get("body_jsonpath") or {}),
    }
    for patch in patch_list or []:
        target = patch.get("target")
        key = patch.get("key") or ""
        value = patch.get("value")
        if target in {"header", "headers"}:
            merged["headers"][key] = value
        elif target == "query":
            merged["query"][key] = value
        elif target in {"body_jsonpath", "jsonpath"}:
            merged["body_jsonpath"][key] = value
        elif target in {"body", "response_body"}:
            merged["response_body"] = value
        elif target == "status_code":
            merged["status_code"] = value
    for key, value in patches.items():
        if key not in merged or value not in ({}, None, ""):
            merged[key] = value
    return merged


def _apply_response_patches(flow: http.HTTPFlow, patches: dict):
    if not flow.response:
        return
    resp = flow.response
    if patches.get("status_code"):
        resp.status_code = int(patches["status_code"])
    for key, val in (patches.get("headers") or {}).items():
        if val is None:
            resp.headers.pop(key, None)
        else:
            resp.headers[key] = str(val)
    if "response_body" in patches:
        body = patches["response_body"]
        resp.text = json.dumps(body, ensure_ascii=False) if isinstance(body, (dict, list)) else str(body)
        resp.headers["content-type"] = "application/json" if isinstance(body, (dict, list)) else resp.headers.get("content-type", "text/plain")


class ApiPulseCapture:
    """mitmproxy 插件：实时捕获 API 流量→推送到 ApiPulse 后端"""

    def __init__(self):
        self.api_pulse_url = "http://localhost:8000"
        self.project_id = "default"
        self.source_id = ""
        self.access_key = ""
        self._capture_enabled = True
        # 抓包域名/URL 过滤：仅采集匹配的请求，空字符串表示不过滤
        self._filter_host: str = ""
        self._filter_url: str = ""
        self._rules: list[dict] = []
        self._config_version = ""
        self._last_config_fetch = 0.0
        self._skip_record_flow_ids: set[str] = set()

    def load(self, loader):
        loader.add_option(
            "api_pulse_url", str, "http://localhost:8000",
            "ApiPulse 后端地址",
        )
        loader.add_option(
            "project_id", str, "default",
            "目标项目 ID",
        )
        loader.add_option("source_id", str, "", "ApiPulse 流量来源 ID")
        loader.add_option("access_key", str, "", "ApiPulse 流量来源 Access Key")
        # 抓包过滤选项：按域名/URL 关键字过滤，仅采集匹配的请求
        loader.add_option(
            "filter_host", str, "",
            "仅捕获匹配此域名的请求（子串匹配，留空不过滤）",
        )
        loader.add_option(
            "filter_url", str, "",
            "仅捕获 URL 中包含此关键字的请求（子串匹配，留空不过滤）",
        )

    def configure(self, updates):
        if "api_pulse_url" in updates:
            self.api_pulse_url = updates["api_pulse_url"]
        if "project_id" in updates:
            self.project_id = updates["project_id"]
        if "source_id" in updates:
            self.source_id = updates["source_id"]
        if "access_key" in updates:
            self.access_key = updates["access_key"]
        if "filter_host" in updates:
            self._filter_host = updates["filter_host"]
        if "filter_url" in updates:
            self._filter_url = updates["filter_url"]

    def running(self):
        filter_info = ""
        if self._filter_host or self._filter_url:
            filter_info = f" filter_host={self._filter_host or '-'} filter_url={self._filter_url or '-'}"
        ctx.log.info(
            f"ApiPulse capture addon ready → {self.api_pulse_url}/traffic/ingest "
            f"(project={self.project_id} source={self.source_id or '-'}){filter_info}"
        )
        self._refresh_rules(force=True)

    def _refresh_rules(self, force: bool = False):
        if not force and time.time() - self._last_config_fetch < 10:
            return
        self._last_config_fetch = time.time()
        config = _fetch_proxy_config(self.api_pulse_url, self.project_id, self.source_id, self.access_key)
        self._rules = config.get("rules") or []
        self._config_version = config.get("version") or self._config_version

    def _matched_rules(self, flow: http.HTTPFlow, phase: str) -> list[dict]:
        self._refresh_rules()
        return [rule for rule in self._rules if _matches_rule(rule, flow, phase)]

    def request(self, flow: http.HTTPFlow):
        """请求阶段应用 drop / modify_request / mock_response 规则。"""
        for rule in self._matched_rules(flow, "request"):
            action = rule.get("action")
            patches = _merge_patch_list(rule.get("patches") or {}, rule.get("patch_list") or [])
            if action == "drop":
                flow.response = http.Response.make(403, b"Blocked by ApiPulse traffic rule", {"content-type": "text/plain"})
                self._skip_record_flow_ids.add(flow.id)
                return
            if action == "modify_request":
                _apply_request_patches(flow, patches)
            if action == "mock_response":
                status = int(patches.get("status_code") or 200)
                body = patches.get("response_body", {"message": "mock response"})
                content = json.dumps(body, ensure_ascii=False).encode("utf-8") if isinstance(body, (dict, list)) else str(body).encode("utf-8")
                headers = patches.get("headers") or {"content-type": "application/json"}
                flow.response = http.Response.make(status, content, headers)
                if not bool(rule.get("record", False)):
                    self._skip_record_flow_ids.add(flow.id)
                return

    def response(self, flow: http.HTTPFlow):
        """每个 HTTP 响应到达时触发，后台线程异步发送不阻塞代理"""
        if not self._capture_enabled:
            return
        if flow.id in self._skip_record_flow_ids:
            self._skip_record_flow_ids.discard(flow.id)
            return
        if _should_skip(flow):
            return

        # 域名/URL 关键字过滤：不匹配则跳过，不发送到后端
        parsed = urlparse(flow.request.pretty_url)
        if self._filter_host and self._filter_host.lower() not in parsed.netloc.lower():
            return
        if self._filter_url and self._filter_url.lower() not in flow.request.pretty_url.lower():
            return

        should_record = True
        for rule in self._matched_rules(flow, "response"):
            action = rule.get("action")
            if action == "modify_response":
                _apply_response_patches(flow, _merge_patch_list(rule.get("patches") or {}, rule.get("patch_list") or []))
            if action == "drop":
                should_record = False
            if action in {"pass_through", "record"}:
                should_record = bool(rule.get("record", True))
        if not should_record:
            return

        payload = _flow_to_payload(flow, self.project_id, self.source_id, self.access_key)
        if payload is None:
            return

        # 捕获实例属性到闭包变量，避免线程中访问 self 的竞态
        base_url = self.api_pulse_url
        t = threading.Thread(target=_send_payload, args=(base_url, payload), daemon=True)
        t.start()

    # mitmproxy 命令: api_pulse.toggle_capture
    def toggle_capture(self):
        self._capture_enabled = not self._capture_enabled
        state = "ON" if self._capture_enabled else "OFF"
        ctx.log.info(f"ApiPulse capture: {state}")
        return state


# mitmproxy addon 注册入口
addons = [ApiPulseCapture()]
