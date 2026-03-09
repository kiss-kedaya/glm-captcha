"""页面流程：打开认证页、切换注册、填写资料、触发验证码、提交账号。"""
from __future__ import annotations

from typing import Any
from typing import Optional

from playwright.sync_api import Frame, Locator, Page

from slider_captcha_solver import SliderCaptchaSolver
from utils import RegisterProfile, sleep_random

PAGE_NAVIGATION_TIMEOUT_MS = 120_000
ELEMENT_ACTION_TIMEOUT_MS = 10_000
ELEMENT_VISIBLE_TIMEOUT_MS = 1_500
REGISTER_SWITCH_RETRY_COUNT = 3
REGISTER_SWITCH_WAIT_MS = 2_500
VERIFY_FAILED_TOAST_WAIT_MS = 4_000
SIGNUP_RESPONSE_TIMEOUT_MS = 20_000
REGISTER_PAGE_MARKER = "已经拥有账号了？"
LOGIN_PAGE_MARKER = "没有账号？"
VERIFY_FAILED_TEXT = "验证失败，请重试"
VERIFY_FAILED_TOAST_SELECTOR = "li[data-sonner-toast][data-type='error'] div[data-title]"
SIGNUP_API_PATH = "/api/v1/auths/signup"
CAPTCHA_POPUP_SELECTOR = "#aliyunCaptcha-window-float"
SLIDER_RESULT_SELECTOR = "#aliyunCaptcha-sliding-text"
SLIDER_FAIL_CLASS = "fail"

REGISTER_BUTTON_TEXT = "注册"
LOGIN_BUTTON_TEXT = "登录"
CREATE_ACCOUNT_BUTTON_TEXT = "创建账号"
COMPLETE_REGISTER_BUTTON_TEXT = "完成注册"
VERIFY_USERNAME_SELECTOR = "#username"
VERIFY_EMAIL_SELECTOR = "#email"
VERIFY_PASSWORD_SELECTOR = "#password"
VERIFY_CONFIRM_PASSWORD_SELECTOR = "#confirmPassword"

NAME_SELECTORS = [
    "input[autocomplete='name']",
    "input[placeholder='输入您的名称']",
]
EMAIL_SELECTORS = [
    "input[autocomplete='email'][name='email']",
    "input[placeholder='输入您的电子邮箱']",
    "input[placeholder*='邮箱']",
    "input[type='email']",
    "input[name='email']",
    "input[autocomplete='email']",
]
PASSWORD_SELECTORS = [
    "input[name='new-password'][autocomplete='new-password']",
    "input[placeholder='输入您的密码']",
    "input[placeholder*='密码']",
    "input[type='password']",
    "input[name='new-password']",
    "input[name='password']",
    "input[autocomplete='new-password']",
]
CREATE_ACCOUNT_BUTTON_SELECTORS = [
    "button.ButtonCreateAccount[type='submit']",
    "button[type='submit']:has-text('创建账号')",
    "button.ButtonCreateAccount:has-text('创建账号')",
]


class AuthPageFlow:
    def __init__(self, page: Page, logger) -> None:
        self.page = page
        self.logger = logger
        self.slider_solver = SliderCaptchaSolver(page, logger)

    def open(self, url: str = "https://chat.z.ai/auth") -> None:
        self.page.goto(url, wait_until="domcontentloaded", timeout=PAGE_NAVIGATION_TIMEOUT_MS)
        self.page.wait_for_load_state("networkidle", timeout=PAGE_NAVIGATION_TIMEOUT_MS)
        self.logger.info("已打开页面: %s", self.page.url)

    def _wait_register_form_ready(self, timeout_ms: int) -> bool:
        try:
            self.page.wait_for_function(
                """({ registerMarker }) => {
                    const nameInput = document.querySelector(\"input[autocomplete='name']\");
                    const registerPassword = document.querySelector(\"input[name='new-password'][autocomplete='new-password']\");
                    const registerFooter = Array.from(document.querySelectorAll('div'))
                        .some((node) => (node.textContent || '').includes(registerMarker));
                    return Boolean(nameInput || registerPassword || registerFooter);
                }""",
                arg={"registerMarker": REGISTER_PAGE_MARKER},
                timeout=timeout_ms,
            )
            return True
        except Exception:
            return False

    def _register_switch_button(self) -> Locator:
        footer_register = self.page.locator(
            f"div:has-text('{LOGIN_PAGE_MARKER}') button[type='button']:has-text('{REGISTER_BUTTON_TEXT}')"
        ).first
        try:
            if footer_register.is_visible(timeout=ELEMENT_VISIBLE_TIMEOUT_MS):
                return footer_register
        except Exception:
            pass
        return self.page.get_by_role("button", name=REGISTER_BUTTON_TEXT).first

    def click_register(self) -> None:
        if self._wait_register_form_ready(timeout_ms=500):
            self.logger.info("已处于注册表单状态，跳过注册按钮点击")
            return
        for attempt in range(1, REGISTER_SWITCH_RETRY_COUNT + 1):
            register = self._register_switch_button()
            register.wait_for(state="visible", timeout=ELEMENT_ACTION_TIMEOUT_MS)
            try:
                register.click(timeout=ELEMENT_ACTION_TIMEOUT_MS)
            except Exception:
                register.click(timeout=ELEMENT_ACTION_TIMEOUT_MS, force=True)
            if self._wait_register_form_ready(timeout_ms=REGISTER_SWITCH_WAIT_MS):
                sleep_random()
                self.logger.info("已点击注册按钮，并进入注册表单（第%s次尝试）", attempt)
                return
        raise RuntimeError("点击注册后未进入注册表单")

    def _first_visible(self, scope: Page | Frame, selectors: list[str]) -> Optional[Locator]:
        for selector in selectors:
            locator = scope.locator(selector).first
            try:
                if locator.is_visible(timeout=ELEMENT_VISIBLE_TIMEOUT_MS):
                    return locator
            except Exception:
                continue
        return None

    def fill_register_form(self, profile: RegisterProfile) -> None:
        name_input = self._first_visible(self.page, NAME_SELECTORS)
        email = self._first_visible(self.page, EMAIL_SELECTORS)
        password = self._first_visible(self.page, PASSWORD_SELECTORS)
        if name_input is not None:
            name_input.fill(profile.name)
            self.logger.info("已填写名称")
            sleep_random()
        else:
            self.logger.warning("未找到名称输入框，页面可能仍处于登录态")
        if email is None or password is None:
            raise RuntimeError("未找到邮箱或密码输入框")
        email.fill(profile.email)
        sleep_random()
        password.fill(profile.password)
        sleep_random()
        self.logger.info("已填写邮箱与密码")

    def click_start_verify(self) -> None:
        popup = self.page.locator(CAPTCHA_POPUP_SELECTOR).first
        try:
            if popup.is_visible(timeout=ELEMENT_VISIBLE_TIMEOUT_MS):
                self.logger.info("验证码浮层已在前台，直接进入重试验证")
                return
        except Exception:
            pass
        trigger = self.page.locator("#aliyunCaptcha-captcha-text").first
        try:
            trigger.wait_for(state="visible", timeout=ELEMENT_ACTION_TIMEOUT_MS)
        except Exception as exc:
            try:
                if popup.is_visible(timeout=ELEMENT_VISIBLE_TIMEOUT_MS):
                    self.logger.info("验证码浮层已在前台，直接进入重试验证")
                    return
            except Exception:
                pass
            raise RuntimeError("未找到“点击开始验证”入口，当前页面状态不在可触发验证阶段") from exc
        try:
            trigger.click(timeout=ELEMENT_ACTION_TIMEOUT_MS)
        except Exception:
            self.page.evaluate(
                """(selector) => {
                    const element = document.querySelector(selector);
                    if (!element) throw new Error("未找到验证码触发元素");
                    element.click();
                }""",
                "#aliyunCaptcha-captcha-text",
            )
        self.logger.info("已点击开始验证")

    def has_verify_failed_toast(self, timeout_ms: int = VERIFY_FAILED_TOAST_WAIT_MS) -> bool:
        try:
            self.page.wait_for_function(
                """({ selector, failedText }) => {
                    const nodes = Array.from(document.querySelectorAll(selector));
                    return nodes.some((node) => (node.textContent || '').includes(failedText));
                }""",
                arg={"selector": VERIFY_FAILED_TOAST_SELECTOR, "failedText": VERIFY_FAILED_TEXT},
                timeout=timeout_ms,
            )
            return True
        except Exception:
            return False

    def has_slider_failed_status(self, timeout_ms: int = 1_500) -> bool:
        try:
            self.page.wait_for_function(
                """({ selector, failClass }) => {
                    const element = document.querySelector(selector);
                    if (!element) return false;
                    const className = String(element.getAttribute("class") || "").toLowerCase();
                    return className.includes(failClass);
                }""",
                arg={"selector": SLIDER_RESULT_SELECTOR, "failClass": SLIDER_FAIL_CLASS},
                timeout=timeout_ms,
            )
            return True
        except Exception:
            return False

    def solve_slider_captcha(self) -> int:
        slider_distance = self.slider_solver.solve()
        self.logger.info("滑块验证流程执行完成，推荐距离: %spx", slider_distance)
        return slider_distance

    def click_create_account(self) -> None:
        button = self._first_visible(self.page, CREATE_ACCOUNT_BUTTON_SELECTORS)
        if button is None:
            fallback = self.page.get_by_role("button", name=CREATE_ACCOUNT_BUTTON_TEXT).first
            if not fallback.is_visible(timeout=ELEMENT_VISIBLE_TIMEOUT_MS):
                raise RuntimeError("未找到创建账号按钮")
            button = fallback
        button.click(timeout=ELEMENT_ACTION_TIMEOUT_MS)
        self.logger.info("已点击创建账号按钮")

    def _wait_signup_response_compat(self, timeout_ms: int):
        wait_for_response = getattr(self.page, "wait_for_response", None)
        if callable(wait_for_response):
            self.click_create_account()
            return wait_for_response(
                lambda item: SIGNUP_API_PATH in item.url and item.request.method.upper() == "POST",
                timeout=timeout_ms,
            )
        with self.page.expect_response(
            lambda item: SIGNUP_API_PATH in item.url and item.request.method.upper() == "POST",
            timeout=timeout_ms,
        ) as response_info:
            self.click_create_account()
        return response_info.value

    def submit_signup_and_get_result(self, timeout_ms: int = SIGNUP_RESPONSE_TIMEOUT_MS) -> tuple[bool, str]:
        try:
            response = self._wait_signup_response_compat(timeout_ms)
        except Exception as exc:
            if self.has_slider_failed_status(timeout_ms=1_200) or self.has_verify_failed_toast(timeout_ms=1_200):
                return False, VERIFY_FAILED_TEXT
            return False, f"等待 signup 响应失败: {exc}"
        try:
            payload: Any = response.json()
        except Exception as exc:
            raise RuntimeError(f"signup 响应不是有效 JSON: status={response.status}") from exc
        if isinstance(payload, dict) and payload.get("success") is True:
            return True, ""
        if isinstance(payload, dict):
            message = payload.get("message") or payload.get("error") or str(payload)
            return False, str(message)
        return False, f"signup 返回非 JSON 对象: {payload}"

    def open_verify_link(self, verify_link: str) -> None:
        self.page.goto(verify_link, wait_until="domcontentloaded", timeout=PAGE_NAVIGATION_TIMEOUT_MS)
        self.page.wait_for_load_state("networkidle", timeout=PAGE_NAVIGATION_TIMEOUT_MS)
        self.logger.info("已打开邮箱验证链接")

    def complete_register_after_verify(self, profile: RegisterProfile) -> None:
        username_input = self.page.locator(VERIFY_USERNAME_SELECTOR).first
        email_input = self.page.locator(VERIFY_EMAIL_SELECTOR).first
        password_input = self.page.locator(VERIFY_PASSWORD_SELECTOR).first
        confirm_password_input = self.page.locator(VERIFY_CONFIRM_PASSWORD_SELECTOR).first
        complete_button = self.page.get_by_role("button", name=COMPLETE_REGISTER_BUTTON_TEXT).first
        username_input.wait_for(state="visible", timeout=ELEMENT_ACTION_TIMEOUT_MS)
        email_input.wait_for(state="visible", timeout=ELEMENT_ACTION_TIMEOUT_MS)
        show_name = username_input.input_value(timeout=ELEMENT_ACTION_TIMEOUT_MS).strip()
        show_email = email_input.input_value(timeout=ELEMENT_ACTION_TIMEOUT_MS).strip().lower()
        expected_name = profile.name.strip()
        expected_email = profile.email.strip().lower()
        if show_name != expected_name:
            raise RuntimeError(f"验证页名称不一致: expected={expected_name}, actual={show_name}")
        if show_email != expected_email:
            raise RuntimeError(f"验证页邮箱不一致: expected={expected_email}, actual={show_email}")
        self.logger.info("验证页名称和邮箱核对通过")
        password_input.wait_for(state="visible", timeout=ELEMENT_ACTION_TIMEOUT_MS)
        confirm_password_input.wait_for(state="visible", timeout=ELEMENT_ACTION_TIMEOUT_MS)
        password_input.fill(profile.password)
        sleep_random()
        confirm_password_input.fill(profile.password)
        sleep_random()
        complete_button.wait_for(state="visible", timeout=ELEMENT_ACTION_TIMEOUT_MS)
        complete_button.click(timeout=ELEMENT_ACTION_TIMEOUT_MS)
        self.logger.info("已点击完成注册按钮")
