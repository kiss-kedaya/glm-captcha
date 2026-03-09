"""账号 token 提取逻辑。"""
from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Page

TOKEN_CAPTURE_TIMEOUT_MS = 30_000
TOKEN_POLL_INTERVAL_MS = 800
COOKIE_URLS = ["https://chat.z.ai", "https://www.chat.z.ai"]
TOKEN_KEY_RE = re.compile(r"(token|auth|jwt|session)", re.IGNORECASE)
MAX_DEBUG_CANDIDATES = 8
PRIMARY_TOKEN_SOURCE_PREFIXES = ("context.cookie:token", "document.cookie:token")

TOKEN_STORAGE_SCRIPT = """
() => {
  const out = [];
  const isJwtLike = (value) => typeof value === "string" && value.split(".").length === 3;
  const isTokenKey = (key) => /(token|auth|jwt|session)/i.test(String(key || ""));
  const push = (source, key, value) => {
    if (typeof value !== "string") return;
    const trimmed = value.trim();
    if (!trimmed) return;
    if (!isTokenKey(key) && !isJwtLike(trimmed)) return;
    out.push({ source, key: String(key || ""), value: trimmed });
  };
  for (let i = 0; i < window.localStorage.length; i += 1) {
    const key = window.localStorage.key(i);
    if (key) push("localStorage", key, window.localStorage.getItem(key));
  }
  for (let i = 0; i < window.sessionStorage.length; i += 1) {
    const key = window.sessionStorage.key(i);
    if (key) push("sessionStorage", key, window.sessionStorage.getItem(key));
  }
  const cookies = (document.cookie || "").split(";");
  for (const item of cookies) {
    const parts = item.trim().split("=");
    if (!parts.length) continue;
    const key = decodeURIComponent(parts.shift() || "");
    const value = decodeURIComponent(parts.join("=") || "");
    push("document.cookie", key, value);
  }
  return out;
}
"""


@dataclass(frozen=True)
class CapturedToken:
    token: str
    source: str
    claims_email: str


def _is_jwt_like(value: str) -> bool:
    return value.count(".") == 2


def _decode_jwt_claims(token: str) -> dict[str, object]:
    if not _is_jwt_like(token):
        return {}
    try:
        payload = token.split(".")[1]
        padding = "=" * ((4 - len(payload) % 4) % 4)
        decoded = base64.urlsafe_b64decode((payload + padding).encode("utf-8")).decode("utf-8")
        data = json.loads(decoded)
    except Exception:
        return {}
    if isinstance(data, dict):
        return data
    return {}


def _claims_email(token: str) -> str:
    claims = _decode_jwt_claims(token)
    value = str(claims.get("email") or "").strip().lower()
    return value


def _collect_storage_candidates(page: Page) -> list[dict[str, str]]:
    raw = page.evaluate(TOKEN_STORAGE_SCRIPT)
    if not isinstance(raw, list):
        return []
    items: list[dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("source") or "").strip()
        key = str(entry.get("key") or "").strip()
        value = str(entry.get("value") or "").strip()
        if source and value:
            items.append({"source": f"{source}:{key}", "value": value})
    return items


def _collect_cookie_candidates(page: Page) -> list[dict[str, str]]:
    cookies = page.context.cookies(COOKIE_URLS)
    items: list[dict[str, str]] = []
    for cookie in cookies:
        name = str(cookie.get("name") or "").strip()
        value = str(cookie.get("value") or "").strip()
        if not name or not value:
            continue
        if not TOKEN_KEY_RE.search(name) and not _is_jwt_like(value):
            continue
        items.append({"source": f"context.cookie:{name}", "value": value})
    return items


def _collect_candidates(page: Page) -> list[dict[str, str]]:
    merged = _collect_cookie_candidates(page) + _collect_storage_candidates(page)
    deduplicated: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in merged:
        token = item["value"]
        if token in seen:
            continue
        seen.add(token)
        deduplicated.append(item)
    return deduplicated


def _is_primary_cookie_token_source(source: str) -> bool:
    lowered = source.strip().lower()
    return any(lowered.startswith(prefix) for prefix in PRIMARY_TOKEN_SOURCE_PREFIXES)


def capture_any_token(page: Page) -> str:
    candidates = _collect_candidates(page)
    for item in candidates:
        if _is_primary_cookie_token_source(item["source"]):
            return item["value"]
    return ""


def _pick_account_token(
    candidates: list[dict[str, str]],
    expected_email: str,
    previous_token: str,
) -> Optional[CapturedToken]:
    normalized_email = expected_email.strip().lower()
    for item in candidates:
        source = item["source"]
        if not _is_primary_cookie_token_source(source):
            continue
        token = item["value"]
        if previous_token and token == previous_token:
            continue
        if not _is_jwt_like(token):
            continue
        claims_email = _claims_email(token)
        if claims_email == normalized_email:
            return CapturedToken(token=token, source=source, claims_email=claims_email)
    return None


def _debug_candidates(candidates: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for item in candidates[:MAX_DEBUG_CANDIDATES]:
        token = item["value"]
        preview = token if len(token) <= 22 else f"{token[:12]}...{token[-6:]}"
        claims_email = _claims_email(token) if _is_jwt_like(token) else ""
        lines.append(f"{item['source']} | jwt={_is_jwt_like(token)} | email={claims_email or '-'} | token={preview}")
    return "; ".join(lines)


def wait_for_account_token(
    page: Page,
    logger,
    expected_email: str,
    previous_token: str = "",
    timeout_ms: int = TOKEN_CAPTURE_TIMEOUT_MS,
) -> CapturedToken:
    deadline = time.time() + (timeout_ms / 1000)
    latest_candidates: list[dict[str, str]] = []
    while time.time() < deadline:
        candidates = _collect_candidates(page)
        latest_candidates = candidates
        selected = _pick_account_token(candidates, expected_email, previous_token)
        if selected is not None:
            logger.info("已提取账号 token，来源: %s", selected.source)
            return selected
        page.wait_for_timeout(TOKEN_POLL_INTERVAL_MS)
    raise RuntimeError(
        f"注册完成后未提取到匹配邮箱的 token: expected_email={expected_email}, "
        f"candidates={_debug_candidates(latest_candidates)}"
    )
