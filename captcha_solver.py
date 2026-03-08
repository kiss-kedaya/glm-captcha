"""验证码调试与人工协作：只负责检测浮层、保存调试信息、等待人工完成"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.sync_api import Page

from utils import DEBUG_DIR, now_tag, save_json


class CaptchaAssistant:
    def __init__(self, page: Page, logger):
        self.page = page
        self.logger = logger

    def collect_debug_info(self) -> dict[str, Any]:
        return self.page.evaluate(
            """() => {
                const pick = (selector) => {
                    const el = document.querySelector(selector);
                    if (!el) return null;
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return {
                        selector,
                        id: el.id || null,
                        className: typeof el.className === 'string' ? el.className : null,
                        text: (el.innerText || '').trim().slice(0, 200),
                        rect: {x: r.x, y: r.y, width: r.width, height: r.height, left: r.left, top: r.top},
                        display: s.display,
                        visibility: s.visibility,
                        opacity: s.opacity,
                        backgroundImage: s.backgroundImage,
                        src: el.getAttribute('src'),
                    };
                };
                return {
                    url: location.href,
                    title: document.title,
                    dpr: window.devicePixelRatio,
                    viewport: {width: window.innerWidth, height: window.innerHeight},
                    trigger: pick('#aliyunCaptcha-captcha-wrapper'),
                    popup: pick('#aliyunCaptcha-window-float'),
                    imgBox: pick('#aliyunCaptcha-img-box'),
                    bgImg: pick('#aliyunCaptcha-img'),
                    puzzle: pick('#aliyunCaptcha-puzzle'),
                    sliderBody: pick('#aliyunCaptcha-sliding-body'),
                    slider: pick('#aliyunCaptcha-sliding-slider'),
                    sliderText: pick('#aliyunCaptcha-sliding-text'),
                };
            }"""
        )

    def save_debug_artifacts(self, step: str = "captcha") -> None:
        tag = now_tag()
        page_png = DEBUG_DIR / f"{tag}-{step}-page.png"
        self.page.screenshot(path=str(page_png), full_page=True)
        self.logger.info("已保存页面截图: %s", page_png)

        for selector, suffix in [
            ("#aliyunCaptcha-window-float", "popup"),
            ("#aliyunCaptcha-img", "background"),
            ("#aliyunCaptcha-puzzle", "puzzle"),
            ("#aliyunCaptcha-sliding-body", "slider-track"),
            ("#aliyunCaptcha-sliding-slider", "slider"),
        ]:
            locator = self.page.locator(selector).first
            if locator.count() == 0:
                continue
            try:
                path = DEBUG_DIR / f"{tag}-{step}-{suffix}.png"
                locator.screenshot(path=str(path))
                self.logger.info("已保存元素截图: %s", path)
            except Exception as exc:
                self.logger.warning("保存 %s 截图失败: %s", selector, exc)

        info = self.collect_debug_info()
        json_path = save_json(f"{tag}-{step}.json", info)
        self.logger.info("已保存调试 JSON: %s", json_path)

    def wait_for_manual_completion(self, timeout_ms: int = 180000) -> bool:
        self.logger.info("请手动完成滑块验证，最长等待 %.1f 秒", timeout_ms / 1000)
        try:
            self.page.wait_for_function(
                """() => {
                    const popup = document.querySelector('#aliyunCaptcha-window-float');
                    if (!popup) return true;
                    const rect = popup.getBoundingClientRect();
                    const style = getComputedStyle(popup);
                    return rect.width === 0 || rect.height === 0 || style.display === 'none' || popup.offsetParent === null;
                }""",
                timeout=timeout_ms,
            )
            self.logger.info("检测到验证码浮层已关闭")
            return True
        except Exception:
            self.logger.warning("等待人工完成验证码超时")
            return False
