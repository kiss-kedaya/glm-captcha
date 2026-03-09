# 阿里云滑动验证码架构分析

本文档聚焦项目当前正在使用的滑块验证码架构、模块职责和运行链路。

## 1. 架构总览

当前项目围绕两个核心目标组织：

1. 自动完成 `https://chat.z.ai/auth` 注册流程
2. 独立验证滑块验证码链路，便于分析识别、拖动和风控行为

系统由以下几个主要模块组成：

| 模块 | 文件 | 职责 |
|------|------|------|
| 浏览器运行时 | `browser_runtime.py` | 启动浏览器、选择通道、注入轻量 stealth、设置上下文 |
| 页面流程控制 | `page_flow.py` | 打开认证页、切换注册、填写表单、触发验证码、提交注册 |
| 滑块求解器 | `slider_captcha_solver.py` | 提图、OCR、偏移换算、真实鼠标闭环拖动、结果判定、内部重试 |
| 页面状态脚本 | `slider_scripts.py` | 从验证码浮层读取滑块、阴影、背景图等实时状态 |
| 邮箱客户端 | `mail_provider_clients.py` | 创建临时邮箱账号 |
| 邮件轮询 | `mail_verification.py` | 轮询验证邮件并提取验证链接 |
| Token 提取 | `token_capture.py` | 注册完成后从 cookie / storage 中提取账号 token |
| 注册入口 | `main.py` | 单账号注册与批量并发注册调度 |
| 滑块压测入口 | `verify_slider.py` | 独立验证滑块链路、输出结构化日志和样本 |

## 2. 注册主链路

当前注册链路按以下顺序运行：

1. 启动浏览器和上下文
2. 打开认证页
3. 切换到注册表单
4. 生成临时邮箱、用户名和密码
5. 填写注册表单
6. 触发阿里云滑块验证码
7. 求解滑块验证码
8. 提交注册请求
9. 轮询邮箱中的验证链接
10. 打开验证链接并完成邮箱验证页流程
11. 从页面 cookie / storage 中提取账号 token
12. 将 token 保存到本地

对应入口文件：

- 单账号或批量注册：`main.py`
- 独立滑块验证：`verify_slider.py`

## 3. 浏览器运行时

浏览器运行时由 `browser_runtime.py` 统一管理。

### 通道选择

当前按以下优先级启动浏览器：

1. `msedge`
2. `chrome`
3. Playwright 自带 `chromium`

这样做的目的是尽量使用更接近真实用户环境的浏览器通道。

### 上下文设置

当前上下文配置包括：

- `locale = zh-CN`
- `timezone_id = Asia/Shanghai`
- `color_scheme = light`
- `Accept-Language = zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7`

### 轻量 stealth

启动后会注入轻量运行时修补，主要覆盖：

- `navigator.webdriver`
- `navigator.language`
- `navigator.languages`
- `navigator.platform`
- `window.chrome.runtime`
- `navigator.permissions.query`

这些修补用于降低最明显的自动化特征，但并不改变项目的核心逻辑，核心仍然依赖真实浏览器与真实鼠标行为。

## 4. 页面流程层

`page_flow.py` 负责注册页的 UI 流程控制。

### 页面打开

页面打开时采用：

- `domcontentloaded`
- 关键元素可见等待

而不是等待整页空闲，这样可以减少页面首屏等待时间。

### 注册表单切换

切换注册时，代码会：

1. 检测当前是否已经位于注册态
2. 优先定位页脚或主区域的“注册”按钮
3. 在短超时时间内快速确认是否进入注册表单

### 表单填写

表单填写当前只处理注册所需的关键字段：

- 名称
- 邮箱
- 密码

输入框通过一组选择器进行兼容匹配，确保页面文案或结构轻微变化时仍可工作。

### 注册提交反馈

提交后，页面流程层负责判断：

- 是否成功提交
- 是否出现“验证失败，请重试” toast
- 是否出现滑块失败状态

这为上层重试逻辑提供统一反馈接口。

## 5. 滑块验证码架构

滑块求解器位于 `slider_captcha_solver.py`，是当前项目的核心模块。

### 5.1 验证码浮层与关键元素

当前依赖的主要页面元素：

| 元素 | 选择器 | 作用 |
|------|--------|------|
| 验证码浮层 | `#aliyunCaptcha-window-float` | 验证码主容器 |
| 背景图 | `#aliyunCaptcha-img` | 带缺口的背景图 |
| 拼图阴影 | `#aliyunCaptcha-puzzle` | 需要对齐缺口的拼图 |
| 滑块按钮 | `#aliyunCaptcha-sliding-slider` | 实际拖动对象 |
| 滑块轨道 | `#aliyunCaptcha-sliding-body` | 滑块可移动轨道 |
| 刷新按钮 | `#aliyunCaptcha-btn-refresh` | 切换下一题 |
| 结果文本 | `#aliyunCaptcha-sliding-text` | 成功 / 失败反馈 |

### 5.2 图片提取

浮层出现后，求解器直接读取：

- 背景图 `src`
- 拼图图 `src`

两类来源都支持：

- `data:` URL
- 远程图片 URL

求解器会统一将它们转换为二进制数据，并在需要时保存为样本文件。

### 5.3 OCR 识别

当前使用 `ddddocr` 的 `slide_match` 来识别拼图在背景中的目标位置。

识别结果中最重要的字段是：

- `target`: 匹配框
- `target_x`: 模板左侧透明边偏移

### 5.4 目标偏移换算

OCR 原始结果不会直接当成最终拖动目标，而是经过以下换算：

1. 扣除拼图模板左侧透明边偏移 `target_x`
2. 根据背景图自然宽度与页面显示宽度换算比例
3. 将结果裁剪到当前轨道最大可拖动范围内

最终得到的值是：

- `target_shadow_offset`

它表示页面上拼图阴影应当移动到的目标偏移。

### 5.5 DOM 实时状态读取

`slider_scripts.py` 提供了验证码状态读取脚本。当前读取的核心字段包括：

- `sliderCenterX`
- `sliderCenterY`
- `sliderWidth`
- `sliderHeight`
- `sliderBodyLeft`
- `sliderBodyWidth`
- `sliderTravel`
- `sliderMaxTravel`
- `backgroundDisplayWidth`
- `backgroundNaturalWidth`
- `shadowOffset`
- `shadowWidth`
- `shadowTransformX`

这些状态使求解器可以基于页面实际反馈进行拖动控制，而不是只依赖本地推算。

### 5.6 真实鼠标闭环拖动

当前拖动策略是：

1. 先将鼠标移动到滑块中心
2. 按下鼠标
3. 进入粗调阶段快速接近目标
4. 进入精调阶段做小步修正
5. 满足最低总拖动时长后释放鼠标

拖动过程中，每一步都会重新读取页面中的 `shadowOffset`，并根据：

- 当前阴影偏移
- 目标阴影偏移
- 当前滑块行程

动态计算下一步应该继续前进多少。

### 5.7 拖动时间控制

当前求解器对拖动时间有明确约束：

- 常规题目有最低总拖动时长
- 短距离题目有更高的最低总拖动时长

这样做的目的是让真实拖动行为更接近正常用户操作节奏。

### 5.8 结果判定

拖动结束后，求解器不会仅依据本地偏移是否命中来判断成功，而是继续观察页面反馈。

当前成功判定条件包括：

1. 验证码浮层关闭
2. 结果文本 class 包含 `success`
3. 结果文本包含成功关键词

当前失败判定条件包括：

1. 结果文本 class 包含 `fail`
2. 文案出现“验证失败，请重试!”

## 6. 滑块重试机制

滑块求解器包含两层重试能力。

### 内部题目重试

单次验证码处理内部，默认最多尝试多轮题目：

1. 读取当前题目
2. OCR 识别
3. 拖动
4. 失败则主动刷新题目
5. 重新开始下一轮

### 偏差补偿

每轮失败后，求解器会记录：

- 目标阴影偏移
- 最终阴影偏移
- 残差 `residual`

下一轮会在目标偏移上应用小范围补偿，以修正题目间的轻微系统误差。

## 7. 邮箱与验证链路

邮箱链路由 `mail_provider_clients.py` 和 `mail_verification.py` 负责。

### 临时邮箱创建

当前支持两个提供商：

- `mailtm`
- `duckmail`

邮箱客户端会：

1. 拉取可用域名
2. 随机生成邮箱地址
3. 创建账号
4. 获取邮箱访问 token

### 验证邮件轮询

注册提交成功后，系统会轮询邮箱消息列表，按消息详情内容提取验证链接。

当前识别的验证链接模式为：

- `https://chat.z.ai/auth/verify_email?...`

当检测到链接后，页面流程层会直接打开它，继续执行邮箱验证页上的后续操作。

## 8. Token 提取链路

`token_capture.py` 负责在注册完成后提取账号 token。

当前提取来源包括：

- 浏览器上下文 cookie
- `document.cookie`
- `localStorage`
- `sessionStorage`

系统会从这些候选值中筛选出：

- 形态符合 JWT
- 来源优先级较高
- JWT claims 中邮箱与当前注册邮箱匹配

的 token，并保存到本地。

## 9. 批量并发架构

批量并发注册由 `main.py` 调度。

### 调度方式

当前实现支持：

- `--count`: 总账号数
- `--concurrency`: 并发任务数

调度层使用线程池并发启动多个独立注册任务。每个任务拥有：

- 独立浏览器实例
- 独立上下文
- 独立临时邮箱
- 独立日志前缀
- 独立结果记录

### 任务结果

每个注册任务最终输出以下结构化结果：

- `task_id`
- `success`
- `duration_ms`
- `email`
- `token_file`
- `browser_channel`
- `error`

批量运行结束后，调度层会生成汇总 JSON。

## 10. 结构化日志与调试产物

系统当前提供两类主要调试输出。

### 运行日志

默认日志目录：

- `output/debug`

其中包括：

- `run.log`
- `slider_verify.log`

批量模式下，日志会带任务前缀，便于区分不同注册任务。

### 滑块结构化日志

`verify_slider.py` 会输出 JSONL 格式结构化日志，包含事件例如：

- `browser_ready`
- `captcha_triggered`
- `captcha_images_extracted`
- `captcha_images_captured`
- `ocr_match_computed`
- `internal_attempt_started`
- `drag_completed`
- `slider_result_success`
- `slider_result_failed`
- `solver_succeeded`

### 滑块样本

样本目录位于：

- `output/slider_samples`

样本内容可包括：

- 背景图
- 拼图图
- 页面截图
- 单次尝试摘要
- 整轮运行摘要

## 11. 性能设计

当前架构在页面启动和注册前半段做了以下优化：

1. 页面导航只等待 `domcontentloaded`
2. 使用关键元素可见作为就绪信号
3. 注册表单切换采用短轮询和短确认
4. 账号资料生成与浏览器启动并行执行
5. 表单填写直接使用 `fill`

这些设计的目标是将时间优先留给真正影响成功率的验证码和服务端反馈环节。

## 12. 当前输出目录

| 目录 | 内容 |
|------|------|
| `output/debug` | 运行日志、结构化日志、批量汇总 |
| `output/tokens` | 成功注册后的 token 文件 |
| `output/slider_samples` | 滑块题目样本、截图、摘要 |

## 13. 当前架构关注点

当前架构的重点不是单一的 OCR 精度，而是整个验证码链路的协同：

1. 浏览器运行时是否接近真实环境
2. 目标偏移换算是否准确
3. DOM 状态读取是否稳定
4. 真实鼠标拖动是否平滑且节奏合理
5. 页面结果判定和重试是否及时
6. 调试信息是否足够支撑样本分析

这也是当前项目后续优化时应优先关注的方向。

---

**注意**：本文档只描述当前实现。项目内容仅用于学习、测试与安全研究，请勿用于非法用途。
