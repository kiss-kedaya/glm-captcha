"""本地邮箱提供商客户端（mailtm / duckmail）。"""
from __future__ import annotations

import random
import secrets
from typing import Any

import requests

REQUEST_TIMEOUT_SECONDS = 20
MAX_CREATE_ATTEMPTS = 5
SUPPORTED_PROVIDERS = {"mailtm", "duckmail"}


def _json_headers(token: str = "") -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _extract_domains(payload: Any) -> list[str]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("hydra:member") or payload.get("items") or []
    else:
        items = []
    domains: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain") or "").strip()
        if not domain:
            continue
        if not item.get("isActive", True):
            continue
        if item.get("isPrivate", False):
            continue
        domains.append(domain)
    return domains


def _fetch_domains(session: requests.Session, api_base: str, headers: dict[str, str]) -> list[str]:
    response = session.get(
        f"{api_base.rstrip('/')}/domains",
        headers=headers,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        raise RuntimeError(f"获取邮箱域名失败: status={response.status_code}, body={response.text[:200]}")
    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError("邮箱域名响应不是有效 JSON") from exc
    domains = _extract_domains(payload)
    if not domains:
        raise RuntimeError("邮箱域名列表为空")
    return domains


def _create_mailbox_with_token(
    session: requests.Session,
    api_base: str,
    base_headers: dict[str, str],
    domains: list[str],
) -> tuple[str, str]:
    last_error = ""
    root = api_base.rstrip("/")
    for _ in range(MAX_CREATE_ATTEMPTS):
        email = f"oc{secrets.token_hex(5)}@{random.choice(domains)}"
        password = secrets.token_urlsafe(18)
        account_response = session.post(
            f"{root}/accounts",
            headers=base_headers,
            json={"address": email, "password": password},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if account_response.status_code not in (200, 201):
            last_error = f"创建账号失败: status={account_response.status_code}, body={account_response.text[:120]}"
            continue
        token_response = session.post(
            f"{root}/token",
            headers=base_headers,
            json={"address": email, "password": password},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if token_response.status_code != 200:
            last_error = f"获取 token 失败: status={token_response.status_code}, body={token_response.text[:120]}"
            continue
        try:
            token = str(token_response.json().get("token") or "").strip()
        except Exception as exc:
            raise RuntimeError("token 响应不是有效 JSON") from exc
        if not token:
            last_error = "token 为空"
            continue
        return email, token
    raise RuntimeError(f"创建临时邮箱失败: {last_error or '未知错误'}")

def create_mailtm_mailbox(api_base: str) -> tuple[str, str]:
    with requests.Session() as session:
        domains = _fetch_domains(session, api_base, _json_headers())
        return _create_mailbox_with_token(session, api_base, _json_headers(), domains)


def create_duckmail_mailbox(api_base: str, bearer_token: str) -> tuple[str, str]:
    auth_headers = _json_headers(bearer_token.strip())
    with requests.Session() as session:
        domains = _fetch_domains(session, api_base, auth_headers)
        return _create_mailbox_with_token(session, api_base, auth_headers, domains)


def create_mailbox(
    *,
    provider: str,
    mailtm_api_base: str,
    duckmail_api_base: str,
    duckmail_bearer_token: str,
) -> tuple[str, str]:
    provider_name = provider.strip().lower()
    if provider_name not in SUPPORTED_PROVIDERS:
        raise RuntimeError(f"不支持的邮箱提供商: {provider_name}")
    if provider_name == "mailtm":
        return create_mailtm_mailbox(mailtm_api_base)
    return create_duckmail_mailbox(duckmail_api_base, duckmail_bearer_token)
