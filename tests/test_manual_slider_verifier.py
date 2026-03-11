"""手动滑块模式回归测试。"""
from __future__ import annotations

import unittest

from slider_captcha_solver import SliderVerificationFailedError
from slider_verifier import MANUAL_POPUP_CLOSED_ERROR, ManualSliderVerifier

POPUP_SELECTOR = "#aliyunCaptcha-window-float"
RESULT_SELECTOR = "#aliyunCaptcha-sliding-text"
SLIDER_SELECTOR = "#aliyunCaptcha-sliding-slider"
CAPTCHA_TEXT = "请完成安全验证"


class FakeLogger:
    def info(self, message: str, *args) -> None:
        if args:
            message % args

    def warning(self, message: str, *args) -> None:
        if args:
            message % args


class FakeTextLocator:
    def to_be_visible(self, timeout: int) -> None:
        if timeout < 0:
            raise AssertionError("timeout must be non-negative")


class FakeResultLocator:
    def __init__(self, page: "FakePage") -> None:
        self.page = page
        self.first = self

    def get_attribute(self, name: str, timeout: int = 0) -> str:
        if name != "class":
            raise AssertionError(f"unexpected attribute: {name}")
        if timeout < 0:
            raise AssertionError("timeout must be non-negative")
        return str(self.page.state["class_name"])

    def inner_text(self, timeout: int = 0) -> str:
        if timeout < 0:
            raise AssertionError("timeout must be non-negative")
        return str(self.page.state["text"])


class FakeSliderLocator:
    def __init__(self, page: "FakePage") -> None:
        self.page = page
        self.first = self

    def is_visible(self, timeout: int = 0) -> bool:
        if timeout < 0:
            raise AssertionError("timeout must be non-negative")
        return bool(self.page.state["slider_visible"])


class FakePopupLocator:
    def __init__(self, page: "FakePage") -> None:
        self.page = page
        self.first = self

    def wait_for(self, state: str, timeout: int = 0) -> None:
        if state != "visible":
            raise AssertionError(f"unexpected state: {state}")
        if timeout < 0:
            raise AssertionError("timeout must be non-negative")
        if not self.page.state["popup_visible"]:
            raise RuntimeError("popup not visible")

    def is_visible(self, timeout: int = 0) -> bool:
        if timeout < 0:
            raise AssertionError("timeout must be non-negative")
        return bool(self.page.state["popup_visible"])

    def locator(self, selector: str) -> FakeSliderLocator:
        if selector != SLIDER_SELECTOR:
            raise AssertionError(f"unexpected selector: {selector}")
        return FakeSliderLocator(self.page)


class FakePage:
    def __init__(self, states: list[dict[str, object]]) -> None:
        self._states = states
        self._index = 0

    @property
    def state(self) -> dict[str, object]:
        return self._states[self._index]

    def locator(self, selector: str):
        if selector == POPUP_SELECTOR:
            return FakePopupLocator(self)
        if selector == RESULT_SELECTOR:
            return FakeResultLocator(self)
        raise AssertionError(f"unexpected selector: {selector}")

    def get_by_text(self, text: str) -> FakeTextLocator:
        if text != CAPTCHA_TEXT:
            raise AssertionError(f"unexpected text: {text}")
        return FakeTextLocator()

    def evaluate(self, script: str, payload: dict[str, object]) -> dict[str, object]:
        if not script or not payload:
            raise AssertionError("evaluate should receive script and payload")
        return {"sliderTravel": self.state["slider_travel"]}

    def wait_for_timeout(self, timeout_ms: int) -> None:
        if timeout_ms < 0:
            raise AssertionError("timeout must be non-negative")
        if self._index < len(self._states) - 1:
            self._index += 1


class ManualSliderVerifierTests(unittest.TestCase):
    def test_popup_hidden_without_success_raises_failure(self) -> None:
        page = FakePage(
            [
                {"popup_visible": True, "slider_visible": True, "class_name": "", "text": "", "slider_travel": 0},
                {"popup_visible": False, "slider_visible": False, "class_name": "", "text": "", "slider_travel": 0},
            ]
        )
        verifier = ManualSliderVerifier(page, FakeLogger(), timeout_ms=1_000)
        with self.assertRaisesRegex(SliderVerificationFailedError, MANUAL_POPUP_CLOSED_ERROR):
            verifier.solve()

    def test_popup_hidden_after_drag_returns_success(self) -> None:
        page = FakePage(
            [
                {"popup_visible": True, "slider_visible": True, "class_name": "", "text": "", "slider_travel": 18},
                {"popup_visible": False, "slider_visible": False, "class_name": "", "text": "", "slider_travel": 18},
            ]
        )
        verifier = ManualSliderVerifier(page, FakeLogger(), timeout_ms=1_000)
        self.assertEqual(verifier.solve(), 18)

    def test_success_returns_latest_slider_distance(self) -> None:
        page = FakePage(
            [
                {"popup_visible": True, "slider_visible": True, "class_name": "", "text": "", "slider_travel": 12},
                {"popup_visible": True, "slider_visible": True, "class_name": "success", "text": "验证通过", "slider_travel": 48},
            ]
        )
        verifier = ManualSliderVerifier(page, FakeLogger(), timeout_ms=1_000)
        self.assertEqual(verifier.solve(), 48)


if __name__ == "__main__":
    unittest.main()
