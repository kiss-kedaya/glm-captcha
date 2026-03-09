#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""求解器"""

from __future__ import annotations

import base64
import hashlib
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import unquote_to_bytes, urlparse

import ddddocr
import requests
from playwright.sync_api import Page, expect

from slider_scripts import READ_CAPTCHA_STATE_SCRIPT

AuthMatchCalculator = Callable[[str, str], "CaptchaImageMatch"]
StructuredEventSink = Callable[[dict[str, object]], None]

REQUEST_TIMEOUT_SECONDS = 10
ELEMENT_ACTION_TIMEOUT_MS = 10_000
CAPTCHA_POPUP_TIMEOUT_MS = 10_000
CAPTCHA_TEXT_TIMEOUT_MS = 10_000
HUMAN_DRAG_NOISE_Y = 1.2
HUMAN_DRAG_HOVER_MIN_MS = 25
HUMAN_DRAG_HOVER_MAX_MS = 60
HUMAN_DRAG_HOLD_MIN_MS = 45
HUMAN_DRAG_HOLD_MAX_MS = 90
HUMAN_DRAG_STEP_WAIT_MIN_MS = 6
HUMAN_DRAG_STEP_WAIT_MAX_MS = 16
HUMAN_DRAG_FINE_WAIT_MIN_MS = 8
HUMAN_DRAG_FINE_WAIT_MAX_MS = 18
HUMAN_DRAG_RELEASE_MIN_MS = 45
HUMAN_DRAG_RELEASE_MAX_MS = 90
HUMAN_DRAG_COARSE_WINDOW_PX = 6
HUMAN_DRAG_TARGET_TOLERANCE_PX = 1
HUMAN_DRAG_MAX_COARSE_STEPS = 36
HUMAN_DRAG_MAX_FINE_STEPS = 16
HUMAN_DRAG_MIN_STEP_PX = 6
HUMAN_DRAG_MAX_STEP_PX = 18
HUMAN_DRAG_MIN_FINE_STEP_PX = 1
HUMAN_DRAG_MAX_FINE_STEP_PX = 3
HUMAN_DRAG_STATE_POLL_ROUNDS = 2
HUMAN_DRAG_STATE_POLL_WAIT_MIN_MS = 8
HUMAN_DRAG_STATE_POLL_WAIT_MAX_MS = 16
HUMAN_DRAG_MIN_TOTAL_MS = 460
HUMAN_DRAG_MIN_TOTAL_SHORT_MS = 540
TARGET_SHADOW_BASE_BIAS_PX = 1
TARGET_SHADOW_MIN_BIAS_PX = -3
TARGET_SHADOW_MAX_BIAS_PX = 6
SOLVER_MAX_INTERNAL_ATTEMPTS = 3
SOLVER_RETRY_WAIT_MS = 1_200
CAPTCHA_REFRESH_WAIT_MS = 900
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
    "未找到背景图元素",
    "未找到验证码浮层容器",
    "验证码轨道不可见",
    "滑块验证失败",
    "滑块拖动后未检测到明确结果",
)

CAPTCHA_HINT_TEXT = "请完成安全验证"
CAPTCHA_POPUP_SELECTOR = "#aliyunCaptcha-window-float"
CAPTCHA_SLIDER_SELECTORS = ["#aliyunCaptcha-sliding-slider"]
CAPTCHA_SLIDER_BODY_SELECTORS = ["#aliyunCaptcha-sliding-body"]
CAPTCHA_SHADOW_SELECTORS = ["#aliyunCaptcha-puzzle"]
CAPTCHA_BACKGROUND_SELECTORS = ["#aliyunCaptcha-img"]
CAPTCHA_REFRESH_SELECTOR = "#aliyunCaptcha-btn-refresh"
DATA_URL_PREFIX = "data:"

_OCR_DETECTOR: Optional[ddddocr.DdddOcr] = None


@dataclass(frozen=True)
class CaptchaImageMatch:
    target_left: int
    target_top: int
    target_right: int
    target_bottom: int
    target_x: int
    target_y: int

    def to_display_offset(self, background_display_width: float, background_natural_width: int) -> int:
        if background_display_width <= 0:
            raise RuntimeError("背景图显示宽度异常")
        natural_width = background_natural_width if background_natural_width > 0 else int(
            round(background_display_width)
        )
        scale = background_display_width / natural_width
        # ddddocr 返回的是裁剪后模板在背景中的匹配位置，需要扣除模板原图左边透明区偏移。
        return max(int(round((self.target_left - self.target_x) * scale)), 0)


@dataclass(frozen=True)
class CaptchaDomState:
    slider_center_x: float
    slider_center_y: float
    slider_width: float
    slider_height: float
    slider_body_left: float
    slider_body_width: float
    slider_travel: int
    slider_max_travel: int
    background_display_width: float
    background_natural_width: int
    shadow_offset: int
    shadow_width: float
    shadow_transform_x: int


@dataclass(frozen=True)
class DragAttemptResult:
    slider_distance: int
    final_shadow_offset: int
    target_shadow_offset: int
    drag_elapsed_ms: int
    movement_count: int


def _get_ocr_detector() -> ddddocr.DdddOcr:
    global _OCR_DETECTOR
    if _OCR_DETECTOR is None:
        _OCR_DETECTOR = ddddocr.DdddOcr(det=False, ocr=False)
    return _OCR_DETECTOR


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


def calculate_shadow_match(shadow_url: str, background_url: str) -> CaptchaImageMatch:
    shadow_bytes = fetch_image_bytes(shadow_url)
    background_bytes = fetch_image_bytes(background_url)
    detector = _get_ocr_detector()
    result = detector.slide_match(shadow_bytes, background_bytes)
    target = result.get("target")
    if not isinstance(target, list) or len(target) < 4:
        raise RuntimeError(f"ddddocr 返回结果异常: {result}")
    return CaptchaImageMatch(
        target_left=int(target[0]),
        target_top=int(target[1]),
        target_right=int(target[2]),
        target_bottom=int(target[3]),
        target_x=int(result.get("target_x") or 0),
        target_y=int(result.get("target_y") or 0),
    )


class SliderVerificationFailedError(RuntimeError):
    pass


class SliderCaptchaSolver:
    def __init__(
        self,
        page: Page,
        logger,
        distance_calculator: Optional[AuthMatchCalculator] = None,
        event_sink: Optional[StructuredEventSink] = None,
        sample_dir: Optional[Path] = None,
    ) -> None:
        self.page = page
        self.logger = logger
        self.distance_calculator = distance_calculator or calculate_shadow_match
        self.event_sink = event_sink
        self.sample_dir = Path(sample_dir) if sample_dir is not None else None

    def _emit_event(self, event: str, **fields: object) -> None:
        if self.event_sink is None:
            return
        payload: dict[str, object] = {"event": event}
        payload.update(fields)
        self.event_sink(payload)

    def _infer_image_extension(self, image_source: str) -> str:
        if image_source.startswith(DATA_URL_PREFIX):
            header = image_source.split(",", maxsplit=1)[0].lower()
            if "image/png" in header:
                return ".png"
            if "image/jpeg" in header:
                return ".jpg"
            if "image/webp" in header:
                return ".webp"
            if "image/gif" in header:
                return ".gif"
        suffix = Path(urlparse(image_source).path).suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
            return suffix
        return ".bin"

    def _capture_image_artifact(
        self,
        image_source: str,
        *,
        internal_attempt: int,
        label: str,
    ) -> dict[str, object]:
        image_bytes = fetch_image_bytes(image_source)
        sha256 = hashlib.sha256(image_bytes).hexdigest()
        saved_path: Optional[str] = None
        if self.sample_dir is not None:
            self.sample_dir.mkdir(parents=True, exist_ok=True)
            file_path = self.sample_dir / (
                f"challenge_{internal_attempt:02d}_{label}{self._infer_image_extension(image_source)}"
            )
            file_path.write_bytes(image_bytes)
            saved_path = str(file_path)
        return {
            "label": label,
            "path": saved_path,
            "sha256": sha256,
            "bytes": len(image_bytes),
            "source_kind": "data_url" if image_source.startswith(DATA_URL_PREFIX) else "remote_url",
        }

    def _capture_challenge_artifacts(
        self,
        shadow_url: str,
        background_url: str,
        *,
        internal_attempt: int,
    ) -> None:
        background = self._capture_image_artifact(
            background_url,
            internal_attempt=internal_attempt,
            label="background",
        )
        shadow = self._capture_image_artifact(
            shadow_url,
            internal_attempt=internal_attempt,
            label="shadow",
        )
        self._emit_event(
            "captcha_images_captured",
            internal_attempt=internal_attempt,
            background=background,
            shadow=shadow,
        )

    def ensure_popup_visible(self) -> None:
        popup = self.page.locator(CAPTCHA_POPUP_SELECTOR).first
        try:
            popup.wait_for(state="visible", timeout=CAPTCHA_POPUP_TIMEOUT_MS)
        except Exception:
            expect(self.page.get_by_text(CAPTCHA_HINT_TEXT)).to_be_visible(
                timeout=CAPTCHA_TEXT_TIMEOUT_MS
            )
        self.logger.info("验证码浮层已出现")
        self._emit_event("captcha_popup_visible")

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

    def _refresh_captcha_challenge(self) -> None:
        popup = self.page.locator(CAPTCHA_POPUP_SELECTOR).first
        refresh = popup.locator(CAPTCHA_REFRESH_SELECTOR).first
        try:
            if not refresh.is_visible(timeout=800):
                return
            refresh.click(timeout=ELEMENT_ACTION_TIMEOUT_MS)
            self.logger.info("已主动刷新验证码题目")
            self._emit_event("captcha_refreshed")
            self.page.wait_for_timeout(CAPTCHA_REFRESH_WAIT_MS)
        except Exception as exc:
            self.logger.warning("主动刷新验证码失败，继续沿用当前题目: %s", exc)
            self._emit_event("captcha_refresh_failed", error=str(exc))

    def _read_captcha_state(self) -> CaptchaDomState:
        state_raw = self.page.evaluate(
            READ_CAPTCHA_STATE_SCRIPT,
            {
                "popupSelector": CAPTCHA_POPUP_SELECTOR,
                "sliderSelectors": CAPTCHA_SLIDER_SELECTORS,
                "sliderBodySelectors": CAPTCHA_SLIDER_BODY_SELECTORS,
                "shadowSelectors": CAPTCHA_SHADOW_SELECTORS,
                "backgroundSelectors": CAPTCHA_BACKGROUND_SELECTORS,
            },
        )
        if not isinstance(state_raw, dict):
            raise RuntimeError(f"验证码状态返回格式错误: {state_raw}")
        return CaptchaDomState(
            slider_center_x=float(state_raw["sliderCenterX"]),
            slider_center_y=float(state_raw["sliderCenterY"]),
            slider_width=float(state_raw["sliderWidth"]),
            slider_height=float(state_raw["sliderHeight"]),
            slider_body_left=float(state_raw["sliderBodyLeft"]),
            slider_body_width=float(state_raw["sliderBodyWidth"]),
            slider_travel=int(state_raw["sliderTravel"]),
            slider_max_travel=int(state_raw["sliderMaxTravel"]),
            background_display_width=float(state_raw["backgroundDisplayWidth"]),
            background_natural_width=int(state_raw["backgroundNaturalWidth"]),
            shadow_offset=int(state_raw["shadowOffset"]),
            shadow_width=float(state_raw["shadowWidth"]),
            shadow_transform_x=int(state_raw["shadowTransformX"]),
        )

    def _calculate_target_shadow_offset(
        self,
        image_match: CaptchaImageMatch,
        state: CaptchaDomState,
    ) -> int:
        raw_display_left = int(
            round(
                image_match.target_left
                * (
                    state.background_display_width
                    / (
                        state.background_natural_width
                        if state.background_natural_width > 0
                        else state.background_display_width
                    )
                )
            )
        )
        adjusted_display_left = image_match.to_display_offset(
            background_display_width=state.background_display_width,
            background_natural_width=state.background_natural_width,
        )
        target_shadow_offset = max(0, min(adjusted_display_left, state.slider_max_travel))
        self.logger.info(
            "OCR 匹配结果: raw_left=%spx, target_x=%spx, scaled_raw=%spx, scaled_adjusted=%spx, target_shadow=%spx",
            image_match.target_left,
            image_match.target_x,
            raw_display_left,
            adjusted_display_left,
            target_shadow_offset,
        )
        self._emit_event(
            "ocr_match_computed",
            raw_left=image_match.target_left,
            raw_top=image_match.target_top,
            raw_right=image_match.target_right,
            raw_bottom=image_match.target_bottom,
            target_x=image_match.target_x,
            target_y=image_match.target_y,
            scaled_raw=raw_display_left,
            scaled_adjusted=adjusted_display_left,
            target_shadow_offset=target_shadow_offset,
            background_display_width=state.background_display_width,
            background_natural_width=state.background_natural_width,
            slider_max_travel=state.slider_max_travel,
        )
        return target_shadow_offset

    def _wait_random(self, min_ms: int, max_ms: int) -> None:
        self.page.wait_for_timeout(int(random.uniform(min_ms, max_ms)))

    def _move_and_sample_state(
        self,
        *,
        x: float,
        y: float,
        wait_min_ms: int,
        wait_max_ms: int,
        previous_shadow_offset: Optional[int] = None,
        poll_rounds: int = HUMAN_DRAG_STATE_POLL_ROUNDS,
    ) -> CaptchaDomState:
        self.page.mouse.move(x, y)
        self._wait_random(wait_min_ms, wait_max_ms)
        state = self._read_captcha_state()
        if previous_shadow_offset is None:
            return state
        for _ in range(max(poll_rounds - 1, 0)):
            if state.shadow_offset != previous_shadow_offset:
                break
            self._wait_random(HUMAN_DRAG_STATE_POLL_WAIT_MIN_MS, HUMAN_DRAG_STATE_POLL_WAIT_MAX_MS)
            state = self._read_captcha_state()
        return state

    def _clamp_target_bias(self, bias: int) -> int:
        return max(TARGET_SHADOW_MIN_BIAS_PX, min(TARGET_SHADOW_MAX_BIAS_PX, bias))

    def _drag_slider_live(
        self,
        target_shadow_offset: int,
        start_state: CaptchaDomState,
    ) -> DragAttemptResult:
        start_x = start_state.slider_center_x
        start_y = start_state.slider_center_y
        current_x = start_x
        current_state = start_state
        track_end_x = start_x + start_state.slider_max_travel
        last_shadow_offset = current_state.shadow_offset
        movement_count = 0
        drag_started = time.perf_counter()

        self.page.mouse.move(start_x, start_y)
        self._wait_random(HUMAN_DRAG_HOVER_MIN_MS, HUMAN_DRAG_HOVER_MAX_MS)
        self.page.mouse.down()
        try:
            self._wait_random(HUMAN_DRAG_HOLD_MIN_MS, HUMAN_DRAG_HOLD_MAX_MS)
            for _ in range(HUMAN_DRAG_MAX_COARSE_STEPS):
                remaining = target_shadow_offset - current_state.shadow_offset
                if remaining <= HUMAN_DRAG_COARSE_WINDOW_PX:
                    break
                base_step = int(round(remaining / 4))
                step_px = max(HUMAN_DRAG_MIN_STEP_PX, min(base_step, HUMAN_DRAG_MAX_STEP_PX))
                if current_state.shadow_offset <= last_shadow_offset and remaining > HUMAN_DRAG_COARSE_WINDOW_PX + 4:
                    step_px = min(HUMAN_DRAG_MAX_STEP_PX, step_px + 2)
                next_x = min(track_end_x, current_x + step_px + random.uniform(0.2, 0.8))
                if next_x <= current_x:
                    next_x = min(track_end_x, current_x + HUMAN_DRAG_MIN_STEP_PX)
                current_x = next_x
                current_y = start_y + random.uniform(-HUMAN_DRAG_NOISE_Y, HUMAN_DRAG_NOISE_Y)
                current_state = self._move_and_sample_state(
                    x=current_x,
                    y=current_y,
                    wait_min_ms=HUMAN_DRAG_STEP_WAIT_MIN_MS,
                    wait_max_ms=HUMAN_DRAG_STEP_WAIT_MAX_MS,
                    previous_shadow_offset=last_shadow_offset,
                )
                movement_count += 1
                last_shadow_offset = current_state.shadow_offset
                if (
                    current_state.shadow_offset >= current_state.slider_max_travel
                    and target_shadow_offset < current_state.slider_max_travel - HUMAN_DRAG_COARSE_WINDOW_PX
                ):
                    break
                if current_x >= track_end_x:
                    break

            for _ in range(HUMAN_DRAG_MAX_FINE_STEPS):
                delta = target_shadow_offset - current_state.shadow_offset
                if abs(delta) <= HUMAN_DRAG_TARGET_TOLERANCE_PX:
                    break
                step_px = max(
                    HUMAN_DRAG_MIN_FINE_STEP_PX,
                    min(abs(delta), HUMAN_DRAG_MAX_FINE_STEP_PX),
                )
                direction = 1 if delta > 0 else -1
                next_x = max(start_x, min(track_end_x, current_x + (direction * step_px)))
                if next_x == current_x:
                    break
                current_x = next_x
                current_y = start_y + random.uniform(-HUMAN_DRAG_NOISE_Y / 2, HUMAN_DRAG_NOISE_Y / 2)
                current_state = self._move_and_sample_state(
                    x=current_x,
                    y=current_y,
                    wait_min_ms=HUMAN_DRAG_FINE_WAIT_MIN_MS,
                    wait_max_ms=HUMAN_DRAG_FINE_WAIT_MAX_MS,
                    previous_shadow_offset=current_state.shadow_offset,
                )
                movement_count += 1
            self._wait_random(HUMAN_DRAG_RELEASE_MIN_MS, HUMAN_DRAG_RELEASE_MAX_MS)
            min_drag_ms = (
                HUMAN_DRAG_MIN_TOTAL_SHORT_MS
                if target_shadow_offset < 120
                else HUMAN_DRAG_MIN_TOTAL_MS
            )
            elapsed_before_release_ms = int((time.perf_counter() - drag_started) * 1000)
            if elapsed_before_release_ms < min_drag_ms:
                self.page.wait_for_timeout(min_drag_ms - elapsed_before_release_ms)
        finally:
            self.page.mouse.up()

        drag_elapsed_ms = int((time.perf_counter() - drag_started) * 1000)
        dragged_distance = max(int(round(current_state.slider_travel)), 0)
        cursor_distance = max(int(round(current_x - start_x)), 0)
        self.logger.info(
            "已执行真实拖动: slider=%spx, cursor=%spx, shadow=%spx, target_shadow=%spx, drag_elapsed=%sms, moves=%s",
            dragged_distance,
            cursor_distance,
            current_state.shadow_offset,
            target_shadow_offset,
            drag_elapsed_ms,
            movement_count,
        )
        self._emit_event(
            "drag_completed",
            slider_distance=dragged_distance,
            cursor_distance=cursor_distance,
            final_slider_travel=current_state.slider_travel,
            final_shadow_offset=current_state.shadow_offset,
            target_shadow_offset=target_shadow_offset,
            shadow_residual=(target_shadow_offset - current_state.shadow_offset),
            drag_elapsed_ms=drag_elapsed_ms,
            movement_count=movement_count,
        )
        return DragAttemptResult(
            slider_distance=dragged_distance,
            final_shadow_offset=current_state.shadow_offset,
            target_shadow_offset=target_shadow_offset,
            drag_elapsed_ms=drag_elapsed_ms,
            movement_count=movement_count,
        )

    def _wait_slider_result(self) -> None:
        popup = self.page.locator(CAPTCHA_POPUP_SELECTOR).first
        result = self.page.locator(SLIDER_RESULT_SELECTOR).first
        deadline = time.time() + (SLIDER_RESULT_WAIT_MS / 1000)
        while time.time() < deadline:
            try:
                if not popup.is_visible(timeout=300):
                    self.logger.info("滑块验证结果: 验证浮层已关闭，判定成功")
                    self._emit_event("slider_result_success", class_name="popup_closed", text="")
                    return
            except Exception:
                self.logger.info("滑块验证结果: 验证浮层不可见，判定成功")
                self._emit_event("slider_result_success", class_name="popup_hidden", text="")
                return
            try:
                class_name = str(result.get_attribute("class", timeout=300) or "").lower()
                text = str(result.inner_text(timeout=300) or "").strip()
            except Exception:
                self.page.wait_for_timeout(SLIDER_RESULT_POLL_MS)
                continue
            if SLIDER_FAIL_CLASS in class_name:
                self._emit_event("slider_result_failed", class_name=class_name, text=text)
                raise SliderVerificationFailedError(
                    f"滑块验证失败: class={class_name}, text={text}"
                )
            if SLIDER_SUCCESS_CLASS in class_name or any(item in text for item in SLIDER_SUCCESS_TEXT_KEYWORDS):
                self.logger.info("滑块验证结果: class=%s, text=%s", class_name, text)
                self._emit_event("slider_result_success", class_name=class_name, text=text)
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
            target_bias = TARGET_SHADOW_BASE_BIAS_PX
            last_failure: Optional[SliderVerificationFailedError] = None
            for attempt in range(1, SOLVER_MAX_INTERNAL_ATTEMPTS + 1):
                if attempt > 1:
                    self._refresh_captcha_challenge()
                self._wait_slider_ready()
                shadow_url, background_url = self._extract_captcha_image_urls()
                self.logger.info("验证码图片 URL 已提取")
                self._emit_event("captcha_images_extracted", internal_attempt=attempt)
                self._capture_challenge_artifacts(
                    shadow_url,
                    background_url,
                    internal_attempt=attempt,
                )
                image_match = self.distance_calculator(shadow_url, background_url)
                state = self._read_captcha_state()
                base_target_shadow_offset = self._calculate_target_shadow_offset(image_match, state)
                target_shadow_offset = max(
                    0,
                    min(state.slider_max_travel, base_target_shadow_offset + target_bias),
                )
                self.logger.info(
                    "第%s次内部滑块尝试: base_target=%spx, bias=%+spx, final_target=%spx",
                    attempt,
                    base_target_shadow_offset,
                    target_bias,
                    target_shadow_offset,
                )
                self._emit_event(
                    "internal_attempt_started",
                    internal_attempt=attempt,
                    base_target_shadow_offset=base_target_shadow_offset,
                    target_bias=target_bias,
                    target_shadow_offset=target_shadow_offset,
                    start_shadow_offset=state.shadow_offset,
                    slider_max_travel=state.slider_max_travel,
                    slider_travel=state.slider_travel,
                )
                drag_result = self._drag_slider_live(target_shadow_offset, state)
                try:
                    self._wait_slider_result()
                    self._emit_event(
                        "internal_attempt_succeeded",
                        internal_attempt=attempt,
                        slider_distance=drag_result.slider_distance,
                        final_shadow_offset=drag_result.final_shadow_offset,
                        target_shadow_offset=drag_result.target_shadow_offset,
                        drag_elapsed_ms=drag_result.drag_elapsed_ms,
                        movement_count=drag_result.movement_count,
                    )
                    self._emit_event(
                        "solver_succeeded",
                        internal_attempt=attempt,
                        slider_distance=drag_result.slider_distance,
                        drag_elapsed_ms=drag_result.drag_elapsed_ms,
                    )
                    return drag_result.slider_distance
                except SliderVerificationFailedError as exc:
                    last_failure = exc
                    residual = target_shadow_offset - drag_result.final_shadow_offset
                    target_bias = self._clamp_target_bias(target_bias + residual)
                    self._emit_event(
                        "internal_attempt_failed",
                        internal_attempt=attempt,
                        error=str(exc),
                        slider_distance=drag_result.slider_distance,
                        final_shadow_offset=drag_result.final_shadow_offset,
                        target_shadow_offset=drag_result.target_shadow_offset,
                        residual=residual,
                        next_target_bias=target_bias,
                        drag_elapsed_ms=drag_result.drag_elapsed_ms,
                        movement_count=drag_result.movement_count,
                    )
                    if attempt >= SOLVER_MAX_INTERNAL_ATTEMPTS:
                        self._emit_event(
                            "solver_failed",
                            internal_attempt=attempt,
                            error=str(exc),
                        )
                        raise
                    self.logger.warning(
                        "第%s次内部滑块尝试失败，残差=%+spx，下一次偏差补偿调整为 %+spx",
                        attempt,
                        residual,
                        target_bias,
                    )
                    self.page.wait_for_timeout(SOLVER_RETRY_WAIT_MS)
            if last_failure is not None:
                raise last_failure
            raise RuntimeError("滑块求解未执行")
        except SliderVerificationFailedError:
            raise
        except Exception as exc:
            self._raise_retryable_failure(exc)
            raise RuntimeError("unreachable")
