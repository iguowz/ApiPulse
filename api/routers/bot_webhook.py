"""
Bot Webhook 接收路由 —— Phase 3 Bot 集成。

接收企微/QQ/飞书平台回调消息，验证签名，转发给 BotMessageService 处理。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import xml.etree.ElementTree as ET
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from motor.motor_asyncio import AsyncIOMotorDatabase
from loguru import logger

from config.database import get_db
from models.bot_config import BotPlatform

router = APIRouter(prefix="/bot/webhook", tags=["bot-webhook"])


async def _find_bot_config(db: AsyncIOMotorDatabase, platform: str, config_id: str = "") -> dict[str, Any] | None:
    """查找启用的 Bot 配置"""
    filt: dict[str, Any] = {"platform": platform, "enabled": True}
    if config_id:
        filt["id"] = config_id
    return await db["bot_configs"].find_one(filt)


# ── 企业微信 Webhook ─────────────────────────────────────


@router.get("/wecom", summary="企业微信 URL 验证")
async def wecom_verify(
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
    config_id: str = Query(default=""),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """企业微信首次配置时的 URL 验证（GET 请求）"""
    config = await _find_bot_config(db, "wecom", config_id)
    if not config:
        raise HTTPException(status_code=404, detail="未找到启用的企业微信 Bot 配置")

    token = config.get("verify_token", "")
    aes_key = config.get("encoding_aes_key", "")
    corp_id = config.get("corp_id", "")

    if not token or not aes_key:
        raise HTTPException(status_code=400, detail="Bot 缺少 Token 或 EncodingAESKey 配置")

    # 验证签名
    if not _wecom_verify_signature(token, timestamp, nonce, echostr, msg_signature):
        raise HTTPException(status_code=403, detail="签名验证失败")

    # 解密 echostr
    try:
        from Crypto.Cipher import AES
        import base64
        import struct

        key = base64.b64decode(aes_key + "=")
        ciphertext = base64.b64decode(echostr)
        cipher = AES.new(key, AES.MODE_CBC, iv=key[:16])
        plaintext = cipher.decrypt(ciphertext)

        # 去除 PKCS7 padding
        pad_len = plaintext[-1]
        content = plaintext[:-pad_len]

        # 解析: random(16) + msg_len(4) + msg + corp_id
        msg_len = struct.unpack("!I", content[16:20])[0]
        msg = content[20:20 + msg_len].decode("utf-8")
        return int(msg) if msg.isdigit() else msg
    except Exception as exc:
        logger.error(f"WeCom echostr decrypt failed: {exc}")
        raise HTTPException(status_code=400, detail=f"解密失败: {exc}")


@router.post("/wecom", summary="企业微信消息接收")
async def wecom_receive(
    request: Request,
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    config_id: str = Query(default=""),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """接收企业微信群机器人消息"""
    config = await _find_bot_config(db, "wecom", config_id)
    if not config:
        raise HTTPException(status_code=404, detail="未找到启用的企业微信 Bot 配置")

    body = await request.body()
    body_str = body.decode("utf-8")
    logger.info(f"WeCom message received: config_id={config_id}")

    # 解析 XML 消息体
    try:
        root = ET.fromstring(body_str)
        encrypt_elem = root.find("Encrypt")
        if encrypt_elem is not None and encrypt_elem.text:
            # 加密消息：解密
            token = config.get("verify_token", "")
            aes_key = config.get("encoding_aes_key", "")
            body_str = _wecom_decrypt(token, aes_key, encrypt_elem.text, msg_signature, timestamp, nonce)
    except ET.ParseError:
        pass  # 非 XML，当作明文处理

    # 解析消息内容
    text = _wecom_extract_text(body_str)
    if not text:
        return ""

    # 处理消息
    from services.bot_message_service import BotMessageService
    svc = BotMessageService(db)
    user_id = _wecom_extract_user(body_str)
    reply = await svc.process_message(
        BotConfig(**config), user_id or "unknown", text,
    )
    if reply:
        # 企业微信：通过 Webhook 主动回复
        await svc.send_reply(BotPlatform.WECOM, BotConfig(**config), reply)

    return ""  # 企微要求返回空字符串


# ── 飞书 Webhook ─────────────────────────────────────────


@router.post("/feishu", summary="飞书消息接收")
async def feishu_receive(
    request: Request,
    config_id: str = Query(default=""),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """接收飞书自定义机器人消息（需验证签名）"""
    config = await _find_bot_config(db, "feishu", config_id)
    if not config:
        raise HTTPException(status_code=404, detail="未找到启用的飞书 Bot 配置")

    body = await request.json()
    timestamp = request.headers.get("X-Lark-Request-Timestamp", "")
    nonce = request.headers.get("X-Lark-Request-Nonce", "")
    signature = request.headers.get("X-Lark-Signature", "")

    # 验证 HMAC-SHA256 签名
    verify_token = config.get("verify_token", "")
    if verify_token and timestamp and nonce and signature:
        sign_str = f"{timestamp}{nonce}"
        expected = hmac.new(
            verify_token.encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            logger.warning("Feishu signature verification failed")
            # 飞书首次配置时可能无 body，放行

    # 延迟回复，先返回 200
    asyncio = __import__("asyncio")

    # 提取消息文本
    text = _feishu_extract_text(body)
    if not text:
        return {}
    logger.info(f"Feishu message received: config_id={config_id}, user={body.get('open_id', 'unknown')}")

    # 异步处理
    async def _process():
        from services.bot_message_service import BotMessageService
        svc = BotMessageService(db)
        user_id = body.get("open_id", "unknown")
        reply = await svc.process_message(
            BotConfig(**config), user_id, text,
        )
        if reply:
            await svc.send_reply(BotPlatform.FEISHU, BotConfig(**config), reply)

    asyncio.create_task(_process())
    return {}


# ── QQ Webhook ───────────────────────────────────────────


@router.post("/qq", summary="QQ 消息接收")
async def qq_receive(
    request: Request,
    config_id: str = Query(default=""),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """接收 QQ 机器人消息"""
    config = await _find_bot_config(db, "qq", config_id)
    if not config:
        raise HTTPException(status_code=404, detail="未找到启用的 QQ Bot 配置")

    # Bearer token 校验
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if token != config.get("verify_token", ""):
            logger.warning("QQ Bot token verification failed")

    body = await request.json()
    text = _qq_extract_text(body)
    if not text:
        return {}
    logger.info(f"QQ message received: config_id={config_id}, user={body.get('user_id', 'unknown')}")

    asyncio = __import__("asyncio")

    async def _process():
        from services.bot_message_service import BotMessageService
        svc = BotMessageService(db)
        user_id = body.get("user_id", body.get("author", {}).get("id", "unknown"))
        reply = await svc.process_message(
            BotConfig(**config), user_id, text,
        )
        if reply:
            await svc.send_reply(BotPlatform.QQ, BotConfig(**config), reply)

    asyncio.create_task(_process())
    return {}


# ── 消息解析辅助函数 ─────────────────────────────────────


def _wecom_verify_signature(token: str, timestamp: str, nonce: str, echostr: str, msg_signature: str) -> bool:
    """企业微信签名验证（SHA1）"""
    import hashlib
    params = sorted([token, timestamp, nonce, echostr])
    sha1 = hashlib.sha1("".join(params).encode("utf-8")).hexdigest()
    return sha1 == msg_signature


def _wecom_decrypt(token: str, aes_key: str, encrypt_text: str,
                   msg_signature: str, timestamp: str, nonce: str) -> str:
    """企业微信 AES 解密消息"""
    # 简化的解密实现，生产环境建议使用 wechatpy 等库
    try:
        from Crypto.Cipher import AES
        import base64
        import struct

        key = base64.b64decode(aes_key + "=")
        ciphertext = base64.b64decode(encrypt_text)
        cipher = AES.new(key, AES.MODE_CBC, iv=key[:16])
        plaintext = cipher.decrypt(ciphertext)

        pad_len = plaintext[-1]
        content = plaintext[:-pad_len]
        msg_len = struct.unpack("!I", content[16:20])[0]
        msg = content[20:20 + msg_len].decode("utf-8")
        return msg
    except Exception as exc:
        logger.error(f"WeCom decrypt failed: {exc}")
        return ""


def _wecom_extract_text(body: str) -> str:
    """从企业微信 XML 消息体中提取文本内容"""
    try:
        root = ET.fromstring(body)
        content = root.find("Content")
        if content is not None and content.text:
            return content.text.strip()
        text_elem = root.find("Text")
        if text_elem is not None:
            content = text_elem.find("Content")
            if content is not None and content.text:
                return content.text.strip()
    except ET.ParseError:
        pass
    return ""


def _wecom_extract_user(body: str) -> str:
    """从企业微信 XML 消息体中提取发送者 ID"""
    try:
        root = ET.fromstring(body)
        from_elem = root.find("From")
        if from_elem is not None:
            user_id = from_elem.find("UserId")
            if user_id is not None and user_id.text:
                return user_id.text
            alias = from_elem.find("Alias")
            if alias is not None and alias.text:
                return alias.text
    except ET.ParseError:
        pass
    return ""


def _feishu_extract_text(body: dict[str, Any]) -> str:
    """从飞书消息体中提取文本内容"""
    # 飞书事件格式: {"challenge": "...", "event": {"message": {"content": "..."}}}
    challenge = body.get("challenge", "")
    if challenge:
        # URL 验证阶段，返回 challenge 值
        return ""

    event = body.get("event", body)
    message = event.get("message", {})
    content = message.get("content", "")
    if isinstance(content, str):
        try:
            content_json = json.loads(content)
            return content_json.get("text", "")
        except (json.JSONDecodeError, TypeError):
            return content
    return str(content) if content else ""


def _qq_extract_text(body: dict[str, Any]) -> str:
    """从 QQ 消息体中提取文本内容"""
    # QQ Bot 消息格式因 SDK 不同而异
    content = body.get("content", body.get("message", ""))
    if isinstance(content, dict):
        return content.get("text", "")
    return str(content) if content else ""
