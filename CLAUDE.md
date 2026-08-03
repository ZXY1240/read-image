# read-image

主模型是纯文本模型时，只要任务需要看到图片或视频，先调用本插件提供的 MCP 视觉工具，不要自行假设图片或视频内容。

## 自动调用

- 单张图片调用 `read_image(image, task, mode)`。
- 多张图片调用 `read_images_batch(images, task, mode, max_workers)`。
- 本地视频或视频 URL 调用 `read_video(video, task, mode)`。
- 网页动态内容先调用 `capture_page(...)`，再把截图路径交给批量识别。
- Windows 截图先调用 `list_windows()`，再调用 `capture_windows(...)`，然后把 PNG 路径交给批量识别。

Claude Code 中 MCP 工具名可能带 `mcp__plugin_...` 前缀。使用前先运行 `/mcp` 查看当前会话中的完整工具名，再调用实际名称。
