"""核心注册与滑块验证流程入口。"""
from __future__ import annotations

import argparse
import json
import time
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright

from browser_runtime import launch_browser_context
from mail_verification import wait_for_verify_link
from page_flow import AuthPageFlow
from slider_captcha_solver import SliderVerificationFailedError
from token_capture import capture_any_token, wait_for_account_token
from utils import DEBUG_DIR, build_logger, build_task_logger, ensure_dirs, generate_profile, load_submit_retry_count, save_account_token

AUTH_URL = "https://chat.z.ai/auth"
VIEWPORT_WIDTH = 1440
VIEWPORT_HEIGHT = 900
POST_WAIT_MS = 5000
LOCALE = "zh-CN"
VERIFY_FAILED_TEXT = "验证失败，请重试"
CAPTCHA_RETRY_WAIT_MS = 1_500


@dataclass(frozen=True)
class RegistrationTaskResult:
    task_id: int
    success: bool
    duration_ms: int
    email: str
    token_file: str
    browser_channel: str
    error: str


def _mask_token(token: str) -> str:
    if len(token) <= 16:
        return token
    return f"{token[:10]}...{token[-6:]}"


def _timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _write_batch_summary(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量注册 chat.z.ai 账号")
    parser.add_argument("--url", default=AUTH_URL, help="认证页地址")
    parser.add_argument("--count", type=int, default=1, help="总注册账号数，默认 1")
    parser.add_argument("--concurrency", type=int, default=1, help="并发任务数，默认 1")
    parser.add_argument("--headless", action="store_true", help="以无头模式运行浏览器")
    parser.add_argument(
        "--summary-path",
        default="",
        help="批量汇总 JSON 输出路径，默认 output/debug/batch_run_<timestamp>.json",
    )
    return parser.parse_args(argv)


def run_registration_task(
    task_id: int,
    *,
    total_count: int,
    auth_url: str,
    headless: bool,
) -> RegistrationTaskResult:
    logger = build_task_logger(task_id, total_count)
    submit_retry_count = load_submit_retry_count()
    started = time.perf_counter()
    browser_channel = ""
    token_file = ""
    email = ""
    context = None
    browser = None
    logger.info("任务启动: submit_retry_count=%s, headless=%s, url=%s", submit_retry_count, headless, auth_url)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            profile_future = executor.submit(generate_profile, logger)
            with sync_playwright() as playwright:
                browser_started = time.perf_counter()
                launch_result = launch_browser_context(
                    playwright,
                    headless=headless,
                    locale=LOCALE,
                    viewport_width=VIEWPORT_WIDTH,
                    viewport_height=VIEWPORT_HEIGHT,
                    logger=logger,
                )
                browser_channel = launch_result.channel
                browser = launch_result.browser
                context = launch_result.context
                page = context.new_page()
                logger.info(
                    "浏览器与上下文已就绪（通道 %s，耗时 %sms）",
                    launch_result.channel,
                    int((time.perf_counter() - browser_started) * 1000),
                )
                flow = AuthPageFlow(page, logger)
                flow.open(auth_url)
                previous_token = capture_any_token(page)
                register_started = time.perf_counter()
                flow.click_register()
                logger.info(
                    "注册表单切换完成（耗时 %sms）",
                    int((time.perf_counter() - register_started) * 1000),
                )
                profile_wait_started = time.perf_counter()
                profile = profile_future.result()
                email = profile.email
                logger.info(
                    "注册资料已就绪（等待耗时 %sms）",
                    int((time.perf_counter() - profile_wait_started) * 1000),
                )
                logger.info("运行账号: %s / %s", profile.email, profile.password)
                fill_started = time.perf_counter()
                flow.fill_register_form(profile)
                logger.info(
                    "注册信息填写完成（耗时 %sms）",
                    int((time.perf_counter() - fill_started) * 1000),
                )
                slider_distance = 0
                last_error = ""
                for attempt in range(1, submit_retry_count + 1):
                    try:
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
                        break
                    last_error = error_message
                    if (
                        VERIFY_FAILED_TEXT in error_message
                        or flow.has_verify_failed_toast()
                        or flow.has_slider_failed_status()
                    ):
                        logger.warning("第%s次提交失败（%s），等待重置后重试", attempt, error_message)
                        page.wait_for_timeout(CAPTCHA_RETRY_WAIT_MS)
                        continue
                    logger.warning("第%s次提交失败（%s）", attempt, error_message)
                else:
                    raise RuntimeError(f"连续{submit_retry_count}次提交失败，最后错误: {last_error}")
                logger.info("滑块流程完成，推荐距离: %spx", slider_distance)
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
                token_file = str(saved_path)
                logger.info("账号 token 已保存: %s", token_file)
                logger.info("token 预览: %s", _mask_token(captured.token))
                logger.info("邮箱验证页注册提交流程已完成")
                page.wait_for_timeout(POST_WAIT_MS)
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info("任务完成: success=true, duration=%sms, email=%s", duration_ms, email)
        return RegistrationTaskResult(
            task_id=task_id,
            success=True,
            duration_ms=duration_ms,
            email=email,
            token_file=token_file,
            browser_channel=browser_channel,
            error="",
        )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.exception("任务失败: duration=%sms", duration_ms)
        return RegistrationTaskResult(
            task_id=task_id,
            success=False,
            duration_ms=duration_ms,
            email=email,
            token_file=token_file,
            browser_channel=browser_channel,
            error=str(exc),
        )
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


def run_core_flow(*, auth_url: str = AUTH_URL, headless: bool = False) -> int:
    result = run_registration_task(
        1,
        total_count=1,
        auth_url=auth_url,
        headless=headless,
    )
    return 0 if result.success else 1


def run_batch_flow(
    *,
    count: int,
    concurrency: int,
    auth_url: str,
    headless: bool,
    summary_path: str,
) -> int:
    ensure_dirs()
    logger = build_logger()
    bounded_concurrency = max(1, min(concurrency, count))
    logger.info(
        "开始批量注册: count=%s, concurrency=%s, headless=%s, url=%s",
        count,
        bounded_concurrency,
        headless,
        auth_url,
    )
    started = time.perf_counter()
    results: list[RegistrationTaskResult] = []
    success_count = 0

    with ThreadPoolExecutor(max_workers=bounded_concurrency) as executor:
        futures = {
            executor.submit(
                run_registration_task,
                task_id,
                total_count=count,
                auth_url=auth_url,
                headless=headless,
            ): task_id
            for task_id in range(1, count + 1)
        }
        for completed_count, future in enumerate(as_completed(futures), start=1):
            task_id = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = RegistrationTaskResult(
                    task_id=task_id,
                    success=False,
                    duration_ms=0,
                    email="",
                    token_file="",
                    browser_channel="",
                    error=f"未捕获任务异常: {exc}",
                )
            results.append(result)
            if result.success:
                success_count += 1
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
        "url": auth_url,
        "duration_ms": finished_ms,
        "success_count": success_count,
        "failure_count": count - success_count,
        "results": [asdict(item) for item in ordered_results],
    }
    summary_target = Path(summary_path) if summary_path else DEBUG_DIR / f"batch_run_{_timestamp()}.json"
    _write_batch_summary(summary_target, summary_payload)
    logger.info(
        "批量注册完成: success=%s/%s, duration=%sms, summary=%s",
        success_count,
        count,
        finished_ms,
        summary_target,
    )
    return 0 if success_count == count else 1


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        if args.count < 1:
            raise SystemExit("--count 必须大于等于 1")
        if args.concurrency < 1:
            raise SystemExit("--concurrency 必须大于等于 1")
        if args.count == 1 and args.concurrency == 1:
            return run_core_flow(auth_url=args.url, headless=args.headless)
        return run_batch_flow(
            count=args.count,
            concurrency=args.concurrency,
            auth_url=args.url,
            headless=args.headless,
            summary_path=args.summary_path,
        )
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


