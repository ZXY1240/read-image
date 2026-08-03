# read-image

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

如果粘贴或拖拽媒体没有可靠本地路径，按以下顺序处理：
1. 调用 `read_clipboard_image(task, mode)`。
2. 剪贴板没有图片时，调用 `read_dragged_image(task, mode)` 或 `read_dragged_video(task, mode)`。
3. 拖拽工具返回多个候选时，使用返回列表中的路径作为 `path` 参数再次调用。
4. 仍失败时，请用户把文件保存成明确路径后再调用 `read_image/read_video`。

禁止自己运行 `Get-ChildItem $env:TEMP`、禁止按修改时间猜文件、禁止使用“很可能/可能是你刚粘贴的图片”这类推测表述。
