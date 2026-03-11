"""注册流程辅助数据结构与输出工具。"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RegistrationTaskResult:
    task_id: int
    success: bool
    duration_ms: int
    email: str
    token_file: str
    browser_channel: str
    error: str


def mask_token(token: str) -> str:
    if len(token) <= 16:
        return token
    return f"{token[:10]}...{token[-6:]}"


def timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def write_batch_summary(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
