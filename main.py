"""核心注册与滑块验证流程入口。"""
from __future__ import annotations

import sys
import traceback

from playwright.sync_api import sync_playwright

from mail_verification import wait_for_verify_link
from page_flow import AuthPageFlow
from slider_captcha_solver import SliderVerificationFailedError
from token_capture import capture_any_token, wait_for_account_token
from utils import build_logger, generate_profile, load_submit_retry_count, save_account_token

AUTH_URL = "https://chat.z.ai/auth"
VIEWPORT_WIDTH = 1440
VIEWPORT_HEIGHT = 900
POST_WAIT_MS = 5000
LOCALE = "zh-CN"
ANTI_DETECTION_ARGS = [
    "--disable-blink-features=AutomationControlled",
]
VERIFY_FAILED_TEXT = "验证失败，请重试"
CAPTCHA_RETRY_WAIT_MS = 1_500


def _mask_token(token: str) -> str:
    if len(token) <= 16:
        return token
    return f"{token[:10]}...{token[-6:]}"


def run_core_flow() -> int:
    logger = build_logger()
    submit_retry_count = load_submit_retry_count()
    logger.info("提交重试次数配置: %s", submit_retry_count)
    profile = generate_profile(logger)
    logger.info("运行账号: %s / %s", profile.email, profile.password)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=False,
            args=ANTI_DETECTION_ARGS,
        )
        context = browser.new_context(
            locale=LOCALE,
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
        )
        page = context.new_page()
        flow = AuthPageFlow(page, logger)
        flow.open(AUTH_URL)
        previous_token = capture_any_token(page)
        flow.click_register()
        flow.fill_register_form(profile)
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
        token_file = save_account_token(
            profile=profile,
            token=captured.token,
            source=captured.source,
            claims_email=captured.claims_email,
        )
        logger.info("账号 token 已保存: %s", token_file)
        logger.info("token 预览: %s", _mask_token(captured.token))
        logger.info("邮箱验证页注册提交流程已完成")
        page.wait_for_timeout(POST_WAIT_MS)
        context.close()
        browser.close()
    return 0


def main() -> int:
    try:
        return run_core_flow()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


