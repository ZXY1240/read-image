# read-image v0.5.0

Codex 插件：让纯文本主模型读取本地图片、批量图片、视频和网页截图。默认使用豆包 Seed 2.1 Turbo，也支持 GLM、通义千问等 OpenAI 兼容视觉接口。

## 功能

- `read_image(image, task, mode)`：读取单张本地图片。
- `read_images_batch(images, task, mode, max_workers)`：并行读取多张图片并按原顺序返回。
- `read_video(video, task, mode)`：读取本地视频或视频 URL，本地视频优先 Files API，失败自动回退 Base64，支持转 MP4 和压缩。
- `capture_page(url, actions, viewport, output_dir)`：用 Playwright 交互式截图。
- `list_windows()`：列出当前可见 Windows 窗口标题。
- `capture_windows(mode, window, output_dir)`：截取 Windows 全屏、主屏或指定窗口并返回 PNG 路径。

## 架构

```mermaid
flowchart LR
  A[Codex / AI Agent] --> B[MCP read-image]
  A --> C[MCP capture-page]
  A --> D[MCP windows-capture]
  B --> E[Vision Provider]
  B --> F[FFmpeg 转码/压缩]
  E --> E1[豆包 Files API / Chat API]
  E --> E2[GLM / Qwen OpenAI 兼容]
  C --> G[Playwright]
  D --> H[PowerShell + Windows API]
  C --> B
  D --> B
```

## 安装

在 Codex 中启用本插件后，插件会通过 `.mcp.json` 自动启动三个 MCP 服务。项目使用 `uv` 管理依赖：

```powershell
uv run --project . read-image-vision --help
uv run --project . read-image-capture-page --help
uv run --project . read-image-windows-capture --help
```

如果使用默认 Chromium 而不是本机 Edge/Chrome，需要先安装 Playwright 浏览器：

```powershell
uv run --project . --with playwright playwright install chromium
```

## 配置

公开仓库不包含任何 API Key。使用前在插件根目录创建本地 `.env`：

```powershell
READ_IMAGE_API_KEY=你的豆包 API Key
```

也兼容系统环境变量 `ARK_API_KEY`、`DOUBAO_API_KEY`、`VISION_API_KEY`。`.env` 不会进入 Git；更多配置参考 `.env.example`。

图片默认最大边为 2048，`READ_IMAGE_FORMAT=auto` 会保留 PNG 等无损格式，避免截图文字被 JPEG 压缩弄糊。批量识别支持 `READ_IMAGE_BATCH_TIMEOUT_SEC` 单图超时。视频新增 `READ_VIDEO_BASE64_MAX_MB`（Base64 回退上限 45MB）、`READ_VIDEO_DOWNLOAD_MAX_MB`（远程下载上限 512MB）和 `READ_VIDEO_FILES_API_TIMEOUT_SEC`。

截图输出目录默认只允许临时目录和当前工作区；需要其他目录时通过 `READ_IMAGE_ALLOWED_OUTPUT_DIRS` 配置。`READ_VIDEO_KEEP_AUDIO=1` 可保留视频音轨，默认继续去掉音轨以兼容当前豆包视频理解。

远程 URL 默认禁止本机、内网和云元数据地址；本地调试需要访问时设置 `READ_IMAGE_ALLOW_PRIVATE_URLS=1`。视频任务使用独立工作池，可通过 `READ_VIDEO_WORKERS` 调整并发数，默认 2；旧的 `READ_IMAGE_VIDEO_WORKERS` 继续兼容。

## 切换视觉 Provider

默认 `READ_IMAGE_PROVIDER=auto`：设置 `READ_IMAGE_BASE_URL` 和 `READ_IMAGE_MODEL` 后自动切换为通用 OpenAI 兼容 Provider，否则使用豆包。

豆包：

```powershell
READ_IMAGE_PROVIDER=doubao
READ_IMAGE_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
READ_IMAGE_MODEL=doubao-seed-2-1-turbo-260628
```

GLM：

```powershell
READ_IMAGE_PROVIDER=openai_compatible
READ_IMAGE_BASE_URL=https://open.bigmodel.cn/api/paas/v4
READ_IMAGE_MODEL=glm-5v-turbo
```

通义千问低成本全能：

```powershell
READ_IMAGE_PROVIDER=openai_compatible
READ_IMAGE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
READ_IMAGE_MODEL=qwen3-omni-flash
```

通义千问更强视觉：

```powershell
READ_IMAGE_PROVIDER=openai_compatible
READ_IMAGE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
READ_IMAGE_MODEL=qwen3-vl-plus
```

以上模型 ID 只是示例，请以你实际开通的模型为准。通用 Provider 的图片会按 OpenAI 兼容格式发送；视频会尝试 `video_url`，如果目标模型不接受会返回“当前模型不支持视频”。GLM/Qwen 的 thinking 参数由 `READ_IMAGE_OPENAI_THINKING_PARAM=auto|thinking|enable_thinking|none` 控制，`auto` 会按模型名自动选择。

六档 mode 仍可单独覆盖：

```powershell
READ_IMAGE_PROFILES_JSON={"quick":{"max_tokens":256,"timeout_sec":20,"thinking":false,"prompt":"只输出关键文字"}}
```

## 调用示例

```python
await read_image(
    image="C:/path/to/image.png",
    task="提取图中所有文字",
    mode="standard",
)
```

```python
await read_images_batch(
    images=["C:/a.png", "C:/b.png"],
    task="描述每张图内容",
    mode="standard",
    max_workers=4,
)
```

```python
await read_video(
    video="C:/path/to/video.mp4",
    task="按时间顺序描述画面变化",
    mode="quick",
)
```

```python
paths = await capture_page(
    url="https://example.com",
    actions=[{"action": "click", "selector": "#menu"}],
    viewport="1280x800",
)
```

## 故障排查

- API Key 无效：检查插件根目录 `.env` 中 `READ_IMAGE_API_KEY` 是否为完整的豆包 API Key，然后重新加载 Codex 插件。
- 远程 URL 被拒绝：插件默认阻止内网/本机地址；确需访问时设置 `READ_IMAGE_ALLOW_PRIVATE_URLS=1`。
- 网页截图失败：确认已安装 Playwright 浏览器，或设置 `CAPTURE_PAGE_BROWSER=msedge` / `chrome`。
- Windows 截图失败：先用 `list_windows()` 查看准确窗口标题；硬件加速窗口可能退回屏幕拷贝。
- 视频处理慢：视频任务运行在独立工作池中，可通过 `READ_VIDEO_WORKERS` 增加并发。

## 模式

- `quick`：快速识别，短 OCR。
- `standard`：标准提取，默认。
- `full`：完整提取。
- `quick_analysis`：快速分析。
- `balanced_analysis`：平衡分析。
- `deep_analysis`：深度分析。

视频模式下六档使用更长超时。本地视频默认 50MB 限制，超过会自动压缩；本地文件优先上传 Files API，模型不支持时回退 Base64；格式不支持会自动转成 MP4/H.264 后重试。远程 URL 优先直连，过大或格式错误时下载到临时目录再走本地处理。

## 开发

```powershell
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run mypy read_image
uv run python scripts/validate_plugin.py
```

版本记录见 [CHANGELOG.md](CHANGELOG.md)。

## License

MIT License. Copyright (c) 2026 ZXY1240
