"""滑块验证模式抽象。"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, Protocol

from playwright.sync_api import Page, expect

from slider_captcha_solver import (
    CAPTCHA_BACKGROUND_SELECTORS,
    CAPTCHA_HINT_TEXT,
    CAPTCHA_POPUP_SELECTOR,
    CAPTCHA_POPUP_TIMEOUT_MS,
    CAPTCHA_SHADOW_SELECTORS,
    CAPTCHA_SLIDER_BODY_SELECTORS,
    CAPTCHA_SLIDER_SELECTORS,
    CAPTCHA_TEXT_TIMEOUT_MS,
    SLIDER_FAIL_CLASS,
    SLIDER_RESULT_POLL_MS,
    SLIDER_RESULT_SELECTOR,
    SLIDER_SUCCESS_CLASS,
    SLIDER_SUCCESS_TEXT_KEYWORDS,
    SliderCaptchaSolver,
    SliderVerificationFailedError,
)
from slider_scripts import READ_CAPTCHA_STATE_SCRIPT

SLIDER_MODE_AUTO = "auto"
SLIDER_MODE_MANUAL = "manual"
SUPPORTED_SLIDER_MODES = (SLIDER_MODE_AUTO, SLIDER_MODE_MANUAL)
MANUAL_SLIDER_TIMEOUT_MS = 300_000
SHORT_ELEMENT_TIMEOUT_MS = 300
MANUAL_RESULT_POLL_MS = 50
MANUAL_HIDDEN_SUCCESS_MIN_DISTANCE_PX = 1
MANUAL_POPUP_CLOSED_ERROR = "手动滑块窗口已关闭，未检测到成功状态"


class SliderVerifier(Protocol):
    def solve(self) -> int:
        ...


class ManualSliderVerifier:
    def __init__(
        self,
        page: Page,
        logger,
        *,
        timeout_ms: int = MANUAL_SLIDER_TIMEOUT_MS,
        event_sink=None,
    ) -> None:
        self.page = page
        self.logger = logger
        self.timeout_ms = timeout_ms
        self.event_sink = event_sink

    def _emit_event(self, event: str, **fields: object) -> None:
        if self.event_sink is None:
            return
        payload: dict[str, object] = {"event": event}
        payload.update(fields)
        self.event_sink(payload)

    def _wait_popup_visible(self) -> None:
        popup = self.page.locator(CAPTCHA_POPUP_SELECTOR).first
        try:
            popup.wait_for(state="visible", timeout=CAPTCHA_POPUP_TIMEOUT_MS)
            return
        except Exception:
            expect(self.page.get_by_text(CAPTCHA_HINT_TEXT)).to_be_visible(
                timeout=CAPTCHA_TEXT_TIMEOUT_MS
            )

    def _popup_visible(self) -> bool:
        popup = self.page.locator(CAPTCHA_POPUP_SELECTOR).first
        try:
            return popup.is_visible(timeout=SHORT_ELEMENT_TIMEOUT_MS)
        except Exception:
            return False

    def _slider_visible(self) -> bool:
        popup = self.page.locator(CAPTCHA_POPUP_SELECTOR).first
        slider = popup.locator(CAPTCHA_SLIDER_SELECTORS[0]).first
        try:
            return slider.is_visible(timeout=SHORT_ELEMENT_TIMEOUT_MS)
        except Exception:
            return False

    def _read_result_state(self) -> tuple[str, str]:
        result = self.page.locator(SLIDER_RESULT_SELECTOR).first
        try:
            class_name = str(result.get_attribute("class", timeout=SHORT_ELEMENT_TIMEOUT_MS) or "").lower()
        except Exception:
            class_name = ""
        try:
            text = str(result.inner_text(timeout=SHORT_ELEMENT_TIMEOUT_MS) or "").strip()
        except Exception:
            text = ""
        return class_name, text

    def _read_slider_distance(self, fallback: int) -> int:
        try:
            state = self.page.evaluate(
                READ_CAPTCHA_STATE_SCRIPT,
                {
                    "popupSelector": CAPTCHA_POPUP_SELECTOR,
                    "sliderSelectors": CAPTCHA_SLIDER_SELECTORS,
                    "sliderBodySelectors": CAPTCHA_SLIDER_BODY_SELECTORS,
                    "shadowSelectors": CAPTCHA_SHADOW_SELECTORS,
                    "backgroundSelectors": CAPTCHA_BACKGROUND_SELECTORS,
                },
            )
        except Exception:
            return fallback
        if not isinstance(state, dict):
            return fallback
        slider_travel = state.get("sliderTravel")
        if slider_travel is None:
            return fallback
        try:
            return max(int(slider_travel), 0)
        except Exception:
            return fallback

    def _is_success(self, class_name: str, text: str) -> bool:
        return SLIDER_SUCCESS_CLASS in class_name or any(
            keyword in text for keyword in SLIDER_SUCCESS_TEXT_KEYWORDS
        )

    def _fail_popup_hidden(self, last_distance: int) -> None:
        self.logger.warning(MANUAL_POPUP_CLOSED_ERROR)
        self._emit_event(
            "manual_verification_failed",
            class_name="popup_hidden",
            text="",
            slider_distance=last_distance,
        )
        raise SliderVerificationFailedError(MANUAL_POPUP_CLOSED_ERROR)

    def _accept_hidden_success(self, last_distance: int) -> int:
        self.logger.info(
            "手动滑块窗口已关闭，按已完成拖动判定成功: slider_distance=%spx",
            last_distance,
        )
        self._emit_event(
            "manual_verification_succeeded",
            slider_distance=last_distance,
            result="popup_hidden_after_drag",
        )
        return last_distance

    def solve(self) -> int:
        self._wait_popup_visible()
        timeout_seconds = max(1, self.timeout_ms // 1000)
        self.logger.info("已切换到手动滑块模式，请在 %s 秒内完成验证", timeout_seconds)
        self._emit_event("manual_verification_started", timeout_ms=self.timeout_ms)
        deadline = time.time() + (self.timeout_ms / 1000)
        last_distance = 0
        ready = False
        while time.time() < deadline:
            class_name, text = self._read_result_state()
            if self._is_success(class_name, text):
                final_distance = self._read_slider_distance(last_distance)
                self._emit_event(
                    "manual_verification_succeeded",
                    slider_distance=final_distance,
                    result=text or class_name,
                )
                return final_distance
            if not self._popup_visible():
                if last_distance >= MANUAL_HIDDEN_SUCCESS_MIN_DISTANCE_PX:
                    return self._accept_hidden_success(last_distance)
                self._fail_popup_hidden(last_distance)
            if self._slider_visible() and SLIDER_FAIL_CLASS not in class_name:
                if not ready:
                    ready = True
                    self.logger.info("滑块题目已就绪，等待手动拖动结果")
                    self._emit_event("manual_slider_ready")
                last_distance = self._read_slider_distance(last_distance)
            if ready and SLIDER_FAIL_CLASS in class_name:
                self._emit_event("manual_verification_failed", class_name=class_name, text=text)
                raise SliderVerificationFailedError(f"手动滑块验证失败: class={class_name}, text={text}")
            self.page.wait_for_timeout(MANUAL_RESULT_POLL_MS)
        if ready:
            raise RuntimeError("等待手动滑块结果超时")
        raise SliderVerificationFailedError("等待手动滑块就绪超时")


def normalize_slider_mode(slider_mode: str) -> str:
    normalized = str(slider_mode).strip().lower()
    if normalized not in SUPPORTED_SLIDER_MODES:
        raise ValueError(f"不支持的滑块模式: {slider_mode}")
    return normalized


def create_slider_verifier(
    page: Page,
    logger,
    *,
    slider_mode: str,
    event_sink=None,
    sample_dir: Optional[Path] = None,
    manual_timeout_ms: int = MANUAL_SLIDER_TIMEOUT_MS,
) -> SliderVerifier:
    normalized_mode = normalize_slider_mode(slider_mode)
    if normalized_mode == SLIDER_MODE_AUTO:
        return SliderCaptchaSolver(
            page,
            logger,
            event_sink=event_sink,
            sample_dir=sample_dir,
        )
    return ManualSliderVerifier(
        page,
        logger,
        timeout_ms=manual_timeout_ms,
        event_sink=event_sink,
    )
