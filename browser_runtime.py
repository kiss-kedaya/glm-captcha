"""Playwright 浏览器启动与轻量反检测配置。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Browser, BrowserContext, Playwright

ANTI_DETECTION_ARGS = [
    "--disable-blink-features=AutomationControlled",
]
IGNORE_DEFAULT_ARGS = ["--enable-automation"]
PREFERRED_CHANNELS: tuple[Optional[str], ...] = ("msedge", "chrome", None)
ACCEPT_LANGUAGE_HEADER = "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7"
STEALTH_INIT_SCRIPT = """
(() => {
  const defineGetter = (target, key, getter) => {
    try {
      Object.defineProperty(target, key, {
        configurable: true,
        get: getter,
      });
    } catch (error) {
      void error;
    }
  };

  defineGetter(Navigator.prototype, "webdriver", () => undefined);
  defineGetter(Navigator.prototype, "language", () => "zh-CN");
  defineGetter(Navigator.prototype, "languages", () => ["zh-CN", "zh", "en-US"]);
  defineGetter(Navigator.prototype, "platform", () => "Win32");

  try {
    if (!window.chrome) {
      Object.defineProperty(window, "chrome", {
        configurable: true,
        value: {},
      });
    }
    if (!window.chrome.runtime) {
      window.chrome.runtime = {};
    }
  } catch (error) {
    void error;
  }

  const permissions = window.navigator.permissions;
  if (permissions && typeof permissions.query === "function") {
    const originalQuery = permissions.query.bind(permissions);
    permissions.query = (parameters) => {
      if (parameters && parameters.name === "notifications") {
        return Promise.resolve({ state: Notification.permission });
      }
      return originalQuery(parameters);
    };
  }
})();
"""


@dataclass(frozen=True)
class BrowserLaunchResult:
    browser: Browser
    context: BrowserContext
    channel: str


def launch_browser_context(
    playwright: Playwright,
    *,
    headless: bool,
    locale: str,
    viewport_width: int,
    viewport_height: int,
    logger=None,
) -> BrowserLaunchResult:
    launch_errors: list[str] = []
    browser: Optional[Browser] = None
    channel_label = "chromium"

    for channel in PREFERRED_CHANNELS:
        launch_kwargs = {
            "headless": headless,
            "args": ANTI_DETECTION_ARGS,
            "ignore_default_args": IGNORE_DEFAULT_ARGS,
        }
        if channel is not None:
            launch_kwargs["channel"] = channel
        try:
            browser = playwright.chromium.launch(**launch_kwargs)
        except Exception as exc:
            launch_errors.append(f"{channel or 'chromium'}: {exc}")
            continue
        channel_label = channel or "chromium"
        break

    if browser is None:
        error_message = "; ".join(launch_errors) or "无可用浏览器通道"
        raise RuntimeError(f"浏览器启动失败: {error_message}")

    context = browser.new_context(
        locale=locale,
        viewport={"width": viewport_width, "height": viewport_height},
        color_scheme="light",
        timezone_id="Asia/Shanghai",
        extra_http_headers={"Accept-Language": ACCEPT_LANGUAGE_HEADER},
    )
    context.add_init_script(STEALTH_INIT_SCRIPT)
    if logger is not None:
        logger.info("浏览器启动通道: %s", channel_label)
    return BrowserLaunchResult(browser=browser, context=context, channel=channel_label)
