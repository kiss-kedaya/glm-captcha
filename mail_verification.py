"""邮箱验证链接轮询逻辑。"""
from __future__ import annotations

import html
import logging
import re
import time
from typing import Optional

import requests

from utils import MailProviderConfig, MailboxSession, load_mail_polling_config, load_mail_provider_config

MAIL_REQUEST_TIMEOUT_SECONDS = 15
VERIFY_LINK_PATTERN = re.compile(
    r"https://chat\.z\.ai/auth/verify_email\?[^\s\"'<>]+",
    re.IGNORECASE,
)


def _mail_api_base(provider_name: str, config: MailProviderConfig) -> str:
    if provider_name == "mailtm":
        return config.mailtm_api_base.rstrip("/")
    if provider_name == "duckmail":
        return config.duckmail_api_base.rstrip("/")
    raise RuntimeError(f"不支持的邮箱提供商: {provider_name}")


def _mail_auth_headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }


def _extract_messages(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("hydra:member", "messages", "member", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _normalize_message_id(message: dict) -> str:
    raw_id = str(message.get("id") or message.get("@id") or "").strip()
    if not raw_id:
        return ""
    if raw_id.startswith("/messages/"):
        return raw_id.split("/")[-1]
    return raw_id


def _message_content(detail_payload: dict) -> str:
    subject = str(detail_payload.get("subject") or "")
    intro = str(detail_payload.get("intro") or "")
    text = str(detail_payload.get("text") or "")
    html_field = detail_payload.get("html") or ""
    html_content = "\n".join(str(item) for item in html_field) if isinstance(html_field, list) else str(html_field)
    return "\n".join([subject, intro, text, html_content])


def _extract_verify_link(content: str) -> Optional[str]:
    if not content:
        return None
    decoded = html.unescape(content)
    matched = VERIFY_LINK_PATTERN.search(decoded)
    if matched is None:
        return None
    return matched.group(0)


def wait_for_verify_link(
    mailbox: MailboxSession,
    logger: Optional[logging.Logger] = None,
) -> str:
    config = load_mail_provider_config()
    polling = load_mail_polling_config()
    api_base = _mail_api_base(mailbox.provider, config)
    headers = _mail_auth_headers(mailbox.auth_credential)
    list_url = f"{api_base}/messages"
    deadline = time.time() + polling.timeout_seconds
    seen_ids: set[str] = set()
    with requests.Session() as session:
        while time.time() < deadline:
            list_resp = session.get(list_url, headers=headers, timeout=MAIL_REQUEST_TIMEOUT_SECONDS)
            if list_resp.status_code != 200:
                raise RuntimeError(f"拉取邮件列表失败: status={list_resp.status_code}, body={list_resp.text[:200]}")
            for message in _extract_messages(list_resp.json()):
                message_id = _normalize_message_id(message)
                if not message_id or message_id in seen_ids:
                    continue
                seen_ids.add(message_id)
                detail_url = f"{api_base}/messages/{message_id}"
                detail_resp = session.get(detail_url, headers=headers, timeout=MAIL_REQUEST_TIMEOUT_SECONDS)
                if detail_resp.status_code != 200:
                    raise RuntimeError(f"拉取邮件详情失败: status={detail_resp.status_code}, id={message_id}")
                link = _extract_verify_link(_message_content(detail_resp.json()))
                if link:
                    if logger is not None:
                        logger.info("已获取邮箱验证链接: %s", link)
                    return link
            time.sleep(polling.poll_interval_seconds)
    raise TimeoutError(
        f"等待邮箱验证链接超时: provider={mailbox.provider}, email={mailbox.email}, timeout={polling.timeout_seconds}s"
    )
