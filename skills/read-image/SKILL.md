---
name: read-image
description: 读取本地图片和视频，并通过豆包、GLM 或通义千问视觉模型提取指定内容。遇到图片路径、截图、UI/网页截图、图表、错误弹窗、OCR/识别/提取图中文字、视频内容理解或任何需要看图/看视频的任务时自动启用。
---
# Read Image

主模型使用纯文本模型时，只要任务需要看见图片或视频，就自动调用视觉工具，不要等用户手动开启插件，也不要自行假设图片或视频内容。

## 自动触发

出现以下任一情况时必须自动读图：
- 用户消息中有本地图片路径，或 Codex 附带了图片。
- 用户消息中有本地视频路径，或任务要求理解视频内容。
- 任务包含识别、OCR、截图、UI、设计稿、图表、表格图片、错误弹窗或视觉比较等意图。
- 主任务本身不是看图/看视频，但为了完成它必须知道图片或视频内容。

## 调用方式

Claude Code 中 MCP 工具名可能带 `mcp__plugin_...` 前缀。调用前先运行 `/mcp` 查看当前会话中的完整工具名，然后使用实际名称。

单图优先调用 MCP 工具 `read_image(image, task, mode)`。

多张图片优先调用 `read_images_batch(images, task, mode, max_workers)`，不要逐张串行调用。

本地视频或视频 URL 调用 `read_video(video, task, mode)`。

网页动态内容先调用 `capture_page(url, actions, viewport, output_dir)` 获取各状态截图，再把返回的截图路径列表传给 `read_images_batch`。

Windows 全屏、主屏或指定窗口截图调用 `capture_windows(mode, window, output_dir)`；需要先知道窗口标题时调用 `list_windows()`。返回的 PNG 路径传给 `read_images_batch`。

桌面软件控制继续使用官方 `computer-use` 插件；如果官方策略阻止浏览器操作，不尝试绕过，改用 `capture_page` 或 `capture_windows` 截图。

## 命令行兜底

如果当前会话没有 MCP 工具，定位已安装插件根目录后使用项目命令：

```powershell
uv run --project <插件根目录> read-image-vision --image <图片绝对路径> --task "<具体任务>" --mode standard
```

批量命令行兜底是重复传 `--image`：

```powershell
uv run --project <插件根目录> read-image-vision --image <图1> --image <图2> --task "<任务>" --mode standard --max-workers 4
```

视频命令行兜底：

```powershell
uv run --project <插件根目录> read-image-vision --video <视频路径或URL> --task "<任务>" --mode standard
```

网页截图命令行兜底：

```powershell
uv run --project <插件根目录> read-image-capture-page --url "<网页URL>" --viewport 1280x800 --output-dir <输出目录>
```

Windows 截图命令行兜底：

```powershell
uv run --project <插件根目录> read-image-windows-capture --list-windows
uv run --project <插件根目录> read-image-windows-capture --capture --mode full
uv run --project <插件根目录> read-image-windows-capture --capture --mode window --window "Chrome"
```

命令行 stdout 就是视觉模型返回结果，原样使用即可。

## Windows 原生截图

`capture_windows` 只截图和保存 PNG，不分析：
- `mode="full"`：所有显示器。
- `mode="primary"`：主显示器。
- `mode="window"`：按窗口标题关键字截取指定窗口，使用 `PrintWindow`。
- 返回路径后交给 `read_images_batch`。

截图工具只用于用户明确要求截图；不用于截取终端命令、密码管理器、系统安全设置或敏感认证窗口。

## 视频处理规则

`read_video(video, task, mode)` 直接把整段视频发送给豆包：
- 本地视频默认 50MB 以内，超过会自动用 FFmpeg 压缩。
- 本地视频优先走 Files API；模型不支持或上传失败时自动回退 Base64。
- 格式不被豆包接受时，会自动转成 MP4/H.264 后重试一次。
- 转换或压缩失败时返回“不支持此视频格式”或“视频文件较大，不支持上传”，并给出解决建议。
- 六档 `mode` 与图片一致，但视频使用更长超时：quick 90s、standard 180s、full 360s、quick_analysis 180s、balanced_analysis 360s、deep_analysis 600s。

图片默认最大边 2048，`READ_IMAGE_FORMAT=auto` 会保留 PNG 等无损格式，截图文字不要手动转成 JPEG。

## Provider

默认使用豆包。通过 `READ_IMAGE_PROVIDER=openai_compatible` 配合 `READ_IMAGE_BASE_URL`、`READ_IMAGE_MODEL` 可切换 GLM、通义千问等 OpenAI 兼容模型。

豆包保留 Files API、Base64 回退、视频转 MP4 和压缩能力。通用 Provider 的图片按 OpenAI 兼容格式发送；视频会尝试 `video_url`，目标模型不接受时返回“当前模型不支持视频”。

## mode 档位

根据任务自动选择，并在调用参数中显式传 `mode`：
- `quick`：短 OCR、快速识别、只要关键文字。
- `standard`：默认标准提取，适合表格、截图、设计稿和一般长文本。
- `full`：完整提取，适合必须无遗漏保留原文、表格、代码块和细节的任务。
- `quick_analysis`：需要快速判断、结论和依据。
- `balanced_analysis`：需要结构化分析，包含结论、依据、例外和风险。
- `deep_analysis`：需要深度分析，覆盖上下文、证据、推理、风险和结论。

参数说明：
- `image`：必填，本地图片的绝对路径。
- `video`：必填，本地视频绝对路径或 http(s) 视频 URL。
- `task`：本次需要从图中提取或分析的具体内容。未传时单图默认“详细描述图片内容”，批量默认“提取每张图片中的可见内容”。
- `mode`：默认 `standard`。
- `max_workers`：批量默认 4，最高 8。

常用 `task` 示例：
- 提取报错堆栈和文件行号
- 描述 UI 设计稿的布局与组件层级
- 把表格转成 Markdown 输出
- 提取图中所有可见文本

## capture_page 动作

`actions` 是可选的 JSON 动作列表，每执行一个动作都会新增一张全页截图：
- `{"action":"click","selector":"#menu"}`
- `{"action":"hover","selector":".tooltip"}`
- `{"action":"scroll","amount":600}`
- `{"action":"wait","ms":500}`
- `{"action":"type","selector":"input","text":"hello","press":"Enter"}`
- `{"action":"press","key":"Escape"}`

`scroll` 带 `selector` 时只滚动指定容器，不带时滚动页面。页面高度超过 `CAPTURE_PAGE_MAX_FULL_PAGE_HEIGHT`（默认 12000px）时自动改截视口，文件名带 `viewport-only` 标记。

## 环境变量

API Key：`ARK_API_KEY`，也兼容 `READ_IMAGE_API_KEY`、`DOUBAO_API_KEY`、`VISION_API_KEY`。

推荐在插件根目录创建本地 `.env`：

```powershell
READ_IMAGE_API_KEY=你的豆包API Key
```

`.env` 不会进入 Git；系统环境变量会优先于 `.env`。

可选配置：
- `READ_IMAGE_PROVIDER`
- `READ_IMAGE_BASE_URL`
- `READ_IMAGE_MODEL`
- `READ_IMAGE_OPENAI_THINKING_PARAM`
- `READ_IMAGE_PROFILES_JSON`
- `READ_IMAGE_CACHE_USE_TASK`
- `READ_IMAGE_MAX_DIMENSION`
- `READ_IMAGE_FORMAT`
- `READ_IMAGE_JPEG_QUALITY`
- `READ_IMAGE_TIMEOUT_SEC`
- `READ_IMAGE_BATCH_WORKERS`
- `READ_IMAGE_BATCH_TIMEOUT_SEC`
- `READ_IMAGE_CACHE_MAX_ENTRIES`
- `READ_IMAGE_LOG_LEVEL`
- `READ_IMAGE_LANGUAGE`（`zh` 或 `en`）
- `READ_VIDEO_MAX_MB`
- `READ_VIDEO_BASE64_MAX_MB`
- `READ_VIDEO_DOWNLOAD_MAX_MB`
- `READ_VIDEO_FILES_API_TIMEOUT_SEC`
- `READ_VIDEO_KEEP_AUDIO`
- `READ_VIDEO_TIMEOUT_SEC`
- `CAPTURE_PAGE_TIMEOUT_SEC`
- `CAPTURE_PAGE_BROWSER`
- `CAPTURE_PAGE_WAIT_UNTIL`
- `CAPTURE_PAGE_SETTLE_MS`
- `CAPTURE_PAGE_MAX_FULL_PAGE_HEIGHT`
- `READ_IMAGE_ALLOWED_OUTPUT_DIRS`
- `READ_IMAGE_ALLOW_PRIVATE_URLS`
- `READ_IMAGE_VIDEO_WORKERS`
- `READ_VIDEO_WORKERS`
- `WINDOWS_CAPTURE_DIR`

公开版不包含 API Key；私人版也不再硬编码 Key。

`READ_IMAGE_ALLOWED_OUTPUT_DIRS` 用分号分隔允许的截图输出根目录；未设置时只允许临时目录和当前工作区。

远程 URL 默认禁止本机、内网和云元数据地址；本地调试需要访问时设置 `READ_IMAGE_ALLOW_PRIVATE_URLS=1`。

## Claude Code 安装

Claude Code 使用原生插件方式安装：

```powershell
powershell -ExecutionPolicy Bypass -File <插件根目录>/scripts/install_claude_plugin.ps1
```

或临时测试：

```powershell
claude --plugin-dir <插件根目录>
```

安装后重启 Claude Code，确认 `/mcp` 中能看到 `read-image`、`capture-page`、`windows-capture`，并确认 `READ_IMAGE_ENV_FILE` 指向插件根目录的 `.env`。
