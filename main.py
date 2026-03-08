from __future__ import annotations

from playwright.sync_api import sync_playwright

from captcha_solver import CaptchaAssistant
from page_flow import AuthPageFlow
from utils import build_logger, generate_profile, save_json


def main() -> None:
    logger = build_logger()
    profile = generate_profile()
    save_json(
        "register-profile.json",
        {
            "nickname": profile.nickname,
            "email": profile.email,
            "password": profile.password,
        },
    )
    logger.info("本次测试资料已生成: %s / %s", profile.nickname, profile.email)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        flow = AuthPageFlow(page, logger)
        captcha = CaptchaAssistant(page, logger)

        try:
            flow.open()
            flow.click_register()
            flow.fill_register_form(profile)
            flow.click_start_verify()
            flow.wait_for_captcha_popup()
            captcha.save_debug_artifacts(step="captcha-popup")

            logger.info("请在浏览器中手动拖动滑块完成验证。")
            ok = captcha.wait_for_manual_completion(timeout_ms=180000)
            if not ok:
                raise RuntimeError("验证码未在规定时间内完成")

            captcha.save_debug_artifacts(step="captcha-after-manual")
            flow.click_create_account()
            logger.info("流程执行完成，请观察页面反馈")
            page.wait_for_timeout(5000)
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
