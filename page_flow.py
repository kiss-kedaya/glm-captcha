"""页面流程：打开认证页、切换注册、填写资料、触发验证码、提交账号"""
from __future__ import annotations

from typing import Optional

from playwright.sync_api import Frame, Locator, Page, expect

from utils import RegisterProfile, sleep_random


class AuthPageFlow:
    def __init__(self, page: Page, logger):
        self.page = page
        self.logger = logger

    def open(self, url: str = "https://chat.z.ai/auth") -> None:
        self.page.goto(url, wait_until="domcontentloaded", timeout=120000)
        self.page.wait_for_load_state("networkidle", timeout=120000)
        self.logger.info("已打开页面: %s", self.page.url)

    def click_register(self) -> None:
        register = self.page.get_by_role("button", name="注册")
        register.click(timeout=10000)
        sleep_random()
        self.logger.info("已点击注册按钮")

    def _first_visible(self, scope: Page | Frame, selectors: list[str]) -> Optional[Locator]:
        for selector in selectors:
            locator = scope.locator(selector).first
            try:
                if locator.is_visible(timeout=1500):
                    return locator
            except Exception:
                continue
        return None

    def fill_register_form(self, profile: RegisterProfile) -> None:
        candidates = {
            "nickname": [
                "input[placeholder*='名称']",
                "input[placeholder*='昵称']",
                "input[name*='name']",
                "input[autocomplete='nickname']",
            ],
            "email": [
                "input[placeholder*='邮箱']",
                "input[type='email']",
                "input[name='email']",
                "input[autocomplete='email']",
            ],
            "password": [
                "input[placeholder*='密码']",
                "input[type='password']",
                "input[name='password']",
                "input[autocomplete='new-password']",
            ],
        }

        nickname = self._first_visible(self.page, candidates["nickname"])
        email = self._first_visible(self.page, candidates["email"])
        password = self._first_visible(self.page, candidates["password"])

        if nickname is None:
            self.logger.warning("未找到昵称输入框，页面可能仍停留在登录态或注册表单结构不同")
        else:
            nickname.fill(profile.nickname)
            self.logger.info("已填写昵称")
            sleep_random()

        if email is None or password is None:
            raise RuntimeError("未找到邮箱或密码输入框")

        email.fill(profile.email)
        sleep_random()
        password.fill(profile.password)
        sleep_random()
        self.logger.info("已填写邮箱与密码")

    def click_start_verify(self) -> None:
        trigger = self._first_visible(
            self.page,
            [
                "#aliyunCaptcha-captcha-text",
                "#aliyunCaptcha-captcha-wrapper",
                "#captcha-element",
                "text=点击开始验证",
            ],
        )
        if trigger is None:
            raise RuntimeError("未找到验证码触发区域")
        trigger.click(timeout=10000)
        self.logger.info("已点击开始验证")

    def wait_for_captcha_popup(self) -> None:
        popup = self.page.locator("#aliyunCaptcha-window-float").first
        try:
            popup.wait_for(state="visible", timeout=10000)
        except Exception:
            expect(self.page.get_by_text("请完成安全验证")).to_be_visible(timeout=10000)
        self.logger.info("验证码浮层已出现")

    def click_create_account(self) -> None:
        button = None
        for name in ["创建账号", "注册", "继续", "下一步"]:
            locator = self.page.get_by_role("button", name=name)
            try:
                if locator.first.is_visible(timeout=1000):
                    button = locator.first
                    break
            except Exception:
                continue
        if button is None:
            raise RuntimeError("未找到创建账号按钮")
        button.click(timeout=10000)
        self.logger.info("已点击创建账号按钮")
