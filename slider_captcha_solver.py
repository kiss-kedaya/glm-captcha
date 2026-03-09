#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""求解器"""

from __future__ import annotations

import base64
import time
from typing import Callable, Optional
from urllib.parse import unquote_to_bytes

import ddddocr
import requests
from playwright.sync_api import Page, expect

from slider_scripts import BUILD_MAPPING_SCRIPT, HUMAN_DRAG_SCRIPT

AuthDistanceCalculator = Callable[[str, str], int]

REQUEST_TIMEOUT_SECONDS = 10
ELEMENT_ACTION_TIMEOUT_MS = 10_000
CAPTCHA_POPUP_TIMEOUT_MS = 10_000
CAPTCHA_TEXT_TIMEOUT_MS = 10_000
MAPPING_DRAG_DISTANCE_PX = 300
MAPPING_FORWARD_DURATION_MS = 8_000
MAPPING_BACKWARD_DURATION_MS = 1_500
HUMAN_DRAG_DURATION_MS = 1_200
HUMAN_DRAG_NOISE_X = 1
HUMAN_DRAG_NOISE_Y = 3
SLIDER_DISTANCE_COMPENSATION_PX = 1
MIN_MAPPING_POINTS = 10
DATA_URL_PREFIX = "data:"
SLIDER_RESULT_SELECTOR = "#aliyunCaptcha-sliding-text"
SLIDER_RESULT_WAIT_MS = 6_000
SLIDER_RESULT_POLL_MS = 200
SLIDER_READY_TIMEOUT_MS = 8_000
SLIDER_FAIL_CLASS = "fail"
SLIDER_SUCCESS_CLASS = "success"
SLIDER_SUCCESS_TEXT_KEYWORDS = ("验证通过", "验证成功", "通过")
RETRYABLE_SLIDER_ERROR_KEYWORDS = (
    "滑块元素不可见",
    "未找到滑块元素",
    "未找到拼图阴影元素",
    "未找到验证码浮层容器",
    "映射点数量过少",
    "滑块验证失败",
    "滑块拖动后未检测到明确结果",
)

CAPTCHA_HINT_TEXT = "请完成安全验证"
CAPTCHA_POPUP_SELECTOR = "#aliyunCaptcha-window-float"
CAPTCHA_SLIDER_SELECTORS = ["#aliyunCaptcha-sliding-slider"]
CAPTCHA_SHADOW_SELECTORS = ["#aliyunCaptcha-puzzle"]
CAPTCHA_BACKGROUND_SELECTORS = ["#aliyunCaptcha-img"]


def decode_data_url(data_url: str) -> bytes:
    if "," not in data_url:
        raise ValueError("data URL 格式错误：缺少逗号分隔符")
    header, payload = data_url.split(",", maxsplit=1)
    if ";base64" in header:
        return base64.b64decode(payload)
    return unquote_to_bytes(payload)


def fetch_image_bytes(image_source: str) -> bytes:
    if image_source.startswith(DATA_URL_PREFIX):
        return decode_data_url(image_source)
    response = requests.get(image_source, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.content


def calculate_shadow_distance(shadow_url: str, background_url: str) -> int:
    shadow_bytes = fetch_image_bytes(shadow_url)
    background_bytes = fetch_image_bytes(background_url)
    detector = ddddocr.DdddOcr(det=False, ocr=False)
    result = detector.slide_match(shadow_bytes, background_bytes)
    if "target" not in result or not result["target"]:
        raise RuntimeError(f"ddddocr 返回结果异常: {result}")
    return int(result["target"][0])


class SliderVerificationFailedError(RuntimeError):
    pass


class SliderCaptchaSolver:
    def __init__(
        self,
        page: Page,
        logger,
        distance_calculator: Optional[AuthDistanceCalculator] = None,
    ) -> None:
        self.page = page
        self.logger = logger
        self.distance_calculator = distance_calculator or calculate_shadow_distance

    def ensure_popup_visible(self) -> None:
        popup = self.page.locator(CAPTCHA_POPUP_SELECTOR).first
        try:
            popup.wait_for(state="visible", timeout=CAPTCHA_POPUP_TIMEOUT_MS)
        except Exception:
            expect(self.page.get_by_text(CAPTCHA_HINT_TEXT)).to_be_visible(
                timeout=CAPTCHA_TEXT_TIMEOUT_MS
            )
        self.logger.info("验证码浮层已出现")

    def _wait_slider_ready(self) -> None:
        popup = self.page.locator(CAPTCHA_POPUP_SELECTOR).first
        slider = popup.locator(CAPTCHA_SLIDER_SELECTORS[0]).first
        result = self.page.locator(SLIDER_RESULT_SELECTOR).first
        deadline = time.time() + (SLIDER_READY_TIMEOUT_MS / 1000)
        while time.time() < deadline:
            try:
                if not popup.is_visible(timeout=300):
                    raise SliderVerificationFailedError("验证码浮层已关闭，无法继续滑块验证")
            except Exception as exc:
                raise SliderVerificationFailedError("验证码浮层已关闭，无法继续滑块验证") from exc
            try:
                class_name = str(result.get_attribute("class", timeout=300) or "").lower()
            except Exception:
                class_name = ""
            if SLIDER_FAIL_CLASS in class_name:
                self.page.wait_for_timeout(SLIDER_RESULT_POLL_MS)
                continue
            try:
                if slider.is_visible(timeout=300):
                    return
            except Exception:
                pass
            self.page.wait_for_timeout(SLIDER_RESULT_POLL_MS)
        raise SliderVerificationFailedError("等待新一轮滑块就绪超时")

    def _extract_captcha_image_urls(self) -> tuple[str, str]:
        popup = self.page.locator(CAPTCHA_POPUP_SELECTOR).first
        shadow_url = popup.locator(CAPTCHA_SHADOW_SELECTORS[0]).first.get_attribute(
            "src",
            timeout=ELEMENT_ACTION_TIMEOUT_MS,
        )
        background_url = popup.locator(CAPTCHA_BACKGROUND_SELECTORS[0]).first.get_attribute(
            "src",
            timeout=ELEMENT_ACTION_TIMEOUT_MS,
        )
        if not shadow_url:
            raise RuntimeError("未获取到滑块图片 URL")
        if not background_url:
            raise RuntimeError("未获取到背景图片 URL")
        return shadow_url, background_url

    def _build_mapping(self) -> dict[int, int]:
        mapping_raw = self.page.evaluate(
            BUILD_MAPPING_SCRIPT,
            {
                "popupSelector": CAPTCHA_POPUP_SELECTOR,
                "sliderSelectors": CAPTCHA_SLIDER_SELECTORS,
                "shadowSelectors": CAPTCHA_SHADOW_SELECTORS,
                "dragDistance": MAPPING_DRAG_DISTANCE_PX,
                "forwardMs": MAPPING_FORWARD_DURATION_MS,
                "backwardMs": MAPPING_BACKWARD_DURATION_MS,
            },
        )
        if not isinstance(mapping_raw, dict):
            raise RuntimeError(f"映射表返回格式错误: {mapping_raw}")
        mapping = {int(k): int(v) for k, v in mapping_raw.items()}
        if len(mapping) < MIN_MAPPING_POINTS:
            raise RuntimeError(f"映射点数量过少，实际 {len(mapping)}，无法用于定位")
        return mapping

    def _find_slider_distance(self, mapping: dict[int, int], shadow_distance: int) -> int:
        closest_slider = min(mapping.keys(), key=lambda key: abs(mapping[key] - shadow_distance))
        self.logger.info("影子距离 %spx 匹配滑块距离 %spx", shadow_distance, closest_slider)
        return int(closest_slider)

    def _drag_slider(self, drag_distance: int) -> None:
        self.page.evaluate(
            HUMAN_DRAG_SCRIPT,
            {
                "popupSelector": CAPTCHA_POPUP_SELECTOR,
                "sliderSelectors": CAPTCHA_SLIDER_SELECTORS,
                "dragDistance": drag_distance,
                "dragDuration": HUMAN_DRAG_DURATION_MS,
                "noiseX": HUMAN_DRAG_NOISE_X,
                "noiseY": HUMAN_DRAG_NOISE_Y,
            },
        )
        self.logger.info("已执行拟人拖动: %spx", drag_distance)

    def _wait_slider_result(self) -> None:
        popup = self.page.locator(CAPTCHA_POPUP_SELECTOR).first
        result = self.page.locator(SLIDER_RESULT_SELECTOR).first
        deadline = time.time() + (SLIDER_RESULT_WAIT_MS / 1000)
        while time.time() < deadline:
            try:
                if not popup.is_visible(timeout=300):
                    self.logger.info("滑块验证结果: 验证浮层已关闭，判定成功")
                    return
            except Exception:
                self.logger.info("滑块验证结果: 验证浮层不可见，判定成功")
                return
            try:
                class_name = str(result.get_attribute("class", timeout=300) or "").lower()
                text = str(result.inner_text(timeout=300) or "").strip()
            except Exception:
                self.page.wait_for_timeout(SLIDER_RESULT_POLL_MS)
                continue
            if SLIDER_FAIL_CLASS in class_name:
                raise SliderVerificationFailedError(
                    f"滑块验证失败: class={class_name}, text={text}"
                )
            if SLIDER_SUCCESS_CLASS in class_name or any(item in text for item in SLIDER_SUCCESS_TEXT_KEYWORDS):
                self.logger.info("滑块验证结果: class=%s, text=%s", class_name, text)
                return
            self.page.wait_for_timeout(SLIDER_RESULT_POLL_MS)
        raise RuntimeError("滑块拖动后未检测到明确结果")

    def _raise_retryable_failure(self, exc: Exception) -> None:
        message = str(exc)
        if any(keyword in message for keyword in RETRYABLE_SLIDER_ERROR_KEYWORDS):
            raise SliderVerificationFailedError(message) from exc
        raise exc

    def solve(self) -> int:
        try:
            self.ensure_popup_visible()
            self._wait_slider_ready()
            shadow_url, background_url = self._extract_captcha_image_urls()
            self.logger.info("验证码图片 URL 已提取")
            shadow_distance = self.distance_calculator(shadow_url, background_url)
            self.logger.info("计算得到影子距离: %spx", shadow_distance)
            mapping = self._build_mapping()
            self.logger.info("映射表构建完成，共 %s 个点", len(mapping))
            slider_distance = self._find_slider_distance(mapping, shadow_distance)
            drag_distance = slider_distance + SLIDER_DISTANCE_COMPENSATION_PX
            self._drag_slider(drag_distance)
            self._wait_slider_result()
            return slider_distance
        except SliderVerificationFailedError:
            raise
        except Exception as exc:
            self._raise_retryable_failure(exc)
            raise RuntimeError("unreachable")




