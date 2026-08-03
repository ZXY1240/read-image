# Changelog

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
