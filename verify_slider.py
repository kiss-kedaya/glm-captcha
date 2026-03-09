
"""
阿里云滑块链路独立验证压测模块
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
import traceback
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, sync_playwright

from browser_runtime import launch_browser_context
from slider_captcha_solver import SliderCaptchaSolver, SliderVerificationFailedError
from utils import DEBUG_DIR, OUTPUT_DIR, ensure_dirs

AUTH_URL = "https://chat.z.ai/auth"
VIEWPORT_WIDTH = 1440
VIEWPORT_HEIGHT = 900
LOCALE = "zh-CN"
CAPTCHA_TRIGGER_SELECTOR = "#aliyunCaptcha-captcha-text"
CAPTCHA_POPUP_SELECTOR = "#aliyunCaptcha-window-float"
PAGE_READY_TIMEOUT_MS = 15_000
DEFAULT_STRUCTURED_LOG = DEBUG_DIR / "slider_verify.jsonl"
DEFAULT_SAMPLE_ROOT = OUTPUT_DIR / "slider_samples"


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="独立验证阿里云滑块链路")
    parser.add_argument("--url", default=AUTH_URL, help="验证页面地址")
    parser.add_argument("--attempts", type=int, default=1, help="独立尝试次数，默认 1")
    parser.add_argument("--headless", action="store_true", help="以 headless 模式运行浏览器")
    parser.add_argument("--pause-ms", type=int, default=800, help="每次尝试结束前停留毫秒数")
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=120_000,
        help="页面打开与元素等待超时时间，默认 120000",
    )
    parser.add_argument(
        "--save-success-screenshot",
        action="store_true",
        help="成功时也保存截图",
    )
    parser.add_argument(
        "--structured-log",
        default=str(DEFAULT_STRUCTURED_LOG),
        help="结构化 JSONL 日志输出路径，默认 output/debug/slider_verify.jsonl",
    )
    parser.add_argument(
        "--sample-dir",
        default=str(DEFAULT_SAMPLE_ROOT),
        help="样本目录根路径，默认 output/slider_samples",
    )
    parser.add_argument(
        "--sample-artifacts",
        choices=("off", "failure", "all"),
        default="failure",
        help="样本目录保存策略：off 不保存，failure 仅保留失败样本，all 保留全部样本",
    )
    return parser.parse_args()


def _timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _write_json_file(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _save_debug_screenshot(
    page: Page,
    prefix: str,
    attempt: int,
    logger: logging.Logger,
    output_dir: Optional[Path] = None,
) -> Path:
    ensure_dirs()
    target_dir = output_dir or DEBUG_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / f"{prefix}_attempt{attempt}_{_timestamp()}.png"
    page.screenshot(path=str(file_path), full_page=True)
    logger.info("已保存截图: %s", file_path)
    return file_path


def _wait_any_visible(page: Page, selectors: list[str], timeout_ms: int) -> None:
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


def _open_and_trigger_captcha(page: Page, url: str, timeout_ms: int, logger: logging.Logger) -> None:
    started = time.perf_counter()
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    _wait_any_visible(
        page,
        [CAPTCHA_TRIGGER_SELECTOR, "input[autocomplete='email']", "input[type='password']"],
        PAGE_READY_TIMEOUT_MS,
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info("已打开页面: %s（耗时 %sms）", page.url, elapsed_ms)
    popup = page.locator(CAPTCHA_POPUP_SELECTOR).first
    try:
        if popup.is_visible(timeout=800):
            logger.info("验证码浮层已在前台，跳过触发点击")
            return
    except Exception:
        pass
    trigger = page.locator(CAPTCHA_TRIGGER_SELECTOR).first
    trigger.wait_for(state="visible", timeout=timeout_ms)
    trigger.click(timeout=timeout_ms)
    logger.info("已点击开始验证")


def _build_attempt_dir(run_dir: Optional[Path], attempt: int) -> Optional[Path]:
    if run_dir is None:
        return None
    attempt_dir = run_dir / f"attempt_{attempt:03d}_{_timestamp()}"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    return attempt_dir


def run_single_attempt(
    attempt: int,
    *,
    url: str,
    headless: bool,
    pause_ms: int,
    timeout_ms: int,
    save_success_screenshot: bool,
    logger: logging.Logger,
    structured_writer: StructuredLogWriter,
    run_dir: Optional[Path],
    sample_artifacts: str,
) -> dict[str, object]:
    started_at_epoch = time.time()
    started_at_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started_at_epoch))
    attempt_dir = _build_attempt_dir(run_dir, attempt)
    attempt_token = attempt_dir.name if attempt_dir is not None else f"attempt_{attempt:03d}_{_timestamp()}"
    events: list[dict[str, object]] = []
    screenshot_path: Optional[str] = None
    success = False
    error_message = ""
    slider_distance: Optional[int] = None
    final_page_url = ""

    def emit(event: str, **fields: object) -> None:
        record = {
            "event": event,
            "attempt": attempt,
            "attempt_token": attempt_token,
            "ts_epoch_ms": int(time.time() * 1000),
        }
        record.update(fields)
        events.append(record)
        structured_writer.emit(event, attempt=attempt, attempt_token=attempt_token, **fields)

    emit(
        "attempt_started",
        url=url,
        headless=headless,
        sample_dir=str(attempt_dir) if attempt_dir is not None else None,
    )

    with sync_playwright() as playwright:
        launch_started = time.perf_counter()
        launch_result = launch_browser_context(
            playwright,
            headless=headless,
            locale=LOCALE,
            viewport_width=VIEWPORT_WIDTH,
            viewport_height=VIEWPORT_HEIGHT,
            logger=logger,
        )
        browser = launch_result.browser
        context = launch_result.context
        emit(
            "browser_ready",
            browser_channel=launch_result.channel,
            duration_ms=int((time.perf_counter() - launch_started) * 1000),
        )
        page = context.new_page()
        try:
            _open_and_trigger_captcha(page, url=url, timeout_ms=timeout_ms, logger=logger)
            emit("captcha_triggered", page_url=page.url)
            solver = SliderCaptchaSolver(
                page,
                logger,
                event_sink=lambda payload: emit(str(payload["event"]), **{
                    key: value for key, value in payload.items() if key != "event"
                }),
                sample_dir=attempt_dir,
            )
            slider_distance = solver.solve()
            final_page_url = page.url
            success = True
            logger.info("第%s次滑块验证成功，滑块移动距离: %spx", attempt, slider_distance)
            emit(
                "attempt_succeeded",
                slider_distance=slider_distance,
                page_url=page.url,
            )
            if save_success_screenshot:
                screenshot_path = str(
                    _save_debug_screenshot(
                        page,
                        "slider_success",
                        attempt,
                        logger,
                        output_dir=attempt_dir,
                    )
                )
            page.wait_for_timeout(pause_ms)
        except SliderVerificationFailedError as exc:
            error_message = str(exc)
            final_page_url = page.url
            logger.warning("第%s次滑块验证失败: %s", attempt, exc)
            emit("attempt_failed", error=error_message, page_url=page.url)
            screenshot_path = str(
                _save_debug_screenshot(
                    page,
                    "slider_fail",
                    attempt,
                    logger,
                    output_dir=attempt_dir,
                )
            )
            page.wait_for_timeout(pause_ms)
        except Exception as exc:
            error_message = str(exc)
            final_page_url = page.url
            logger.exception("第%s次滑块验证出现异常", attempt)
            emit("attempt_error", error=error_message, page_url=page.url)
            screenshot_path = str(
                _save_debug_screenshot(
                    page,
                    "slider_error",
                    attempt,
                    logger,
                    output_dir=attempt_dir,
                )
            )
            page.wait_for_timeout(pause_ms)
        finally:
            context.close()
            browser.close()

    finished_at_epoch = time.time()
    finished_at_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(finished_at_epoch))
    duration_ms = int((finished_at_epoch - started_at_epoch) * 1000)
    emit(
        "attempt_finished",
        success=success,
        duration_ms=duration_ms,
        slider_distance=slider_distance,
        error=error_message or None,
    )

    summary = {
        "attempt": attempt,
        "attempt_token": attempt_token,
        "started_at": started_at_text,
        "finished_at": finished_at_text,
        "duration_ms": duration_ms,
        "success": success,
        "slider_distance": slider_distance,
        "error": error_message,
        "page_url": final_page_url,
        "screenshot_path": screenshot_path,
        "sample_dir": str(attempt_dir) if attempt_dir is not None else None,
        "events": events,
    }

    if attempt_dir is not None:
        keep_attempt_dir = sample_artifacts == "all" or not success or (success and save_success_screenshot)
        if keep_attempt_dir:
            _write_json_file(attempt_dir / "attempt_summary.json", summary)
        else:
            shutil.rmtree(attempt_dir, ignore_errors=True)
            summary["sample_dir"] = None
            summary["screenshot_path"] = None
            for event in summary["events"]:
                for key in ("background", "shadow"):
                    value = event.get(key)
                    if isinstance(value, dict) and "path" in value:
                        value["path"] = None

    return summary


def main() -> int:
    args = parse_args()
    if args.attempts < 1:
        raise SystemExit("--attempts 必须大于等于 1")
    logger = build_slider_logger()
    structured_writer = StructuredLogWriter(Path(args.structured_log))
    run_dir: Optional[Path] = None
    if args.sample_artifacts != "off":
        run_dir = Path(args.sample_dir) / f"run_{_timestamp()}"
        run_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "开始独立验证滑块链路: attempts=%s, headless=%s, url=%s",
        args.attempts,
        args.headless,
        args.url,
    )
    structured_writer.emit(
        "run_started",
        attempts=args.attempts,
        headless=args.headless,
        url=args.url,
        run_dir=str(run_dir) if run_dir is not None else None,
        sample_artifacts=args.sample_artifacts,
    )

    success_count = 0
    attempt_summaries: list[dict[str, object]] = []
    for attempt in range(1, args.attempts + 1):
        logger.info("开始第%s次滑块验证", attempt)
        summary = run_single_attempt(
            attempt,
            url=args.url,
            headless=args.headless,
            pause_ms=args.pause_ms,
            timeout_ms=args.timeout_ms,
            save_success_screenshot=args.save_success_screenshot,
            logger=logger,
            structured_writer=structured_writer,
            run_dir=run_dir,
            sample_artifacts=args.sample_artifacts,
        )
        attempt_summaries.append(summary)
        if summary["success"] is True:
            success_count += 1

    logger.info("滑块验证汇总: success=%s/%s", success_count, args.attempts)
    structured_writer.emit(
        "run_finished",
        success_count=success_count,
        total_count=args.attempts,
        run_dir=str(run_dir) if run_dir is not None else None,
    )

    run_summary = {
        "attempts": args.attempts,
        "success_count": success_count,
        "failure_count": args.attempts - success_count,
        "headless": args.headless,
        "url": args.url,
        "structured_log": str(Path(args.structured_log)),
        "run_dir": str(run_dir) if run_dir is not None else None,
        "sample_artifacts": args.sample_artifacts,
        "attempt_summaries": attempt_summaries,
    }

    if run_dir is not None:
        _write_json_file(run_dir / "run_summary.json", run_summary)
    else:
        _write_json_file(DEBUG_DIR / "slider_run_summary.json", run_summary)
    return 0 if success_count == args.attempts else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc(file=sys.stderr)
        raise SystemExit(1)
