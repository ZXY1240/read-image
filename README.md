# read-image v0.3.0

Codex 插件：让纯文本主模型通过豆包 Seed 2.1 Turbo 读取本地图片、批量图片、视频和网页截图。

## 功能

- `read_image(image, task, mode)`：读取单张本地图片。
- `read_images_batch(images, task, mode, max_workers)`：并行读取多张图片并按原顺序返回。
- `read_video(video, task, mode)`：读取本地视频或视频 URL，本地视频优先 Files API，失败自动回退 Base64，支持转 MP4 和压缩。
- `capture_page(url, actions, viewport, output_dir)`：用 Playwright 交互式截图。
- `list_windows()`：列出当前可见 Windows 窗口标题。
- `capture_windows(mode, window, output_dir)`：截取 Windows 全屏、主屏或指定窗口并返回 PNG 路径。

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

公开仓库不包含任何 API Key。使用前必须设置环境变量：

```powershell
$env:READ_IMAGE_API_KEY = "你的豆包 API Key"
```

也兼容 `ARK_API_KEY`、`DOUBAO_API_KEY`、`VISION_API_KEY`。可用配置参考 `.env.example`。

图片默认最大边为 2048，`READ_IMAGE_FORMAT=auto` 会保留 PNG 等无损格式，避免截图文字被 JPEG 压缩弄糊。批量识别支持 `READ_IMAGE_BATCH_TIMEOUT_SEC` 单图超时。视频新增 `READ_VIDEO_BASE64_MAX_MB`（Base64 回退上限 45MB）、`READ_VIDEO_DOWNLOAD_MAX_MB`（远程下载上限 512MB）和 `READ_VIDEO_FILES_API_TIMEOUT_SEC`。

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

## License

MIT License. Copyright (c) 2026 ZXY1240
