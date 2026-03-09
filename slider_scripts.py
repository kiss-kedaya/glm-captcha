"""滑块验证码脚本常量。"""

BUILD_MAPPING_SCRIPT = """
async ({ popupSelector, sliderSelectors, shadowSelectors, dragDistance, forwardMs, backwardMs }) => {
  const popup = document.querySelector(popupSelector);
  if (!popup) throw new Error("未找到验证码浮层容器");
  const findElementInPopup = (selectors) => {
    for (const selector of selectors) {
      const element = popup.querySelector(selector);
      if (element) return element;
    }
    return null;
  };
  const slider = findElementInPopup(sliderSelectors);
  const shadow = findElementInPopup(shadowSelectors);
  if (!slider) throw new Error("未找到滑块元素");
  if (!shadow) throw new Error("未找到拼图阴影元素");
  const rect = slider.getBoundingClientRect();
  if (!rect.width || !rect.height) throw new Error("滑块元素不可见");
  const mapping = {};
  const startX = rect.left + rect.width / 2;
  const startY = rect.top + rect.height / 2;
  const readShadowOffset = () => {
    const styleLeft = Number.parseFloat(shadow.style.left || "0");
    if (!Number.isNaN(styleLeft) && styleLeft !== 0) return Math.round(styleLeft);
    const computedLeft = Number.parseFloat(getComputedStyle(shadow).left || "0");
    if (!Number.isNaN(computedLeft) && computedLeft !== 0) return Math.round(computedLeft);
    const transform = getComputedStyle(shadow).transform;
    if (transform && transform !== "none" && typeof DOMMatrixReadOnly !== "undefined") {
      return Math.round(new DOMMatrixReadOnly(transform).m41);
    }
    return 0;
  };
  const dispatchMove = (x, y) => {
    slider.dispatchEvent(new MouseEvent("mousemove", {
      bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0
    }));
  };
  const animate = (distance, duration, shouldSample) => new Promise((resolve) => {
    const fromTime = performance.now();
    const tick = (currentTime) => {
      const progress = Math.min((currentTime - fromTime) / duration, 1);
      const currentX = startX + distance * progress;
      dispatchMove(currentX, startY);
      if (shouldSample) {
        const sliderMove = Math.round(dragDistance * progress);
        const shadowMove = readShadowOffset();
        if (sliderMove > 0 && mapping[sliderMove] === undefined) mapping[sliderMove] = shadowMove;
      }
      if (progress < 1) {
        requestAnimationFrame(tick);
        return;
      }
      resolve();
    };
    requestAnimationFrame(tick);
  });
  slider.dispatchEvent(new MouseEvent("mousedown", {
    bubbles: true, cancelable: true, clientX: startX, clientY: startY, button: 0
  }));
  await animate(dragDistance, forwardMs, true);
  await animate(-dragDistance, backwardMs, false);
  slider.dispatchEvent(new MouseEvent("mouseup", {
    bubbles: true, cancelable: true, clientX: startX, clientY: startY, button: 0
  }));
  return mapping;
}
"""

HUMAN_DRAG_SCRIPT = """
async ({ popupSelector, sliderSelectors, dragDistance, dragDuration, noiseX, noiseY }) => {
  const popup = document.querySelector(popupSelector);
  if (!popup) throw new Error("未找到验证码浮层容器");
  const findElementInPopup = (selectors) => {
    for (const selector of selectors) {
      const element = popup.querySelector(selector);
      if (element) return element;
    }
    return null;
  };
  const slider = findElementInPopup(sliderSelectors);
  if (!slider) throw new Error("未找到滑块元素");
  const rect = slider.getBoundingClientRect();
  if (!rect.width || !rect.height) throw new Error("滑块元素不可见");
  const easeInOutQuad = (t) => (t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t);
  const startX = rect.left + rect.width / 2;
  const startY = rect.top + rect.height / 2;
  slider.dispatchEvent(new MouseEvent("mousedown", {
    bubbles: true, cancelable: true, clientX: startX, clientY: startY, button: 0
  }));
  await new Promise((resolve) => {
    const fromTime = performance.now();
    const tick = (currentTime) => {
      const progress = Math.min((currentTime - fromTime) / dragDuration, 1);
      const eased = easeInOutQuad(progress);
      const randomX = (Math.random() - 0.5) * noiseX;
      const randomY = (Math.random() - 0.5) * noiseY;
      const currentX = startX + dragDistance * eased + randomX;
      const currentY = startY + randomY;
      slider.dispatchEvent(new MouseEvent("mousemove", {
        bubbles: true, cancelable: true, clientX: currentX, clientY: currentY, button: 0
      }));
      if (progress < 1) {
        requestAnimationFrame(tick);
        return;
      }
      resolve();
    };
    requestAnimationFrame(tick);
  });
  const finalX = startX + dragDistance + Math.random() * 0.2 + 0.1;
  const finalY = startY + (Math.random() - 0.5) * noiseY;
  slider.dispatchEvent(new MouseEvent("mouseup", {
    bubbles: true, cancelable: true, clientX: finalX, clientY: finalY, button: 0
  }));
  return { finalX, finalY };
}
"""
