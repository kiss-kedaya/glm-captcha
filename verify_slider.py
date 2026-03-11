"""阿里云滑块链路独立验证压测模块。"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright

from browser_runtime import launch_browser_context
from slider_captcha_solver import SliderVerificationFailedError
from slider_verifier import SLIDER_MODE_MANUAL, SUPPORTED_SLIDER_MODES, create_slider_verifier
from slider_verify_support import (
    StructuredLogWriter,
    build_attempt_dir,
    build_slider_logger,
    open_and_trigger_captcha,
    save_debug_screenshot,
    timestamp,
    write_json_file,
)
from utils import DEBUG_DIR, OUTPUT_DIR

AUTH_URL = "https://chat.z.ai/auth"
VIEWPORT_WIDTH = 1440
VIEWPORT_HEIGHT = 900
LOCALE = "zh-CN"
CAPTCHA_TRIGGER_SELECTOR = "#aliyunCaptcha-captcha-text"
CAPTCHA_POPUP_SELECTOR = "#aliyunCaptcha-window-float"
PAGE_READY_TIMEOUT_MS = 15_000
DEFAULT_STRUCTURED_LOG = DEBUG_DIR / "slider_verify.jsonl"
DEFAULT_SAMPLE_ROOT = OUTPUT_DIR / "slider_samples"


@dataclass(frozen=True)
class SliderAttemptOptions:
    attempt: int
    url: str
    headless: bool
    pause_ms: int
    timeout_ms: int
    save_success_screenshot: bool
    slider_mode: str
    sample_artifacts: str


@dataclass(frozen=True)
class AttemptOutcome:
    success: bool
    error_message: str
    slider_distance: Optional[int]
    screenshot_path: Optional[str]
    final_page_url: str


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="独立验证阿里云滑块链路")
    parser.add_argument("--url", default=AUTH_URL, help="验证页面地址")
    parser.add_argument("--attempts", type=int, default=1, help="独立尝试次数，默认 1")
    parser.add_argument("--headless", action="store_true", help="以 headless 模式运行浏览器")
    parser.add_argument("--pause-ms", type=int, default=800, help="每次尝试结束前停留毫秒数")
    parser.add_argument("--timeout-ms", type=int, default=120_000, help="页面打开与元素等待超时时间，默认 120000")
    parser.add_argument("--save-success-screenshot", action="store_true", help="成功时也保存截图")
    parser.add_argument(
        "--slider-mode",
        choices=SUPPORTED_SLIDER_MODES,
        default="auto",
        help="滑块模式：auto 自动识别拖动，manual 手动完成验证",
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
    return parser.parse_args(argv)


def _validate_args(args: argparse.Namespace) -> None:
    if args.attempts < 1:
        raise SystemExit("--attempts 必须大于等于 1")
    if args.slider_mode == SLIDER_MODE_MANUAL and args.headless:
        raise SystemExit("手动滑块模式不支持 --headless")


def _emit_attempt_event(
    events: list[dict[str, object]],
    writer: StructuredLogWriter,
    *,
    attempt: int,
    attempt_token: str,
    event: str,
    **fields: object,
) -> None:
    record = {
        "event": event,
        "attempt": attempt,
        "attempt_token": attempt_token,
        "ts_epoch_ms": int(time.time() * 1000),
    }
    record.update(fields)
    events.append(record)
    writer.emit(event, attempt=attempt, attempt_token=attempt_token, **fields)


def _build_verifier(page, logger, options: SliderAttemptOptions, attempt_dir: Optional[Path], emit):
    return create_slider_verifier(
        page,
        logger,
        slider_mode=options.slider_mode,
        sample_dir=attempt_dir,
        event_sink=lambda payload: emit(
            event=str(payload["event"]),
            **{key: value for key, value in payload.items() if key != "event"},
        ),
    )


def _execute_attempt(page, logger, options: SliderAttemptOptions, attempt_dir: Optional[Path], emit) -> AttemptOutcome:
    open_and_trigger_captcha(
        page,
        url=options.url,
        timeout_ms=options.timeout_ms,
        logger=logger,
        page_ready_timeout_ms=PAGE_READY_TIMEOUT_MS,
        trigger_selector=CAPTCHA_TRIGGER_SELECTOR,
        popup_selector=CAPTCHA_POPUP_SELECTOR,
    )
    emit(event="captcha_triggered", page_url=page.url)
    verifier = _build_verifier(page, logger, options, attempt_dir, emit)
    try:
        slider_distance = verifier.solve()
        logger.info("第%s次滑块验证成功，滑块移动距离: %spx", options.attempt, slider_distance)
        emit(event="attempt_succeeded", slider_distance=slider_distance, page_url=page.url)
        screenshot_path = None
        if options.save_success_screenshot:
            screenshot_path = str(save_debug_screenshot(page, "slider_success", options.attempt, logger, attempt_dir))
        page.wait_for_timeout(options.pause_ms)
        return AttemptOutcome(True, "", slider_distance, screenshot_path, page.url)
    except SliderVerificationFailedError as exc:
        logger.warning("第%s次滑块验证失败: %s", options.attempt, exc)
        emit(event="attempt_failed", error=str(exc), page_url=page.url)
        screenshot_path = str(save_debug_screenshot(page, "slider_fail", options.attempt, logger, attempt_dir))
        page.wait_for_timeout(options.pause_ms)
        return AttemptOutcome(False, str(exc), None, screenshot_path, page.url)
    except Exception as exc:
        logger.exception("第%s次滑块验证出现异常", options.attempt)
        emit(event="attempt_error", error=str(exc), page_url=page.url)
        screenshot_path = str(save_debug_screenshot(page, "slider_error", options.attempt, logger, attempt_dir))
        page.wait_for_timeout(options.pause_ms)
        return AttemptOutcome(False, str(exc), None, screenshot_path, page.url)


def _finalize_attempt_artifacts(
    summary: dict[str, object],
    attempt_dir: Optional[Path],
    *,
    sample_artifacts: str,
    save_success_screenshot: bool,
) -> dict[str, object]:
    if attempt_dir is None:
        return summary
    keep_attempt_dir = sample_artifacts == "all" or not summary["success"] or save_success_screenshot
    if keep_attempt_dir:
        write_json_file(attempt_dir / "attempt_summary.json", summary)
        return summary
    shutil.rmtree(attempt_dir, ignore_errors=True)
    summary["sample_dir"] = None
    summary["screenshot_path"] = None
    for event in summary["events"]:
        for key in ("background", "shadow"):
            value = event.get(key)
            if isinstance(value, dict) and "path" in value:
                value["path"] = None
    return summary


def run_single_attempt(
    options: SliderAttemptOptions,
    *,
    logger,
    structured_writer: StructuredLogWriter,
    run_dir: Optional[Path],
) -> dict[str, object]:
    started_at_epoch = time.time()
    attempt_dir = build_attempt_dir(run_dir, options.attempt)
    attempt_token = attempt_dir.name if attempt_dir is not None else f"attempt_{options.attempt:03d}_{timestamp()}"
    events: list[dict[str, object]] = []

    def emit(*, event: str, **fields: object) -> None:
        _emit_attempt_event(
            events,
            structured_writer,
            attempt=options.attempt,
            attempt_token=attempt_token,
            event=event,
            **fields,
        )

    emit(event="attempt_started", url=options.url, headless=options.headless, slider_mode=options.slider_mode, sample_dir=str(attempt_dir) if attempt_dir else None)
    with sync_playwright() as playwright:
        launch_started = time.perf_counter()
        launch_result = launch_browser_context(
            playwright,
            headless=options.headless,
            locale=LOCALE,
            viewport_width=VIEWPORT_WIDTH,
            viewport_height=VIEWPORT_HEIGHT,
            logger=logger,
        )
        emit(event="browser_ready", browser_channel=launch_result.channel, duration_ms=int((time.perf_counter() - launch_started) * 1000))
        page = launch_result.context.new_page()
        try:
            outcome = _execute_attempt(page, logger, options, attempt_dir, emit)
        finally:
            launch_result.context.close()
            launch_result.browser.close()
    duration_ms = int((time.time() - started_at_epoch) * 1000)
    emit(event="attempt_finished", success=outcome.success, duration_ms=duration_ms, slider_distance=outcome.slider_distance, error=outcome.error_message or None)
    summary = {
        "attempt": options.attempt,
        "attempt_token": attempt_token,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started_at_epoch)),
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "duration_ms": duration_ms,
        "success": outcome.success,
        "slider_distance": outcome.slider_distance,
        "error": outcome.error_message,
        "page_url": outcome.final_page_url,
        "screenshot_path": outcome.screenshot_path,
        "sample_dir": str(attempt_dir) if attempt_dir else None,
        "slider_mode": options.slider_mode,
        "events": events,
    }
    return _finalize_attempt_artifacts(
        summary,
        attempt_dir,
        sample_artifacts=options.sample_artifacts,
        save_success_screenshot=options.save_success_screenshot,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _validate_args(args)
    logger = build_slider_logger()
    structured_writer = StructuredLogWriter(Path(args.structured_log))
    run_dir = None if args.sample_artifacts == "off" else Path(args.sample_dir) / f"run_{timestamp()}"
    if run_dir is not None:
        run_dir.mkdir(parents=True, exist_ok=True)
    logger.info("开始独立验证滑块链路: attempts=%s, headless=%s, slider_mode=%s, url=%s", args.attempts, args.headless, args.slider_mode, args.url)
    structured_writer.emit(
        "run_started",
        attempts=args.attempts,
        headless=args.headless,
        slider_mode=args.slider_mode,
        url=args.url,
        run_dir=str(run_dir) if run_dir else None,
        sample_artifacts=args.sample_artifacts,
    )
    success_count = 0
    attempt_summaries: list[dict[str, object]] = []
    for attempt in range(1, args.attempts + 1):
        logger.info("开始第%s次滑块验证", attempt)
        options = SliderAttemptOptions(
            attempt=attempt,
            url=args.url,
            headless=args.headless,
            pause_ms=args.pause_ms,
            timeout_ms=args.timeout_ms,
            save_success_screenshot=args.save_success_screenshot,
            slider_mode=args.slider_mode,
            sample_artifacts=args.sample_artifacts,
        )
        summary = run_single_attempt(options, logger=logger, structured_writer=structured_writer, run_dir=run_dir)
        attempt_summaries.append(summary)
        success_count += int(summary["success"] is True)
    logger.info("滑块验证汇总: success=%s/%s", success_count, args.attempts)
    structured_writer.emit("run_finished", success_count=success_count, total_count=args.attempts, run_dir=str(run_dir) if run_dir else None)
    run_summary = {
        "attempts": args.attempts,
        "success_count": success_count,
        "failure_count": args.attempts - success_count,
        "headless": args.headless,
        "slider_mode": args.slider_mode,
        "url": args.url,
        "structured_log": str(Path(args.structured_log)),
        "run_dir": str(run_dir) if run_dir else None,
        "sample_artifacts": args.sample_artifacts,
        "attempt_summaries": attempt_summaries,
    }
    if run_dir is not None:
        write_json_file(run_dir / "run_summary.json", run_summary)
    else:
        write_json_file(DEBUG_DIR / "slider_run_summary.json", run_summary)
    return 0 if success_count == args.attempts else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc(file=sys.stderr)
        raise SystemExit(1)
