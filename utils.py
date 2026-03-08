"""工具函数：随机数据生成、日志、截图保存、调试产物"""
from __future__ import annotations

import json
import logging
import random
import string
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
DEBUG_DIR = OUTPUT_DIR / "debug"


@dataclass
class RegisterProfile:
    nickname: str
    email: str
    password: str


def ensure_dirs() -> None:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)


def now_tag() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def build_logger() -> logging.Logger:
    ensure_dirs()
    logger = logging.getLogger("ddocr")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    fh = logging.FileHandler(DEBUG_DIR / "run.log", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger


def random_string(length: int, alphabet: str) -> str:
    return "".join(random.choice(alphabet) for _ in range(length))


def generate_profile() -> RegisterProfile:
    suffix = random_string(6, string.ascii_lowercase + string.digits)
    nickname = f"tester_{suffix}"
    email = f"{nickname}@example.com"
    password = f"Aa!{random_string(10, string.ascii_letters + string.digits)}"
    return RegisterProfile(nickname=nickname, email=email, password=password)


def save_json(name: str, data: dict[str, Any]) -> Path:
    ensure_dirs()
    path = DEBUG_DIR / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def sleep_random(low: float = 0.3, high: float = 0.8) -> None:
    time.sleep(random.uniform(low, high))
