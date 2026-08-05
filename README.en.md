# Omnimodal v3.0.0

Omnimodal gives text-only models multimodal capabilities: recognize images, video, and audio, and generate images, video, and audio through Qwen/DashScope.

## Positioning

Omnimodal is an MCP-based multimodal capability layer for text-only agents, including DeepSeek. Instead of replacing the underlying model, it exposes stable MCP tools so agents can perceive, process, and generate image, video, and audio media.

Design goals:

- Stable tool names, arguments, and return formats for agent automation.
- Recognition and generation are separated; generated output is not automatically recognized again.
- Batch, clipboard, dragged media, web capture, and native Windows capture are supported.
- Cost confirmation, async `task_id`, media conversion/compression, log redaction, and remote URL protection are built in.

## DeepSeek Harness Integration

The project is ready to be registered as a plugin, skill, or MCP component in the DeepSeek Harness ecosystem:

- Four stdio MCP servers: `omnimodal-recognize`, `omnimodal-generation`, `omnimodal-capture-page`, and `omnimodal-windows-capture`.
- Tool names, configuration, and return formats are stable.
- Image, video, and audio recognition and generation support async tasks with `task_id`.
- When DSH is available, MCP registration, tool discovery, media upload/cleanup, and cost confirmation can be adapted first.

## Architecture

```text
Agent (Codex / Claude Code / DSH)
         │  MCP stdio
         ▼
omnimodal-recognize / omnimodal-generation
omnimodal-capture-page / omnimodal-windows-capture
         │  HTTP
         ▼
Qwen / DashScope APIs
```

## Tools

### Recognition

- `omnimodal_recognize_image(image, task, mode)`
- `omnimodal_recognize_images_batch(images, task, mode, max_workers)`
- `omnimodal_recognize_video(video, task, mode)`
- `omnimodal_recognize_videos_batch(videos, task, mode, max_workers)`
- `omnimodal_recognize_audio(audio, task, mode)`
- `omnimodal_recognize_audios_batch(audios, task, mode, max_workers)`
- `omnimodal_read_clipboard_image(task, mode)`
- `omnimodal_read_dragged_image(task, mode, path)`
- `omnimodal_read_dragged_video(task, mode, path)`
- `omnimodal_read_dragged_audio(task, mode, path)`

### Generation

- `omnimodal_generate_image(prompt, tier, size, n, wait, confirm)`
- `omnimodal_generate_video(prompt, tier, duration, resolution, wait, confirm)`
- `omnimodal_generate_video_from_image(image, prompt, tier, duration, resolution, wait, confirm)`
- `omnimodal_edit_video(video, prompt, tier, duration, resolution, reference_image, wait, confirm)`
- `omnimodal_generate_audio(text, voice, tier, kind, preview_text, wait, confirm)`
- `omnimodal_transcribe_audio(audio, language, wait)`
- `omnimodal_get_task_result(task_id)`

Generation tools only call paid APIs when `confirm=true`. Results are saved to `~/.omnimodal/outputs`.
After generation, return the result path directly; do not automatically recognize it unless the user explicitly asks to verify the output.

Video generation resolution supports `720P/1080P`; `480P` is upgraded to `720P`.
For `omnimodal_generate_audio`, `kind` supports `tts`, `clone`, `voice_design`, and `music`.
`music` uses `fun-music-v1`, which is an invite-only Alibaba Cloud model and may return `AccessDenied` until enabled.

### Capture

- `omnimodal_capture_page(url, actions, viewport, output_dir)`
- `omnimodal_list_windows()`
- `omnimodal_capture_windows(mode, window, output_dir)`

## Install

Requires Python 3.10+ and `uv`.

```powershell
git clone https://github.com/good-boy4069/Deepseek-omnimodal.git
cd Deepseek-omnimodal
Copy-Item .env.example .env
```

Set `OMNIMODAL_API_KEY` in `.env`. For Claude Code:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_claude_plugin.ps1
```

Restart Claude Code after installation.

## Configuration

- `config/model_catalog.json`: models, capabilities, pricing, timeouts.
- `config/profiles.json`: recognition profile defaults.
- `config/local.json`: local overrides, ignored by Git.

Important environment variables:

- `OMNIMODAL_API_KEY`
- `OMNIMODAL_BASE_URL`
- `OMNIMODAL_IMAGE_MODEL`
- `OMNIMODAL_VIDEO_MODEL`
- `OMNIMODAL_AUDIO_MODEL_STANDARD`
- `OMNIMODAL_AUDIO_MODEL_PRO`
- `OMNIMODAL_VIDEO_GEN_MODEL_STANDARD_I2V`
- `OMNIMODAL_VIDEO_GEN_MODEL_MAX_I2V`
- `OMNIMODAL_VIDEO_GEN_MODEL_EDIT`
- `OMNIMODAL_OCR_MODEL`
- `OMNIMODAL_ASR_MODEL`
- `OMNIMODAL_IMAGE_GEN_TIMEOUT_SEC`
- `OMNIMODAL_VIDEO_GEN_TIMEOUT_SEC`
- `OMNIMODAL_AUDIO_GEN_TIMEOUT_SEC`
- `OMNIMODAL_MAX_VIDEO_DURATION`
- `OMNIMODAL_GENERATION_OUTPUT_DIR`
- `OMNIMODAL_ALLOWED_OUTPUT_DIRS`
- `OMNIMODAL_ALLOW_PRIVATE_URLS`

## Security

- API keys are only read from `.env` or system environment variables.
- Remote URLs block private, local, and cloud metadata addresses by default.
- Logs redact API keys, query parameters, Base64, and media bodies.
- Generation tools require explicit cost confirmation.

## Maintainer

- GitHub: https://github.com/good-boy4069
- Repository: https://github.com/good-boy4069/Deepseek-omnimodal
