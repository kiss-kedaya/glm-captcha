"""独立滑块验证脚本的公共辅助函数。"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page

from utils import DEBUG_DIR, ensure_dirs

START_VERIFY_PRECLICK_SETTLE_MS = 350
START_VERIFY_CLICK_DELAY_MS = 160


class StructuredLogWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: str, **fields: object) -> None:
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "ts_epoch_ms": int(time.time() * 1000),
            "event": event,
        }
        record.update(fields)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str))
            handle.write("\n")


def build_slider_logger() -> logging.Logger:
    ensure_dirs()
    logger = logging.getLogger("slider-verify")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    file_handler = logging.FileHandler(DEBUG_DIR / "slider_verify.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


def timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def write_json_file(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def save_debug_screenshot(
    page: Page,
    prefix: str,
    attempt: int,
    logger: logging.Logger,
    output_dir: Optional[Path] = None,
) -> Path:
    ensure_dirs()
    target_dir = output_dir or DEBUG_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / f"{prefix}_attempt{attempt}_{timestamp()}.png"
    page.screenshot(path=str(file_path), full_page=True)
    logger.info("已保存截图: %s", file_path)
    return file_path


def wait_any_visible(page: Page, selectors: list[str], timeout_ms: int) -> None:
    deadline = time.time() + (timeout_ms / 1000)
    while time.time() < deadline:
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                if locator.is_visible(timeout=200):
                    return
            except Exception:
                continue
        page.wait_for_timeout(120)
    raise RuntimeError(f"页面关键元素未在 {timeout_ms}ms 内就绪: {selectors}")


def open_and_trigger_captcha(
    page: Page,
    *,
    url: str,
    timeout_ms: int,
    logger: logging.Logger,
    page_ready_timeout_ms: int,
    trigger_selector: str,
    popup_selector: str,
) -> None:
    started = time.perf_counter()
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    wait_any_visible(
        page,
        [trigger_selector, "input[autocomplete='email']", "input[type='password']"],
        page_ready_timeout_ms,
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info("已打开页面: %s（耗时 %sms）", page.url, elapsed_ms)
    popup = page.locator(popup_selector).first
    try:
        if popup.is_visible(timeout=800):
            logger.info("验证码浮层已在前台，跳过触发点击")
            return
    except Exception:
        pass
    trigger = page.locator(trigger_selector).first
    trigger.wait_for(state="visible", timeout=timeout_ms)
    page.wait_for_timeout(START_VERIFY_PRECLICK_SETTLE_MS)
    try:
        trigger.click(timeout=timeout_ms, delay=START_VERIFY_CLICK_DELAY_MS)
    except Exception:
        trigger.click(
            timeout=timeout_ms,
            force=True,
            delay=START_VERIFY_CLICK_DELAY_MS,
        )
    logger.info("已点击开始验证")


def build_attempt_dir(run_dir: Optional[Path], attempt: int) -> Optional[Path]:
    if run_dir is None:
        return None
    attempt_dir = run_dir / f"attempt_{attempt:03d}_{timestamp()}"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    return attempt_dir
