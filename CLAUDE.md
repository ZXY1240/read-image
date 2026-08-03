# read-image

主模型是纯文本模型时，只要任务需要看到图片或视频，先调用本插件提供的 MCP 视觉工具，不要自行假设图片或视频内容。

## 自动调用

- 单张图片调用 `read_image(image, task, mode)`，支持本地路径、`data:` URL 和 base64。
- 多张图片调用 `read_images_batch(images, task, mode, max_workers)`。
- 本地视频或视频 URL 调用 `read_video(video, task, mode)`。
- 网页动态内容先调用 `capture_page(...)`，再把截图路径交给批量识别。
- Windows 截图先调用 `list_windows()`，再调用 `capture_windows(...)`，然后把 PNG 路径交给批量识别。

Claude Code 中 MCP 工具名可能带 `mcp__plugin_...` 前缀。使用前先运行 `/mcp` 查看当前会话中的完整工具名，再调用实际名称。

如果粘贴图片没有可靠本地路径，不要猜测临时目录里的 `.tmp` 或旧文件。优先把图片数据作为 `data:` URL 或 base64 传给 `read_image`；拿不到数据时运行 `scripts/save_clipboard_image.ps1` 保存剪贴板图片为稳定 PNG，再传给 `read_image`。剪贴板也没有图片时，请用户先把图片保存成文件。
