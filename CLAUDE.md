# omnimodal

主模型是纯文本模型时，只要任务需要看到图片或视频，先调用本插件提供的 MCP 视觉工具，不要自行假设图片或视频内容。

## 自动调用

- 单张图片调用 `read_image(image, task, mode)`，支持本地路径、`data:` URL 和 base64。
- 剪贴板图片且没有可靠路径时，直接调用 `read_clipboard_image(task, mode)`。
- 拖拽图片/视频且没有可靠路径时，调用 `read_dragged_image(task, mode)` 或 `read_dragged_video(task, mode)`。
- 多张图片调用 `read_images_batch(images, task, mode, max_workers)`。
- 本地视频或视频 URL 调用 `read_video(video, task, mode)`。
- 网页动态内容先调用 `capture_page(...)`，再把截图路径交给批量识别。
- Windows 截图先调用 `list_windows()`，再调用 `capture_windows(...)`，然后把 PNG 路径交给批量识别。

Claude Code 中 MCP 工具名可能带 `mcp__plugin_...` 前缀。使用前先运行 `/mcp` 查看当前会话中的完整工具名，再调用实际名称。

Claude 桌面端不支持直接识别跨窗口拖拽的图片和视频。拖入后媒体不会落盘，纯文本模型只能看到 `[Unsupported Image]` 占位符。

正确工作流：
1. 拖入后 `Ctrl+C` 复制，调用 `read_clipboard_image(task, mode)`。
2. 或把文件保存到明确路径，提供路径调用 `read_image/read_video`。
3. `read_dragged_image/read_dragged_video` 只适用于会落盘的客户端环境；Claude 桌面端不适用。

禁止自己运行 `Get-ChildItem $env:TEMP`、禁止按修改时间猜文件、禁止使用“很可能/可能是你刚粘贴的图片”这类推测表述。
