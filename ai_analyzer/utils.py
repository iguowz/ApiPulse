"""
ai_analyzer 共享工具函数
"""
from __future__ import annotations

import asyncio
import ast
import json
import re
from typing import Any, Coroutine

from loguru import logger


def safe_fire_and_forget(coro: Coroutine, name: str = "") -> None:
    """
    安全地触发 fire-and-forget 协程：异常通过 add_done_callback 记录到日志，
    而非静默吞没。适用于 memory.record_l2、knowledge.extract 等不影响主流程的
    辅助任务。
    """
    task = asyncio.create_task(coro)
    task.add_done_callback(_log_task_exception(name))
    # 不返回 task，调用方无需管理


def _log_task_exception(name: str):
    """返回一个 done_callback，仅在 task 以异常结束时记录日志"""
    tag = f"fire-and-forget:{name}" if name else "fire-and-forget"

    def _callback(task: asyncio.Task):
        try:
            task.result()  # 如果内部抛异常，这里会重新抛出
        except asyncio.CancelledError:
            logger.debug("[{}] task cancelled", tag)
        except Exception:
            logger.exception("[{}] task failed with unhandled exception", tag)
    return _callback


def safe_parse_json(text: str) -> Any:
    """
    安全解析 LLM 返回的非标准 JSON。

    处理流程（按序）：
    1. 去掉 markdown 围栏 ```json ... ``` 或 ``` ... ```
    2. 去掉独立行注释（// 和 #）
    3. 去掉 trailing commas（} 或 ] 前的逗号）
    4. 替换 Python literals：None→null, True→true, False→false
    5. 尝试 json.loads，失败则用 ast.literal_eval 兜底
    6. 单引号→双引号后 json.loads
    7. 从混合文本中提取 JSON 片段（定位 { } 或 [ ] 边界）
    8. 尝试闭合截断的 JSON（auto-close unclosed braces/brackets）
    """
    text = text.strip()

    # 1. 去掉 markdown 围栏 ```json...``` 或 ```...```
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[-1].strip() == "```":
            # 首尾都有围栏标记 → 去掉首行和末行
            text = "\n".join(lines[1:-1])
        else:
            # 只有开头围栏标记（可能末端被截断）→ 仅去掉首行
            text = "\n".join(lines[1:])

    text = text.strip()

    # 空文本无法解析，尽早抛出明确错误
    if not text:
        raise ValueError("Empty text after stripping markdown fences")

    # 2. 去掉单行注释（仅处理以 // 或 # 开头的独立行，不处理行尾注释避免误伤 URL）
    lines = text.split("\n")
    lines = [
        l for l in lines
        if not (l.strip().startswith("//") or l.strip().startswith("#"))
    ]
    text = "\n".join(lines)

    # 3. 去掉 trailing commas（} 或 ] 前的逗号）
    text = re.sub(r",\s*([}\]])", r"\1", text)

    # 4. 替换 Python literals: None/True/False → null/true/false
    #    用词边界匹配，避免替换字段名中的这些词
    text = re.sub(r"\bNone\b", "null", text)
    text = re.sub(r"\bTrue\b", "true", text)
    text = re.sub(r"\bFalse\b", "false", text)

    cleaned = text.strip()

    # 5. 尝试标准 json.loads → 最快路径，大多数正常输出在此成功
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # 非标准 JSON → 继续后续修复步骤
        pass

    # 6. 兜底：尝试用 ast.literal_eval 解析 Python dict 字面量，再转标准 JSON
    #    处理 LLM 输出 Python 风格 dict（单引号键、None/True/False 等）
    try:
        parsed = ast.literal_eval(cleaned)
        # 将 Python 对象转为 JSON 兼容格式再 parse 回来，确保类型一致
        return json.loads(json.dumps(parsed, default=str))
    except (ValueError, SyntaxError):
        # ast 解析失败 → 文本不是合法 Python 字面量，继续后续修复
        pass

    # 7. 尝试将单引号替换为双引号后再解析
    #    只处理 JSON 键值对中常见的单引号模式，谨慎避免误伤字符串内容中的单引号
    try:
        fixed = _fix_single_quotes(cleaned)
        return json.loads(fixed)
    except json.JSONDecodeError:
        # 单引号修复后仍失败 → 可能文本中混入了非 JSON 内容
        pass

    # 8. 从混合文本中提取 JSON 片段：定位 { } 或 [ ] 边界
    #    小模型（如 Qwen2.5-3B）可能输出解释文字+JSON 混合内容，
    #    或 max_tokens 截断导致 JSON 不完整
    try:
        extracted = _extract_json_fragment(cleaned)
        if extracted:
            # 对提取的片段重新执行步骤 3-7 的预处理
            extracted = re.sub(r",\s*([}\]])", r"\1", extracted)
            extracted = re.sub(r"\bNone\b", "null", extracted)
            extracted = re.sub(r"\bTrue\b", "true", extracted)
            extracted = re.sub(r"\bFalse\b", "false", extracted)
            try:
                return json.loads(extracted)
            except json.JSONDecodeError:
                # 标准解析失败 → 单引号修复后再试
                try:
                    return json.loads(_fix_single_quotes(extracted))
                except json.JSONDecodeError:
                    # 提取片段仍无法解析 → 继续后续兜底步骤
                    pass
        # extracted 为 None（未找到 JSON 边界）→ 静默跳过，进入下一步
    except Exception:
        # _extract_json_fragment 本身异常 → 静默跳过，不阻塞后续兜底
        pass

    # 9. 尝试闭合截断的 JSON（LLM 输出被 max_tokens 截断时常见）
    #    统计未闭合的 {/[ 数量，补上对应的 }/] 后再解析
    try:
        closed = _auto_close_json(cleaned)
        if closed != cleaned:
            # _auto_close_json 做了修改 → 尝试解析闭合后的结果
            try:
                return json.loads(closed)
            except json.JSONDecodeError:
                # 标准解析失败 → 单引号修复后再试
                try:
                    return json.loads(_fix_single_quotes(closed))
                except json.JSONDecodeError:
                    # 闭合后仍无法解析 → 继续最后兜底
                    pass
        # closed == cleaned：未做任何闭合修改 → 无帮助，跳过
    except Exception:
        # _auto_close_json 本身异常 → 静默跳过
        pass

    # 10. 最后兜底：从混合/损坏的文本中逐个提取 {…} 对象，组装成数组返回。
    #     适用于 LLM 输出数组 [{…}, {…}] 部分损坏（截断/混入非 JSON 文本）的场景。
    #     每个 {…} 对象独立提取、独立解析，收集所有成功解析的对象。
    try:
        objects = _extract_objects_individually(cleaned)
        if objects:
            return objects
        # objects 为空列表（一个对象也没提取到）→ 进入最终报错
    except Exception:
        # 逐个提取过程异常 → 进入最终报错
        pass

    logger.warning("_safe_parse_json: all 10 parse attempts failed for text[:200]={}", text[:200])
    raise ValueError(f"Failed to parse non-standard JSON: {text[:200]}")


def _fix_single_quotes(text: str) -> str:
    """
    将 JSON 中的单引号字符串替换为双引号。

    策略：逐字符扫描，跟踪 JSON 结构上下文，
    只在 JSON 字符串值的位置才将单引号替换为双引号。
    对已用双引号包裹的字符串不重复处理。
    """
    result = []
    i = 0
    in_double_quote = False
    in_single_quote = False
    escape = False

    while i < len(text):
        ch = text[i]

        if escape:
            # 转义字符：直接保留，重置转义标记（如 \", \' 等不触发引号状态切换）
            result.append(ch)
            escape = False
            i += 1
            continue

        if ch == "\\":
            # 遇到反斜杠：标记下一个字符为转义字符
            result.append(ch)
            escape = True
            i += 1
            continue

        if in_double_quote:
            # 当前在双引号字符串内：保持原样直到遇到闭合双引号
            result.append(ch)
            if ch == '"':
                in_double_quote = False  # 双引号闭合
            i += 1
            continue

        if in_single_quote:
            # 当前在单引号字符串内：保留内容，但将闭合单引号替换为双引号
            if ch == "'":
                in_single_quote = False
                result.append('"')  # 结束的单引号 → 双引号
            else:
                result.append(ch)
            i += 1
            continue

        # 不在任何字符串内 → 检测引号进入状态，或保留普通字符
        if ch == '"':
            # 遇到双引号 → 进入双引号字符串，保持原样
            in_double_quote = True
            result.append(ch)
        elif ch == "'":
            # 遇到单引号 → 进入单引号字符串，将起始单引号替换为双引号
            in_single_quote = True
            result.append('"')  # 开始的单引号 → 双引号
        else:
            # 普通字符 → 直接保留
            result.append(ch)
        i += 1

    return "".join(result)


def _extract_json_fragment(text: str) -> str | None:
    """
    从混合文本中提取最外层的 JSON 对象或数组。

    策略：找到第一个 { 或 [，追踪嵌套深度找到匹配的闭合括号。
    适用于 LLM 在 JSON 前后附加了解释文字的输出。
    返回提取到的片段，失败返回 None。
    """
    # 找到第一个 JSON 起始符（{ 或 [）
    start_idx = -1
    start_char = ""
    for i, ch in enumerate(text):
        if ch in ("{", "["):
            start_idx = i
            start_char = ch
            break
    if start_idx < 0:
        # 文本中完全没有 JSON 起始符 → 无法提取
        return None

    # 追踪嵌套深度找到匹配的闭合括号
    close_char = "}" if start_char == "{" else "]"
    depth = 0
    in_string = False
    escape = False
    for i in range(start_idx, len(text)):
        ch = text[i]
        if escape:
            # 转义字符 → 跳过，不触发引号/括号状态切换
            escape = False
            continue
        if ch == "\\":
            # 反斜杠 → 下一个字符是转义字符
            escape = True
            continue
        if ch == '"':
            # 双引号 → 切换字符串内外状态（括号在字符串内不计数）
            in_string = not in_string
            continue
        if in_string:
            # 字符串内部 → 所有字符原样跳过（包括括号）
            continue
        if ch in ("{", "["):
            # 嵌套开始 → 深度+1
            depth += 1
        elif ch in ("}", "]"):
            # 嵌套结束 → 深度-1
            depth -= 1
            if depth == 0:
                # 回到初始深度 → 找到匹配闭合括号，返回完整片段
                return text[start_idx:i + 1]

    # 未找到匹配闭合括号（文本被截断）
    if depth > 0:
        # 深度>0：有未闭合的括号 → 返回从起始到末尾的截断片段
        return text[start_idx:]
    # depth==0 但未返回（不应出现）→ 返回 None
    return None


def _auto_close_json(text: str) -> str:
    """
    尝试自动闭合截断的 JSON：统计未闭合的 {/[，在末尾补上 }/]。

    同时处理截断在字符串中间的情况（移除末尾不完整的字符串片段）。
    返回闭合后的文本，无法修复时返回原文本。
    """
    # 移除末尾不完整的字符串（截断在字符串中间）
    # 策略：从末尾向前扫描，如果双引号不配对则截断到最后一个配对的引号位置
    in_string = False
    escape = False
    last_string_end = 0
    for i, ch in enumerate(text):
        if escape:
            # 转义字符 → 跳过不触发引号切换
            escape = False
            continue
        if ch == "\\":
            # 反斜杠 → 下一个字符转义
            escape = True
            continue
        if ch == '"':
            # 双引号 → 切换字符串状态
            in_string = not in_string
            if not in_string:
                # 字符串刚刚闭合，记录闭合位置（用于截断回退）
                last_string_end = i + 1

    # 如果仍在字符串内，说明文本在字符串中间被截断
    if in_string:
        # 尝试在末尾加一个引号闭合它
        text = text + '"'
        # 重新扫描验证加引号后是否匹配
        in_string = False
        escape = False
        for i, ch in enumerate(text):
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
        if in_string:
            # 加引号后仍未闭合（说明截断在转义序列中间等复杂情况）
            # → 回退到最后一个完整字符串结束位置
            text = text[:last_string_end]

    # 统计未闭合的括号（忽略字符串内的括号）
    stack: list[str] = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            # 转义字符 → 跳过
            escape = False
            continue
        if ch == "\\":
            # 反斜杠 → 下一个字符转义
            escape = True
            continue
        if ch == '"':
            # 双引号 → 切换字符串内外状态
            in_string = not in_string
            continue
        if in_string:
            # 字符串内 → 括号不参与栈计数
            continue
        if ch in ("{", "["):
            # 开括号 → 入栈
            stack.append(ch)
        elif ch == "}":
            # 闭大括号：仅当栈顶为 { 时配对出栈
            if stack and stack[-1] == "{":
                stack.pop()
        elif ch == "]":
            # 闭中括号：仅当栈顶为 [ 时配对出栈
            if stack and stack[-1] == "[":
                stack.pop()

    # 反向闭合：栈中剩余的未闭合括号，按后进先出的顺序补上闭括号
    closers = []
    for bracket in reversed(stack):
        closers.append("}" if bracket == "{" else "]")

    return text + "".join(closers)


def _extract_objects_individually(text: str) -> list[dict] | None:
    """
    从损坏/混合文本中逐个提取 {…} 对象，独立解析后组装成数组。

    策略：找到每个 { 起始位置，追踪嵌套深度提取完整对象，
    对每个对象独立执行 JSON 修复流水线，收集所有成功解析的对象。
    适用于 LLM 输出 [{…}, {…}] 部分损坏/截断/混入非 JSON 文本的场景。
    """
    objects = []
    i = 0
    while i < len(text):
        # 找下一个 { 起始位置
        brace = text.find("{", i)
        if brace < 0:
            # 没有更多 { → 扫描结束
            break

        # 从该 { 开始追踪嵌套深度，提取完整对象
        depth = 0
        in_string = False
        escape = False
        end = -1
        for j in range(brace, len(text)):
            ch = text[j]
            if escape:
                # 转义字符 → 跳过
                escape = False
                continue
            if ch == "\\":
                # 反斜杠 → 下一个字符转义
                escape = True
                continue
            if ch == '"':
                # 双引号 → 切换字符串状态（括号在字符串内不计数）
                in_string = not in_string
                continue
            if in_string:
                # 字符串内 → 跳过所有字符
                continue
            if ch == "{":
                # 嵌套开始 → 深度+1
                depth += 1
            elif ch == "}":
                # 嵌套结束 → 深度-1
                depth -= 1
                if depth == 0:
                    # 回到初始深度 → 完整对象结束
                    end = j + 1
                    break

        if end > 0:
            # 提取到完整对象（括号配对完整）
            fragment = text[brace:end]
        else:
            # 未找到闭合括号（对象被截断）→ 尝试用 _auto_close_json 修复
            fragment = _auto_close_json(text[brace:])

        # 对片段执行修复流水线，成功则收集
        obj = _parse_single_object(fragment)
        if obj is not None:
            # 解析成功 → 加入结果列表
            objects.append(obj)

        # 移动到下一个搜索位置
        i = end if end > 0 else brace + 1

    return objects if objects else None


def _parse_single_object(fragment: str) -> dict | None:
    """对单个 JSON 对象片段执行修复流水线，成功返回 dict，失败返回 None"""
    fragment = fragment.strip()
    if not fragment:
        return None

    # 修复流水线（与 safe_parse_json 相同）
    fragment = re.sub(r",\s*([}\]])", r"\1", fragment)
    fragment = re.sub(r"\bNone\b", "null", fragment)
    fragment = re.sub(r"\bTrue\b", "true", fragment)
    fragment = re.sub(r"\bFalse\b", "false", fragment)

    # 尝试1: 标准 json.loads → 最快路径
    try:
        return json.loads(fragment)
    except json.JSONDecodeError:
        # 非标准 JSON → 尝试单引号修复
        pass

    # 尝试2: 单引号替换为双引号后解析
    try:
        return json.loads(_fix_single_quotes(fragment))
    except json.JSONDecodeError:
        # 单引号修复仍失败 → 尝试 ast 兜底
        pass

    # 尝试3: ast.literal_eval 解析 Python 字面量 → 转 JSON 兼容格式
    try:
        parsed = ast.literal_eval(fragment)
        return json.loads(json.dumps(parsed, default=str))
    except (ValueError, SyntaxError):
        # 所有修复手段均失败 → 此对象无法解析
        pass

    return None
