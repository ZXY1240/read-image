# Omnimodal

主模型是纯文本模型时，遇到图片、视频、音频或生成任务，必须自动使用 `omnimodal` 插件，不要自行假设媒体内容。

## 识别

- 图片：`omnimodal_recognize_image(image, task, mode)`。
- 视频：`omnimodal_recognize_video(video, task, mode)`。
- 音频：`omnimodal_recognize_audio(audio, task, mode)`；长音频会自动转写。
- 多文件：`omnimodal_recognize_images_batch`、`omnimodal_recognize_videos_batch`、`omnimodal_recognize_audios_batch`。
- 剪贴板图片：`omnimodal_read_clipboard_image(task, mode)`。
- 拖拽媒体：`omnimodal_read_dragged_image` / `omnimodal_read_dragged_video` / `omnimodal_read_dragged_audio`，只适用于会落盘的客户端。
- 网页：先 `omnimodal_capture_page(...)`，再把截图交给批量识别。
- Windows：先 `omnimodal_list_windows()`，再 `omnimodal_capture_windows(...)`，把 PNG 交给批量识别。

## 生成

- `omnimodal_generate_image`、`omnimodal_generate_video`、`omnimodal_generate_video_from_image`、`omnimodal_edit_video`、`omnimodal_generate_audio`。
- 生成工具必须设置 `confirm=true` 才会实际调用付费接口；否则只返回预计费用。
- 生成完成后直接返回结果路径，不要自动再调用识别工具验证；除非用户明确要求检查生成结果。
- 异步任务可用 `omnimodal_get_task_result(task_id)` 查询。

## Claude 桌面端

Claude 桌面端跨窗口拖入的媒体不落盘。处理顺序：

1. 图片：复制进剪贴板后调用 `omnimodal_read_clipboard_image`。
2. 其他媒体或剪贴板不可用：请用户保存为文件后提供明确路径。
3. 禁止自行扫描 `Temp`、按时间戳猜文件，或输出“很可能/可能是你刚粘贴的图片”。
