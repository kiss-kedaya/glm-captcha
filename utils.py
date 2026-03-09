"""工具函数：随机数据生成、日志、截图保存、调试产物"""
from __future__ import annotations

import json
import logging
import os
import random
import string
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from mail_provider_clients import create_mailbox

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
DEBUG_DIR = OUTPUT_DIR / "debug"
TOKENS_DIR = OUTPUT_DIR / "tokens"
ENV_FILE = BASE_DIR / ".env"
ENV_MAIL_PROVIDER = "MAIL_PROVIDER"
ENV_MAILTM_API_BASE = "MAILTM_API_BASE"
ENV_DUCKMAIL_API_BASE = "DUCKMAIL_API_BASE"
ENV_DUCKMAIL_BEARER_TOKEN = "DUCKMAIL_BEARER_TOKEN"
ENV_SUBMIT_RETRY_COUNT = "SUBMIT_RETRY_COUNT"
ENV_MAIL_VERIFY_TIMEOUT_SECONDS = "MAIL_VERIFY_TIMEOUT_SECONDS"
ENV_MAIL_POLL_INTERVAL_SECONDS = "MAIL_POLL_INTERVAL_SECONDS"
DEFAULT_MAILTM_API_BASE = "https://api.mail.tm"
DEFAULT_DUCKMAIL_API_BASE = "https://api.duckmail.sbs"
DEFAULT_SUBMIT_RETRY_COUNT = 3
DEFAULT_MAIL_VERIFY_TIMEOUT_SECONDS = 120
DEFAULT_MAIL_POLL_INTERVAL_SECONDS = 3
SUPPORTED_MAIL_PROVIDERS = {"mailtm", "duckmail"}
ACCOUNT_NAME_PREFIX = "tester_"
ACCOUNT_NAME_SUFFIX_LEN = 6
ACCOUNT_PASSWORD_PREFIX = "Aa!"
ACCOUNT_PASSWORD_LEN = 10


@dataclass
class RegisterProfile:
    name: str
    email: str
    password: str
    mailbox: "MailboxSession"


@dataclass(frozen=True)
class MailboxSession:
    provider: str
    email: str
    auth_credential: str


@dataclass(frozen=True)
class MailProviderConfig:
    provider: str
    mailtm_api_base: str
    duckmail_api_base: str
    duckmail_bearer_token: str


@dataclass(frozen=True)
class MailPollingConfig:
    timeout_seconds: int
    poll_interval_seconds: int


def ensure_dirs() -> None:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    TOKENS_DIR.mkdir(parents=True, exist_ok=True)


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


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"缺少配置文件: {path}")
    env_map: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            raise ValueError(f".env 第 {line_no} 行格式错误: {raw_line}")
        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        cleaned = value.strip().strip('"').strip("'")
        if not key:
            raise ValueError(f".env 第 {line_no} 行缺少键名")
        env_map[key] = cleaned
    return env_map


def get_env_value(env_map: dict[str, str], key: str) -> str:
    return str(os.getenv(key, env_map.get(key, ""))).strip()


def _load_positive_int_env(key: str, default_value: int, minimum: int = 1) -> int:
    env_map = parse_env_file(ENV_FILE)
    raw_value = get_env_value(env_map, key)
    if not raw_value:
        return default_value
    try:
        parsed_value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{key} 必须是整数，当前值: {raw_value}") from exc
    if parsed_value < minimum:
        raise RuntimeError(f"{key} 必须大于等于 {minimum}，当前值: {parsed_value}")
    return parsed_value


def load_mail_provider_config() -> MailProviderConfig:
    env_map = parse_env_file(ENV_FILE)
    provider = get_env_value(env_map, ENV_MAIL_PROVIDER).lower()
    if provider not in SUPPORTED_MAIL_PROVIDERS:
        raise RuntimeError(
            f"无效 MAIL_PROVIDER: {provider or '<empty>'}，仅支持 {sorted(SUPPORTED_MAIL_PROVIDERS)}"
        )
    mailtm_api_base = get_env_value(env_map, ENV_MAILTM_API_BASE) or DEFAULT_MAILTM_API_BASE
    duckmail_api_base = get_env_value(env_map, ENV_DUCKMAIL_API_BASE) or DEFAULT_DUCKMAIL_API_BASE
    duckmail_bearer_token = get_env_value(env_map, ENV_DUCKMAIL_BEARER_TOKEN)
    return MailProviderConfig(
        provider=provider,
        mailtm_api_base=mailtm_api_base,
        duckmail_api_base=duckmail_api_base,
        duckmail_bearer_token=duckmail_bearer_token,
    )


def load_submit_retry_count() -> int:
    return _load_positive_int_env(ENV_SUBMIT_RETRY_COUNT, DEFAULT_SUBMIT_RETRY_COUNT)


def load_mail_polling_config() -> MailPollingConfig:
    timeout_seconds = _load_positive_int_env(
        ENV_MAIL_VERIFY_TIMEOUT_SECONDS,
        DEFAULT_MAIL_VERIFY_TIMEOUT_SECONDS,
    )
    poll_interval_seconds = _load_positive_int_env(
        ENV_MAIL_POLL_INTERVAL_SECONDS,
        DEFAULT_MAIL_POLL_INTERVAL_SECONDS,
    )
    return MailPollingConfig(
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )


def create_temp_email() -> MailboxSession:
    config = load_mail_provider_config()
    email, auth_credential = create_mailbox(
        provider=config.provider,
        mailtm_api_base=config.mailtm_api_base,
        duckmail_api_base=config.duckmail_api_base,
        duckmail_bearer_token=config.duckmail_bearer_token,
    )
    if not email or not auth_credential:
        raise RuntimeError(
            f"{config.provider} create_mailbox 返回无效结果: email={email}, credential={auth_credential}"
        )
    return MailboxSession(
        provider=config.provider,
        email=str(email).strip(),
        auth_credential=str(auth_credential).strip(),
    )


def generate_name() -> str:
    suffix = random_string(ACCOUNT_NAME_SUFFIX_LEN, string.ascii_lowercase + string.digits)
    return f"{ACCOUNT_NAME_PREFIX}{suffix}"


def generate_account_password() -> str:
    body = random_string(ACCOUNT_PASSWORD_LEN, string.ascii_letters + string.digits)
    return f"{ACCOUNT_PASSWORD_PREFIX}{body}"


def generate_profile(logger: Optional[logging.Logger] = None) -> RegisterProfile:
    name = generate_name()
    mailbox = create_temp_email()
    password = generate_account_password()
    if logger is not None:
        logger.info("邮箱提供商: %s, 已生成邮箱: %s", mailbox.provider, mailbox.email)
    return RegisterProfile(name=name, email=mailbox.email, password=password, mailbox=mailbox)


def sleep_random(low: float = 0.3, high: float = 0.8) -> None:
    time.sleep(random.uniform(low, high))


def save_account_token(profile: RegisterProfile, token: str, source: str, claims_email: str) -> Path:
    ensure_dirs()
    safe_email = profile.email.lower().replace("@", "_at_").replace(".", "_")
    file_path = TOKENS_DIR / f"token_{safe_email}_{time.time_ns()}.json"
    payload = {
        "name": profile.name,
        "email": profile.email,
        "token": token,
        "token_source": source,
        "token_claim_email": claims_email,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return file_path
