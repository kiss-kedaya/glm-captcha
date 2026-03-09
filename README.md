# glm-captcha

基于 Playwright 和 `ddddocr` 的注册自动化实验项目，目标站点为 `https://chat.z.ai/auth`。项目当前重点有两部分：

1. 自动完成注册流程，包括临时邮箱创建、滑动验证码处理、邮箱验证和 token 提取
2. 独立验证阿里云滑动验证码链路，方便单独调试识别、拖动和风控行为

当前滑块方案不是简单的“识别出缺口后一次性拖到目标”，而是：

- 提取背景图和拼图图
- 用 `ddddocr` 做缺口匹配
- 读取页面实时 DOM 状态
- 用真实鼠标做闭环拖动
- 按验证结果刷新题目并重试

更详细的滑块架构说明见 [docs.md](/E:/GitHub/glm-captcha/docs.md)。

## 功能特性

- 支持单账号注册
- 支持批量并发注册多个账号
- 支持 `mail.tm` 和 `duckmail` 两种临时邮箱提供商
- 支持独立滑块压测脚本 `verify_slider.py`
- 支持结构化 JSONL 日志与失败样本采集
- 优先使用本机 `msedge` / `chrome` 浏览器通道，失败时回退到 Playwright Chromium
- 注册成功后自动提取并保存账号 token

## 目录说明

| 路径 | 说明 |
|------|------|
| `main.py` | 注册主入口，支持单账号和批量并发 |
| `verify_slider.py` | 独立滑块验证入口 |
| `slider_captcha_solver.py` | 滑块识别、换算、拖动与重试 |
| `browser_runtime.py` | 浏览器启动、轻量 stealth、上下文配置 |
| `mail_provider_clients.py` | 临时邮箱创建 |
| `mail_verification.py` | 邮箱验证链接轮询 |
| `token_capture.py` | 注册完成后的 token 提取 |
| `output/debug` | 运行日志、结构化日志、批量汇总 |
| `output/tokens` | 成功注册后的 token 文件 |
| `output/slider_samples` | 滑块样本、截图、尝试摘要 |

## 环境要求

- Python 3.10+
- Windows 环境优先验证
- 本机建议安装至少一个真实浏览器：
  - Microsoft Edge
  - Google Chrome

项目会优先尝试：

1. `msedge`
2. `chrome`
3. Playwright 自带 `chromium`

如果你机器上没有 Edge 或 Chrome，仍然可以运行，但滑块通过率通常不如真实浏览器通道稳定。

## 安装

### 1. 创建虚拟环境

```powershell
python -m venv .venv
.venv\Scripts\activate
```

### 2. 安装依赖

```powershell
pip install -r requirements.txt
```

### 3. 安装 Playwright 浏览器依赖

如果你依赖 Playwright 自带 Chromium 作为回退通道，建议执行：

```powershell
playwright install chromium
```

如果你的系统已经安装 Edge 或 Chrome，项目会优先直接使用它们。

## 配置

复制配置模板：

```powershell
Copy-Item .env.example .env
```

`.env.example` 当前字段如下：

```env
MAIL_PROVIDER=mailtm
MAILTM_API_BASE=https://api.mail.tm
DUCKMAIL_API_BASE=https://api.duckmail.sbs
DUCKMAIL_BEARER_TOKEN=
SUBMIT_RETRY_COUNT=5
MAIL_VERIFY_TIMEOUT_SECONDS=120
MAIL_POLL_INTERVAL_SECONDS=3
```

### 配置项说明

| 变量 | 说明 |
|------|------|
| `MAIL_PROVIDER` | 邮箱提供商，支持 `mailtm` 或 `duckmail` |
| `MAILTM_API_BASE` | Mail.tm API 地址 |
| `DUCKMAIL_API_BASE` | DuckMail API 地址 |
| `DUCKMAIL_BEARER_TOKEN` | DuckMail 鉴权 token，如服务端要求则必须配置 |
| `SUBMIT_RETRY_COUNT` | 提交注册失败后的最大重试次数 |
| `MAIL_VERIFY_TIMEOUT_SECONDS` | 等待验证邮件的最长时长 |
| `MAIL_POLL_INTERVAL_SECONDS` | 邮件轮询间隔 |

## 快速开始

### 单账号注册

推荐先用可见浏览器模式跑通一遍：

```powershell
.venv\Scripts\python.exe main.py
```

如果已经激活虚拟环境，也可以直接：

```powershell
python main.py
```

### Headless 模式

```powershell
.venv\Scripts\python.exe main.py --headless
```

### 指定目标地址

```powershell
.venv\Scripts\python.exe main.py --url https://chat.z.ai/auth
```

## 批量并发注册

`main.py` 已支持批量任务调度。常用参数：

- `--count`: 需要注册的总账号数
- `--concurrency`: 同时运行的并发任务数
- `--headless`: 是否无头运行
- `--summary-path`: 批量汇总 JSON 输出路径

### 示例 1：并发 2 个，总共注册 2 个账号

```powershell
.venv\Scripts\python.exe main.py --count 2 --concurrency 2 --headless
```

### 示例 2：并发 3 个，总共注册 5 个账号

```powershell
.venv\Scripts\python.exe main.py --count 5 --concurrency 3
```

### 批量输出

批量运行完成后，会在默认路径生成汇总文件：

```text
output/debug/batch_run_<timestamp>.json
```

汇总内容包含：

- 总任务数
- 并发数
- 总耗时
- 成功数 / 失败数
- 每个任务的结果
  - `task_id`
  - `success`
  - `duration_ms`
  - `email`
  - `token_file`
  - `browser_channel`
  - `error`

## 独立滑块验证

当你只想调试验证码，不想完整跑注册流程时，使用 `verify_slider.py`。

### 最简单用法

```powershell
.venv\Scripts\python.exe verify_slider.py
```

### 连续验证 5 次

```powershell
.venv\Scripts\python.exe verify_slider.py --attempts 5
```

### 不保存样本，只看通过率

```powershell
.venv\Scripts\python.exe verify_slider.py --attempts 5 --sample-artifacts off
```

### 保存全部样本

```powershell
.venv\Scripts\python.exe verify_slider.py --attempts 3 --sample-artifacts all
```

### 常用参数

| 参数 | 说明 |
|------|------|
| `--attempts` | 独立验证次数 |
| `--headless` | 是否无头运行 |
| `--pause-ms` | 每次尝试结束前停留时间 |
| `--timeout-ms` | 页面加载与等待超时 |
| `--save-success-screenshot` | 成功时也保存截图 |
| `--structured-log` | JSONL 结构化日志路径 |
| `--sample-dir` | 样本根目录 |
| `--sample-artifacts` | 样本保留策略：`off` / `failure` / `all` |

## 输出文件

### 注册日志

- `output/debug/run.log`

单账号和批量注册都会写这里。批量场景下，每条日志前面会带任务前缀，例如：

```text
[task-01/5] 已打开页面: https://chat.z.ai/auth（耗时 1088ms）
```

### 滑块结构化日志

- `output/debug/slider_verify.jsonl`

关键事件包括：

- `browser_ready`
- `captcha_triggered`
- `captcha_images_extracted`
- `ocr_match_computed`
- `drag_completed`
- `slider_result_success`
- `slider_result_failed`

### Token 文件

- `output/tokens/token_<email>_<timestamp>.json`

内容包含：

- 注册名称
- 邮箱
- token
- token 来源
- JWT claims 中的邮箱
- 创建时间

### 失败样本

- `output/slider_samples/run_<timestamp>/...`

可用于分析：

- 当前题目的背景图
- 拼图图
- 页面截图
- 单次尝试摘要
- 整轮运行汇总

## 滑块方案说明

当前滑块链路的关键点如下：

1. 读取背景图和拼图图 URL
2. 用 `ddddocr.slide_match` 识别缺口
3. 用背景图自然尺寸和显示尺寸做缩放换算
4. 扣除拼图透明边偏移 `target_x`
5. 读取页面实时 `shadow_offset` 和 `slider_travel`
6. 用 Playwright 真实鼠标拖动而不是 JS 合成事件
7. 按验证结果决定是否刷新题目并继续重试

一个重要结论是：

- `final_shadow_offset == target_shadow_offset` 并不一定意味着服务端会放行

因为阿里云滑块还会综合评估：

- 浏览器指纹
- 鼠标行为真实性
- 拖动总时长
- 拖动节奏是否过于机械

更详细说明见 [docs.md](/E:/GitHub/glm-captcha/docs.md)。

## 常见问题

### 1. `ModuleNotFoundError: No module named 'playwright'`

通常是因为当前 `python` 不是项目虚拟环境里的解释器。优先使用：

```powershell
.venv\Scripts\python.exe main.py
```

或先激活虚拟环境再运行。

### 2. 滑块位置对了，但还是提示“验证失败，请重试”

这通常不是单纯坐标误差，而是服务端行为评分没过。优先排查：

- 是否使用了真实浏览器通道
- 是否拖动过快
- 是否 headless 模式下通过率下降
- 当前网络环境或 IP 是否触发风控

建议先运行：

```powershell
.venv\Scripts\python.exe verify_slider.py --attempts 5 --sample-artifacts failure
```

结合 `output/debug/slider_verify.jsonl` 分析失败样本。

### 3. 批量并发时成功率下降

这是正常现象。并发越高，越容易触发目标站点风控，也可能压垮临时邮箱接口。建议先从较保守的参数开始：

```powershell
.venv\Scripts\python.exe main.py --count 3 --concurrency 2
```

再逐步增加并发。

### 4. 为什么推荐先不开 headless

因为验证码调试阶段最重要的是观察页面状态、重试节奏和失败反馈。先用有头模式确认链路稳定，再切换到 `--headless` 更稳妥。

## 开发建议

推荐先按下面顺序调试：

1. 先跑 `verify_slider.py`，确认当前环境下滑块通过率
2. 再跑 `main.py` 单账号，确认邮箱验证和 token 提取
3. 最后再提高到批量并发

如果你要继续优化成功率，最有价值的数据通常来自：

- `slider_verify.jsonl`
- `output/slider_samples`
- `output/debug/batch_run_<timestamp>.json`

## 合规说明

本项目仅用于学习、测试与安全研究。请勿将其用于任何非法用途，使用者需自行承担由此产生的风险与责任。
