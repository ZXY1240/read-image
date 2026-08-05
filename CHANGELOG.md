# Changelog

## v3.0.0

- 项目正式升级为 Omnimodal 3.0.0：工具名全部改为 `omnimodal_*`，环境变量全部改为 `OMNIMODAL_*`。
- 删除豆包和 GLM Provider，只保留千问/阿里 DashScope API。
- 新增/完善音频识别：短音频理解、长音频 ASR 自动转写、批量音频和拖拽音频。
- 新增/完善图片、视频、音频生成：文生图、图生图、图片编辑、文生视频、图生视频、视频编辑、TTS、声音克隆、声音设计、音乐生成。
- 生成工具强制 `confirm=true` 费用确认；异步任务统一返回 `task_id` 并可用 `omnimodal_get_task_result` 查询。
- 配置迁移到插件目录 `config/model_catalog.json`、`config/profiles.json`，本机覆盖使用被 Git 忽略的 `config/local.json`。
- 更新 Codex、Claude Code MCP 配置、CLI 命令、安装脚本、文档和公开版密钥扫描。
- 视频生成按官方接口修正：分辨率仅支持 `720P/1080P`，`480P` 自动升级；图生视频使用 `media.first_frame`，视频编辑新增 `omnimodal_edit_video`，本地媒体上传自动带 OSS 资源解析头。
- 音频生成按官方接口修正：新增 Qwen 声音克隆、声音设计和 CosyVoice 自定义音色流程；`fun-music-v1` 为阿里云邀测接口，未开通时返回 `AccessDenied`。
- 增加真实 API 探针汇总 `test-results/probe-summary.json`，覆盖识别、ASR、图片生成/编辑、TTS、声音克隆/设计和视频生成/编辑模型。

## v2.1.0

- 修复第二份锐评（v2.0.0 评审）P0+P1 问题：
  - `upload.py` 文件句柄改用 with 管理，避免泄漏。
  - `get_generation_result` 由无效桩改为真实任务状态查询（返回 result_url/error）。
  - 价格标注四处统一（Field description / _*_spec() / README / docstring），并加防漂移测试。
  - `.mcp.json` 补 generation server，validate_plugin 增加两配置文件服务器一致性校验。
  - `video_worker_count` 非法值改为继续尝试下一个变量名（原直接返回默认值）。
  - 默认 Provider 修正为 openai_compatible + qwen3-vl-flash（与文档一致），豆包显式配置仍可用。
  - `analyze_audio` 本地音频 base64 构造修复（oss:// URL 不再被误包 base64）；小文件走内联 base64、大文件走临时上传。
  - `WINDOWS_CAPTURE_DIR` 统一走沙箱校验。
  - `poll_status` 对 4xx 不再重试（客户端错误重试无意义）。
  - api.py 消除对 `_provider._client` 的直接访问（新增 `attach_client` 公开方法）。
  - 新增 `trf()` 参数化翻译，迁移 6 处 f-string 预插值调用点（i18n 不再形同虚设）。
  - capture_page / windows_capture 临时目录：失败时清理、成功保留交付物、顺带清理 24h 前旧目录。
  - 视频拖拽无候选提示修正（视频无法复制到剪贴板，改为引导保存到路径）。
- 新模块补测试：audio_processing（10 用例）、upload（4 用例）、generation_server（10 用例）、workers（4 用例）。
- CI 加覆盖率门禁（pytest-cov，阈值 60%，当前实际 ~69%）。
- README/README.en 开头补项目定位说明（给 DeepSeek 等纯文本主模型补齐多模态能力）。

## v2.0.0

- 项目更名为 omnimodal（原 read-image），包名 `read_image` → `omnimodal`，仓库迁移至 https://github.com/good-boy4069/Deepseek-omnimodal。
- 从视觉（图片/视频识别）扩展为全模态：新增音频识别、文生图、文生视频与 TTS 语音生成能力（新增 generation server，工具名 `read_image` 等保持兼容不变）。

## v1.1.0

- 默认视觉模型切换为通义千问 qwen3-vl-flash（DashScope 兼容模式），新增 `READ_IMAGE_PROVIDER=openai_compatible` + `READ_IMAGE_BASE_URL` + `READ_IMAGE_MODEL` 配置。
- 图片与视频均走 OpenAI 兼容格式；视频自动回退 Base64 通道（上限 `READ_VIDEO_BASE64_MAX_MB`，默认 45MB）。
- 豆包保留为可回退 Provider（`READ_IMAGE_PROVIDER=doubao`），Files API 视频上传能力不变。
- 同步更新 README / README.en / SKILL / .env.example 的默认模型与配置示例。

## v1.0.0

- 正式稳定版发布。
- 公开声明 Claude 桌面端不支持直接识别跨窗口拖拽图片/视频。
- 推荐工作流改为剪贴板复制或提供文件路径。
- 修复拖拽白名单 `claude-*` → `claude*`，兼容 `claude_drag_*` 命名。
- 改进拖拽工具无候选和路径校验错误提示。
- 安装脚本升级为清理旧 `.venv`、缓存和 egg-info。
- 拆分 `media.py` 为图片处理与视频处理模块，保留兼容入口。
- 移除测试全局 fake API Key，改为 API 测试显式启用。
- 新增英文 README。

## v0.9.0

- 删除未接线的 `ConcurrencyGate` 死代码。
- 缓存默认包含 task，并新增 `READ_IMAGE_CACHE_TTL_SEC` TTL，避免同图不同任务串结果。
- 密钥扫描扩展为 `ark-`、`sk-` 等常见格式。
- 新增 `read_dragged_image` / `read_dragged_video` 拖拽媒体识别工具。
- 新增 `READ_DRAG_WINDOW_MIN`、`READ_DRAG_PATTERNS`、`READ_DRAG_DIRS` 配置。

## v0.8.0

- 新增 `read_clipboard_image` MCP 工具，直接保存并识别 Windows 剪贴板图片。
- `read-image-vision` 增加 `--clipboard` 命令行入口。
- SKILL/CLAUDE 文档明确禁止扫描临时目录、禁止按时间戳猜测图片、禁止使用“很可能”类推测。

## v0.7.0

- `read_image` 支持本地路径、`data:` URL 和 base64 图片数据。
- 修复图片文件被占用时误报“无法解码”，改为明确的“文件被占用或无权限”提示。
- 极端长宽比图片自动切片识别，避免被缩成细条后产生幻觉。
- 新增 Claude 桌面剪贴板图片保存脚本 `scripts/save_clipboard_image.ps1`。
- SKILL/CLAUDE 文档禁止猜测临时目录旧文件，优先使用 data URL/base64 或剪贴板稳定路径。

## v0.6.0

- 新增 Claude Code 原生插件包装：`.claude-plugin/plugin.json`、`.claude-mcp.json`、`CLAUDE.md`。
- 新增 `scripts/install_claude_plugin.ps1`，可把插件持久安装到 `~/.claude/skills/read-image`。
- SKILL 和 CLAUDE.md 增加 Claude Code 工具名前缀说明，确保图片、视频、网页截图和 Windows 截图可自动调用。
- README 增加 Claude Code 安装、权限批准和故障排查说明。

## v0.5.0

- 新增 Provider 抽象：豆包保持完整视频能力，新增通用 OpenAI 兼容 Provider，可配置 GLM、Qwen 等模型。
- 重写批量图片并发：请求级并发、单任务 deadline、429 局部退避，不再因闸门阻塞误判超时。
- 增强 SSRF：HTTP 传输层在每次连接和重定向前重新校验目标地址，缓解 DNS 重绑定。
- 缓存 key 默认去掉 task 文本，改为媒体哈希 + mode + model + provider；`READ_IMAGE_CACHE_USE_TASK=1` 可恢复。
- 六档 mode 保留默认值，并支持 `READ_IMAGE_PROFILES_JSON` 覆盖 thinking、max_tokens、超时和提示词。
- 视频工作池环境变量改为 `READ_VIDEO_WORKERS`，旧的 `READ_IMAGE_VIDEO_WORKERS` 继续兼容。
- CI 增加真实 Playwright 网页截图和真实 Windows Notepad 截图集成测试。

## v0.4.0

- 增加 SSRF 防护：远程 URL 默认禁止本机、内网和云元数据地址。
- 视频任务使用独立工作池，避免长期占用普通图片请求线程。
- 视频文件删除失败时重试并返回清理失败提示。
- 合并错误码规范化函数，抽取三个 MCP 服务器的公共 CLI 启动代码。
- CI 增加 Python 3.10/3.11/3.12 matrix、Windows job 和 `ruff format --check`。
- README 增加架构说明、调用示例、配置说明和故障排查。

## v0.3.1

- 改为本地 `.env` + 系统环境变量存储 API Key，删除硬编码 Key。
- 增加统一脱敏 HTTP 日志、连接池限制和退出清理。
- 重写批量图片并发与超时取消。
- 增加错误体系、路径沙箱、视频音轨开关和递归转换深度保护。

## v0.3.0

- 完成工程化重构：Python 包结构、CI、测试、文档和公开发布流程。
