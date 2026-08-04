# read-image v1.1.0

read-image gives text-only models vision support for local images, batches, videos, web pages, and Windows screenshots. It defaults to Qwen3-VL-Flash (DashScope compatible mode) and supports GLM and Doubao through OpenAI-compatible providers.

[English](README.en.md) | [中文](README.md)

## Known Limitation

Claude Desktop does not support direct recognition of images or videos dragged from other windows. Dragged media is embedded in the message and is not written to a scannable directory, so text-only models only see `[Unsupported Image]`.

Recommended workflow:
1. Copy the image with `Ctrl+C` after dragging it in, then call `read_clipboard_image`.
2. Or save the file to a known path and call `read_image` / `read_video` with that path.

## Tools

- `read_image(image, task, mode)`: local path, `data:` URL, or base64 image.
- `read_clipboard_image(task, mode)`: read the Windows clipboard image.
- `read_images_batch(images, task, mode, max_workers)`: parallel batch image reading.
- `read_video(video, task, mode)`: local video or HTTP(S) video URL.
- `read_dragged_image(task, mode, path)` / `read_dragged_video(task, mode, path)`: limited scanning for clients that write dragged files to disk. Not supported on Claude Desktop.
- `capture_page(...)`: Playwright interactive webpage screenshots.
- `list_windows()` / `capture_windows(...)`: native Windows screenshots.

## Installation

The plugin uses `uv` and exposes three MCP servers:

- `read-image`
- `capture-page`
- `windows-capture`

### Codex

Enable the plugin in Codex. The local `.env` file can store your API key:

```powershell
READ_IMAGE_API_KEY=your-dashscope-api-key
READ_IMAGE_PROVIDER=openai_compatible
READ_IMAGE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
READ_IMAGE_MODEL=qwen3-vl-flash
```

### Claude Code

Persistent local install:

```powershell
powershell -ExecutionPolicy Bypass -File <plugin-root>\scripts\install_claude_plugin.ps1
```

Session-only testing:

```powershell
claude --plugin-dir <plugin-root>
```

## Configuration

Key environment variables:

- `READ_IMAGE_API_KEY` / `ARK_API_KEY` / `DOUBAO_API_KEY` / `VISION_API_KEY`
- `READ_IMAGE_PROVIDER`: `openai_compatible` (default), `doubao`, or `auto`
- `READ_IMAGE_BASE_URL`
- `READ_IMAGE_MODEL`
- `READ_IMAGE_CACHE_USE_TASK` (default enabled)
- `READ_IMAGE_CACHE_TTL_SEC` (default 300)
- `READ_IMAGE_EXTREME_ASPECT_RATIO_LIMIT`
- `READ_DRAG_WINDOW_MIN`
- `READ_DRAG_PATTERNS`
- `READ_DRAG_DIRS`

See `.env.example` for the full list.

## Development

```powershell
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run mypy read_image
```

## Privacy and Terms

- [Privacy Policy](PRIVACY.md)
- [Terms of Use](TERMS.md)

## License

MIT License. Copyright (c) 2026 ZXY1240
