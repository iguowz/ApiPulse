"""
cURL 命令解析器 —— 将 cURL 命令字符串解析为 ApiDSL 结构
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import shlex
from urllib.parse import urlparse, parse_qs

from models.dsl import ApiDSL, RequestDSL, ResponseDSL, HttpMethod, BodyType


def _shell_split(cmd: str) -> list[str]:
    """安全地将 cURL 命令字符串拆分为 token 列表"""
    # 移除换行符和续行符（反斜杠+换行）
    cmd = re.sub(r'\\\s*\n\s*', ' ', cmd)
    try:
        return shlex.split(cmd)
    except ValueError:
        # shlex 失败时用正则简易分割（处理引号不闭合等情况）
        tokens = []
        for m in re.finditer(r"""(?:[^\s"']+|"[^"]*"|'[^']*')+""", cmd):
            token = m.group(0)
            if (token.startswith('"') and token.endswith('"')) or \
               (token.startswith("'") and token.endswith("'")):
                token = token[1:-1]
            tokens.append(token)
        return tokens


def parse_curl(curl_command: str) -> ApiDSL:
    """
    解析 cURL 命令字符串，返回 ApiDSL 对象。

    支持解析：
    - URL（含查询参数）
    - HTTP 方法（-X / --request）
    - 请求头（-H / --header）
    - 请求体（-d / --data / --data-raw）
    - Basic Auth（-u / --user）
    """
    tokens = _shell_split(curl_command)

    # 找 curl 关键字位置，从其之后开始解析
    start = 0
    for i, t in enumerate(tokens):
        if t.lower() == 'curl':
            start = i + 1
            break

    method = HttpMethod.GET  # 默认 GET
    url = ""
    headers: dict[str, str] = {}
    body: str | None = None
    body_type = BodyType.NONE

    i = start
    while i < len(tokens):
        token = tokens[i]

        # 跳过管道、重定向等 shell 操作符
        if token in ('|', '>', '>>', '2>', '&>'):
            break

        # HTTP 方法
        if token in ('-X', '--request'):
            if i + 1 < len(tokens):
                method_str = tokens[i + 1].upper()
                try:
                    method = HttpMethod(method_str)
                except ValueError:
                    # 如果是合法 HTTP 方法但不在枚举中，保留原始 GET
                    pass
                i += 2
                continue

        # 请求头
        elif token in ('-H', '--header'):
            if i + 1 < len(tokens):
                header_str = tokens[i + 1]
                if ':' in header_str:
                    key, _, val = header_str.partition(':')
                    headers[key.strip()] = val.strip()
                i += 2
                continue

        # Basic Auth
        elif token in ('-u', '--user'):
            if i + 1 < len(tokens):
                user_pwd = tokens[i + 1]
                encoded = base64.b64encode(user_pwd.encode()).decode()
                headers['Authorization'] = f'Basic {encoded}'
                i += 2
                continue

        # 请求体 (-d / --data / --data-raw)
        elif token in ('-d', '--data', '--data-raw', '--data-binary'):
            if i + 1 < len(tokens):
                body = tokens[i + 1]
                # 检查是否已有 Content-Type
                has_ct = any(k.lower() == 'content-type' for k in headers)
                if not has_ct:
                    # 尝试检测 JSON
                    body_stripped = body.strip()
                    if body_stripped.startswith('{') or body_stripped.startswith('['):
                        headers['Content-Type'] = 'application/json'
                        body_type = BodyType.JSON
                    elif '=' in body:
                        headers['Content-Type'] = 'application/x-www-form-urlencoded'
                        body_type = BodyType.FORM
                    else:
                        body_type = BodyType.TEXT
                else:
                    # 已存在 Content-Type，按现有头推断 body_type
                    ct_lower = next((v.lower() for k, v in headers.items() if k.lower() == 'content-type'), '')
                    if 'json' in ct_lower:
                        body_type = BodyType.JSON
                    elif 'form' in ct_lower:
                        body_type = BodyType.FORM
                    elif 'xml' in ct_lower:
                        body_type = BodyType.XML
                    elif 'multipart' in ct_lower:
                        body_type = BodyType.MULTIPART
                    else:
                        body_type = BodyType.TEXT
                i += 2
                continue

        # 压缩/静默等标志（无参数，跳过）
        elif token in ('--compressed', '-s', '--silent', '-k', '--insecure',
                       '-L', '--location', '-v', '--verbose', '--http1.1',
                       '--http2', '--http2-prior-knowledge', '--fail', '-f'):
            i += 1
            continue

        # 有参数但不需要解析的选项：-o/--output, -w/--write-out, --connect-timeout, --max-time
        elif token in ('-o', '--output', '-w', '--write-out', '--connect-timeout',
                       '--max-time', '-m', '--retry', '--retry-delay', '--retry-max-time',
                       '-b', '--cookie', '-c', '--cookie-jar', '-e', '--referer',
                       '-A', '--user-agent', '--proxy', '-x', '--cacert', '--cert',
                       '--key', '--limit-rate', '--max-redirs'):
            i += 2  # 跳过选项及其值
            continue

        # 不认识的以 - 开头的是标志，跳过
        elif token.startswith('-'):
            i += 1
            continue

        # 其他 token 视为 URL
        else:
            url = token
            i += 1

    if not url:
        raise ValueError("No URL found in curl command")

    # 分离 URL 中的路径和查询参数
    parsed = urlparse(url)
    path = parsed.path or '/'
    query_params: dict[str, str] = {}
    if parsed.query:
        for k, v_list in parse_qs(parsed.query).items():
            query_params[k] = v_list[0] if len(v_list) == 1 else v_list

    # 构建 RequestDSL
    request = RequestDSL(
        method=method,
        url=url,
        path=path,
        query_params=query_params,
        headers=headers,
        body=body,
        body_type=body_type,
    )

    # 基于 method + url（不含 query）+ body 结构生成去重指纹
    parsed_url = urlparse(url)
    url_no_query = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"
    raw_hash = f"{method.value}|{url_no_query}|{json.dumps(body, sort_keys=True, default=str)}"
    source_hash = hashlib.sha256(raw_hash.encode()).hexdigest()[:16]

    return ApiDSL(
        name=f"{method.value} {path}",
        source_har="curl://import",
        source_hash=source_hash,
        request=request,
        response=ResponseDSL(status_code=0),
    )
