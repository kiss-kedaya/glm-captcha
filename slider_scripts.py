"""滑块验证码脚本常量。"""

READ_CAPTCHA_STATE_SCRIPT = """
({ popupSelector, sliderSelectors, sliderBodySelectors, shadowSelectors, backgroundSelectors }) => {
  const popup = document.querySelector(popupSelector);
  if (!popup) throw new Error("未找到验证码浮层容器");
  const findElement = (selectors) => {
    for (const selector of selectors) {
      const element = popup.querySelector(selector) || document.querySelector(selector);
      if (element) return element;
    }
    return null;
  };
  const slider = findElement(sliderSelectors);
  const sliderBody = findElement(sliderBodySelectors);
  const shadow = findElement(shadowSelectors);
  const background = findElement(backgroundSelectors);
  if (!slider) throw new Error("未找到滑块元素");
  if (!shadow) throw new Error("未找到拼图阴影元素");
  if (!background) throw new Error("未找到背景图元素");
  const sliderRect = slider.getBoundingClientRect();
  const bodyRect = (sliderBody || background).getBoundingClientRect();
  const shadowRect = shadow.getBoundingClientRect();
  const backgroundRect = background.getBoundingClientRect();
  if (!sliderRect.width || !sliderRect.height) throw new Error("滑块元素不可见");
  if (!bodyRect.width || !backgroundRect.width) throw new Error("验证码轨道不可见");
  const readTransformX = (element) => {
    const transform = getComputedStyle(element).transform;
    if (!transform || transform === "none" || typeof DOMMatrixReadOnly === "undefined") {
      return 0;
    }
    return new DOMMatrixReadOnly(transform).m41;
  };
  const readShadowOffset = () => {
    const rectOffset = shadowRect.left - backgroundRect.left;
    if (Number.isFinite(rectOffset)) return Math.round(rectOffset);
    const styleLeft = Number.parseFloat(shadow.style.left || "0");
    if (!Number.isNaN(styleLeft)) return Math.round(styleLeft);
    const computedLeft = Number.parseFloat(getComputedStyle(shadow).left || "0");
    if (!Number.isNaN(computedLeft)) return Math.round(computedLeft);
    return Math.round(readTransformX(shadow));
  };
  return {
    sliderCenterX: sliderRect.left + sliderRect.width / 2,
    sliderCenterY: sliderRect.top + sliderRect.height / 2,
    sliderWidth: sliderRect.width,
    sliderHeight: sliderRect.height,
    sliderBodyLeft: bodyRect.left,
    sliderBodyWidth: bodyRect.width,
    sliderTravel: Math.round(sliderRect.left - bodyRect.left),
    sliderMaxTravel: Math.max(Math.round(bodyRect.width - sliderRect.width), 0),
    backgroundDisplayWidth: backgroundRect.width,
    backgroundNaturalWidth: Number(background.naturalWidth || 0),
    shadowOffset: readShadowOffset(),
    shadowWidth: shadowRect.width,
    shadowTransformX: Math.round(readTransformX(shadow)),
  };
}
"""
