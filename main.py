"""核心注册与滑块验证流程入口。"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

from playwright.sync_api import sync_playwright

from browser_runtime import BrowserLaunchResult, launch_browser_context
from mail_verification import wait_for_verify_link
from page_flow import AuthPageFlow
from registration_support import RegistrationTaskResult, mask_token, timestamp, write_batch_summary
from slider_captcha_solver import SliderVerificationFailedError
from slider_verifier import SLIDER_MODE_MANUAL, SUPPORTED_SLIDER_MODES, create_slider_verifier
from token_capture import capture_any_token, wait_for_account_token
from utils import (
    DEBUG_DIR,
    build_logger,
    build_task_logger,
    ensure_dirs,
    generate_profile,
    load_submit_retry_count,
    save_account_token,
)

AUTH_URL = "https://chat.z.ai/auth?action=signup&redirect_uri=https%3A%2F%2Fz.ai%2F"
VIEWPORT_WIDTH = 1440
VIEWPORT_HEIGHT = 900
POST_WAIT_MS = 5000
LOCALE = "zh-CN"
VERIFY_FAILED_TEXT = "验证失败，请重试"
CAPTCHA_RETRY_WAIT_MS = 1_500


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量注册 chat.z.ai 账号")
    parser.add_argument("--url", default=AUTH_URL, help="认证页地址")
    parser.add_argument("--count", type=int, default=1, help="总注册账号数，默认 1")
    parser.add_argument("--concurrency", type=int, default=1, help="并发任务数，默认 1")
    parser.add_argument("--headless", action="store_true", help="以无头模式运行浏览器")
    parser.add_argument(
        "--slider-mode",
        choices=SUPPORTED_SLIDER_MODES,
        default="auto",
        help="滑块模式：auto 自动识别拖动，manual 手动完成验证",
    )
    parser.add_argument(
        "--summary-path",
        default="",
        help="批量汇总 JSON 输出路径，默认 output/debug/batch_run_<timestamp>.json",
    )
    return parser.parse_args(argv)


def _validate_args(args: argparse.Namespace) -> None:
    if args.count < 1:
        raise SystemExit("--count 必须大于等于 1")
    if args.concurrency < 1:
        raise SystemExit("--concurrency 必须大于等于 1")
    if args.slider_mode == SLIDER_MODE_MANUAL and args.headless:
        raise SystemExit("手动滑块模式不支持 --headless")
    if args.slider_mode == SLIDER_MODE_MANUAL and args.concurrency > 1:
        raise SystemExit("手动滑块模式不支持并发大于 1")


def _close_quietly(resource) -> None:
    if resource is None:
        return
    try:
        resource.close()
    except Exception:
        pass


def _launch_task_page(playwright, *, headless: bool, logger) -> tuple[BrowserLaunchResult, object]:
    started = time.perf_counter()
    launch_result = launch_browser_context(
        playwright,
        headless=headless,
        locale=LOCALE,
        viewport_width=VIEWPORT_WIDTH,
        viewport_height=VIEWPORT_HEIGHT,
        logger=logger,
    )
    page = launch_result.context.new_page()
    logger.info(
        "浏览器与上下文已就绪（通道 %s，耗时 %sms）",
        launch_result.channel,
        int((time.perf_counter() - started) * 1000),
    )
    return launch_result, page


def _prepare_registration(flow: AuthPageFlow, page, profile_future: Future, logger, auth_url: str):
    flow.open(auth_url)
    previous_token = capture_any_token(page)
    register_started = time.perf_counter()
    flow.click_register()
    logger.info("注册表单切换完成（耗时 %sms）", int((time.perf_counter() - register_started) * 1000))
    profile_wait_started = time.perf_counter()
    profile = profile_future.result()
    logger.info("注册资料已就绪（等待耗时 %sms）", int((time.perf_counter() - profile_wait_started) * 1000))
    logger.info("运行账号: %s / %s", profile.email, profile.password)
    fill_started = time.perf_counter()
    flow.fill_register_form(profile)
    logger.info("注册信息填写完成（耗时 %sms）", int((time.perf_counter() - fill_started) * 1000))
    return profile, previous_token


def _solve_signup_with_retries(flow: AuthPageFlow, page, submit_retry_count: int, logger) -> int:
    slider_distance = 0
    last_error = ""
    for attempt in range(1, submit_retry_count + 1):
        try:
            flow.trigger_signup_captcha()
            flow.click_start_verify()
            slider_distance = flow.solve_slider_captcha()
        except SliderVerificationFailedError as exc:
            last_error = f"滑块处理异常: {exc}"
            logger.warning("第%s次滑块验证失败（%s），等待重置后重试", attempt, exc)
            page.wait_for_timeout(CAPTCHA_RETRY_WAIT_MS)
            continue
        success, error_message = flow.submit_signup_and_get_result()
        if success:
            logger.info("第%s次提交注册成功（signup success=true）", attempt)
            return slider_distance
        last_error = error_message
        if VERIFY_FAILED_TEXT not in error_message and not flow.has_verify_failed_toast() and not flow.has_slider_failed_status():
            logger.warning("第%s次提交失败（%s）", attempt, error_message)
            continue
        logger.warning("第%s次提交失败（%s），等待重置后重试", attempt, error_message)
        page.wait_for_timeout(CAPTCHA_RETRY_WAIT_MS)
    raise RuntimeError(f"连续{submit_retry_count}次提交失败，最后错误: {last_error}")


def _complete_registration(flow: AuthPageFlow, page, profile, previous_token, logger) -> str:
    verify_link = wait_for_verify_link(profile.mailbox, logger)
    flow.open_verify_link(verify_link)
    flow.complete_register_after_verify(profile)
    captured = wait_for_account_token(
        page=page,
        logger=logger,
        expected_email=profile.email,
        previous_token=previous_token,
    )
    saved_path = save_account_token(
        profile=profile,
        token=captured.token,
        source=captured.source,
        claims_email=captured.claims_email,
    )
    logger.info("账号 token 已保存: %s", saved_path)
    logger.info("token 预览: %s", mask_token(captured.token))
    logger.info("邮箱验证页注册提交流程已完成")
    return str(saved_path)


def _build_flow(page, logger, slider_mode: str) -> AuthPageFlow:
    verifier = create_slider_verifier(page, logger, slider_mode=slider_mode)
    return AuthPageFlow(page, logger, verifier)


def run_registration_task(
    task_id: int,
    *,
    total_count: int,
    auth_url: str,
    headless: bool,
    slider_mode: str,
) -> RegistrationTaskResult:
    logger = build_task_logger(task_id, total_count)
    submit_retry_count = load_submit_retry_count()
    started = time.perf_counter()
    browser = None
    context = None
    browser_channel = ""
    token_file = ""
    email = ""
    logger.info(
        "任务启动: submit_retry_count=%s, headless=%s, slider_mode=%s, url=%s",
        submit_retry_count,
        headless,
        slider_mode,
        auth_url,
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as executor, sync_playwright() as playwright:
            profile_future = executor.submit(generate_profile, logger)
            launch_result, page = _launch_task_page(playwright, headless=headless, logger=logger)
            browser = launch_result.browser
            context = launch_result.context
            browser_channel = launch_result.channel
            flow = _build_flow(page, logger, slider_mode)
            profile, previous_token = _prepare_registration(flow, page, profile_future, logger, auth_url)
            email = profile.email
            slider_distance = _solve_signup_with_retries(flow, page, submit_retry_count, logger)
            logger.info("滑块流程完成，推荐距离: %spx", slider_distance)
            token_file = _complete_registration(flow, page, profile, previous_token, logger)
            page.wait_for_timeout(POST_WAIT_MS)
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info("任务完成: success=true, duration=%sms, email=%s", duration_ms, email)
        return RegistrationTaskResult(task_id, True, duration_ms, email, token_file, browser_channel, "")
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.exception("任务失败: duration=%sms", duration_ms)
        return RegistrationTaskResult(task_id, False, duration_ms, email, token_file, browser_channel, str(exc))
    finally:
        _close_quietly(context)
        _close_quietly(browser)


def run_core_flow(*, auth_url: str = AUTH_URL, headless: bool = False, slider_mode: str = "auto") -> int:
    result = run_registration_task(
        1,
        total_count=1,
        auth_url=auth_url,
        headless=headless,
        slider_mode=slider_mode,
    )
    return 0 if result.success else 1


def _submit_batch_tasks(executor: ThreadPoolExecutor, *, count: int, auth_url: str, headless: bool, slider_mode: str):
    return {
        executor.submit(
            run_registration_task,
            task_id,
            total_count=count,
            auth_url=auth_url,
            headless=headless,
            slider_mode=slider_mode,
        ): task_id
        for task_id in range(1, count + 1)
    }


def run_batch_flow(
    *,
    count: int,
    concurrency: int,
    auth_url: str,
    headless: bool,
    slider_mode: str,
    summary_path: str,
) -> int:
    ensure_dirs()
    logger = build_logger()
    bounded_concurrency = max(1, min(concurrency, count))
    logger.info(
        "开始批量注册: count=%s, concurrency=%s, headless=%s, slider_mode=%s, url=%s",
        count,
        bounded_concurrency,
        headless,
        slider_mode,
        auth_url,
    )
    started = time.perf_counter()
    results: list[RegistrationTaskResult] = []
    success_count = 0
    with ThreadPoolExecutor(max_workers=bounded_concurrency) as executor:
        futures = _submit_batch_tasks(
            executor,
            count=count,
            auth_url=auth_url,
            headless=headless,
            slider_mode=slider_mode,
        )
        for completed_count, future in enumerate(as_completed(futures), start=1):
            task_id = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = RegistrationTaskResult(task_id, False, 0, "", "", "", f"未捕获任务异常: {exc}")
            results.append(result)
            success_count += int(result.success)
            logger.info(
                "批量进度: completed=%s/%s, success=%s, failed=%s, last_task=%s, last_email=%s",
                completed_count,
                count,
                success_count,
                completed_count - success_count,
                result.task_id,
                result.email or "-",
            )
    finished_ms = int((time.perf_counter() - started) * 1000)
    ordered_results = sorted(results, key=lambda item: item.task_id)
    summary_payload = {
        "count": count,
        "concurrency": bounded_concurrency,
        "headless": headless,
        "slider_mode": slider_mode,
        "url": auth_url,
        "duration_ms": finished_ms,
        "success_count": success_count,
        "failure_count": count - success_count,
        "results": [asdict(item) for item in ordered_results],
    }
    summary_target = Path(summary_path) if summary_path else DEBUG_DIR / f"batch_run_{timestamp()}.json"
    write_batch_summary(summary_target, summary_payload)
    logger.info("批量注册完成: success=%s/%s, duration=%sms, summary=%s", success_count, count, finished_ms, summary_target)
    return 0 if success_count == count else 1


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        _validate_args(args)
        if args.count == 1 and args.concurrency == 1:
            return run_core_flow(auth_url=args.url, headless=args.headless, slider_mode=args.slider_mode)
        return run_batch_flow(
            count=args.count,
            concurrency=args.concurrency,
            auth_url=args.url,
            headless=args.headless,
            slider_mode=args.slider_mode,
            summary_path=args.summary_path,
        )
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
