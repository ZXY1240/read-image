from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

KEY_PREFIX = "ark" + "-"


def _python_version(text: str) -> str | None:
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else None


def _pyproject_version(text: str) -> str | None:
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else None


def _iter_text_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(
            part in {".git", "__pycache__", ".venv", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
            for part in relative.parts
        ):
            continue
        if path.suffix.lower() in {".pyc", ".png", ".jpg", ".mp4", ".pyo"}:
            continue
        yield path


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate read-image plugin layout.")
    parser.add_argument("--root", default=None, help="Plugin repository root.")
    parser.add_argument(
        "--public",
        action="store_true",
        help="Also enforce that no API key is present.",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    errors: list[str] = []

    plugin_json = root / ".codex-plugin" / "plugin.json"
    if not plugin_json.is_file():
        errors.append("Missing .codex-plugin/plugin.json")
    else:
        try:
            plugin = json.loads(plugin_json.read_text(encoding="utf-8"))
            if plugin.get("name") != "read-image":
                errors.append("plugin.json name must be read-image")
            skills = plugin.get("skills")
            if not skills:
                errors.append("plugin.json missing skills")
            else:
                skill_root = root / skills.lstrip("./")
                if not list(skill_root.glob("*/SKILL.md")):
                    errors.append("No skills/*/SKILL.md found")
        except json.JSONDecodeError as exc:
            errors.append(f"plugin.json is invalid JSON: {exc}")

    mcp_json = root / ".mcp.json"
    if not mcp_json.is_file():
        errors.append("Missing .mcp.json")
    else:
        try:
            mcp = json.loads(mcp_json.read_text(encoding="utf-8"))
            servers = mcp.get("mcpServers", {})
            if not {"read-image", "capture-page", "windows-capture"}.issubset(servers):
                errors.append("mcpServers must include read-image, capture-page, windows-capture")
            read_image_env = set(servers.get("read-image", {}).get("env_vars", []))
            required_read_image_env = {
                "READ_IMAGE_PROVIDER",
                "READ_IMAGE_BASE_URL",
                "READ_IMAGE_MODEL",
                "READ_IMAGE_OPENAI_THINKING_PARAM",
                "READ_IMAGE_PROFILES_JSON",
                "READ_IMAGE_CACHE_USE_TASK",
                "READ_IMAGE_EXTREME_ASPECT_RATIO_LIMIT",
                "READ_IMAGE_ALLOWED_OUTPUT_DIRS",
                "READ_IMAGE_ALLOW_PRIVATE_URLS",
                "READ_IMAGE_VIDEO_WORKERS",
                "READ_VIDEO_WORKERS",
                "READ_IMAGE_BATCH_TIMEOUT_SEC",
                "READ_VIDEO_BASE64_MAX_MB",
                "READ_VIDEO_DOWNLOAD_MAX_MB",
                "READ_VIDEO_FILES_API_TIMEOUT_SEC",
                "READ_VIDEO_KEEP_AUDIO",
            }
            if not required_read_image_env.issubset(read_image_env):
                errors.append("read-image mcp env_vars missing video/batch timeout vars")
            capture_env = set(servers.get("capture-page", {}).get("env_vars", []))
            required_capture_env = {
                "CAPTURE_PAGE_WAIT_UNTIL",
                "CAPTURE_PAGE_SETTLE_MS",
                "CAPTURE_PAGE_MAX_FULL_PAGE_HEIGHT",
            }
            if not required_capture_env.issubset(capture_env):
                errors.append("capture-page mcp env_vars missing capture tuning vars")
        except json.JSONDecodeError as exc:
            errors.append(f".mcp.json is invalid JSON: {exc}")

    if not (root / "pyproject.toml").is_file():
        errors.append("Missing pyproject.toml")
    if not (root / "read_image" / "mcp" / "read_image_server.py").is_file():
        errors.append("Missing read_image.mcp.read_image_server")
    if not (root / "read_image" / "providers" / "factory.py").is_file():
        errors.append("Missing read_image.providers.factory")
    if not (root / "skills" / "read-image" / "SKILL.md").is_file():
        errors.append("Missing skills/read-image/SKILL.md")
    if not (root / "CLAUDE.md").is_file():
        errors.append("Missing CLAUDE.md")
    if not (root / ".claude-mcp.json").is_file():
        errors.append("Missing .claude-mcp.json")

    claude_plugin_path = root / ".claude-plugin" / "plugin.json"
    if not claude_plugin_path.is_file():
        errors.append("Missing .claude-plugin/plugin.json")
    else:
        try:
            claude_plugin = json.loads(claude_plugin_path.read_text(encoding="utf-8"))
            if claude_plugin.get("name") != "read-image":
                errors.append(".claude-plugin/plugin.json name must be read-image")
            if claude_plugin.get("mcpServers") != "./.claude-mcp.json":
                errors.append(
                    ".claude-plugin/plugin.json mcpServers must point to ./.claude-mcp.json"
                )
        except json.JSONDecodeError as exc:
            errors.append(f".claude-plugin/plugin.json is invalid JSON: {exc}")

    if (root / ".claude-mcp.json").is_file():
        try:
            claude_mcp = json.loads((root / ".claude-mcp.json").read_text(encoding="utf-8"))
            servers = claude_mcp.get("mcpServers", {})
            if not {"read-image", "capture-page", "windows-capture"}.issubset(servers):
                errors.append(
                    ".claude-mcp.json must include read-image, capture-page, windows-capture"
                )
            for server_name, server in servers.items():
                server_args = server.get("args", [])
                if "${CLAUDE_PLUGIN_ROOT}" not in server_args:
                    errors.append(
                        f".claude-mcp.json server {server_name} must use ${{CLAUDE_PLUGIN_ROOT}}"
                    )
        except json.JSONDecodeError as exc:
            errors.append(f".claude-mcp.json is invalid JSON: {exc}")

    if not (root / "scripts" / "install_claude_plugin.ps1").is_file():
        errors.append("Missing scripts/install_claude_plugin.ps1")
    if not (root / "scripts" / "save_clipboard_image.ps1").is_file():
        errors.append("Missing scripts/save_clipboard_image.ps1")

    versions: list[str] = []
    init_path = root / "read_image" / "__init__.py"
    if init_path.is_file():
        version = _python_version(init_path.read_text(encoding="utf-8"))
        if version:
            versions.append(version)
    pyproject_path = root / "pyproject.toml"
    if pyproject_path.is_file():
        version = _pyproject_version(pyproject_path.read_text(encoding="utf-8"))
        if version:
            versions.append(version)
    if plugin_json.is_file():
        try:
            plugin = json.loads(plugin_json.read_text(encoding="utf-8"))
            version = plugin.get("version")
            if isinstance(version, str) and version:
                versions.append(version)
        except json.JSONDecodeError:
            pass
    if claude_plugin_path.is_file():
        try:
            claude_plugin = json.loads(claude_plugin_path.read_text(encoding="utf-8"))
            version = claude_plugin.get("version")
            if isinstance(version, str) and version:
                versions.append(version)
        except json.JSONDecodeError:
            pass
    if len(set(versions)) != 1:
        errors.append(f"Version mismatch across package metadata: {versions}")

    config_path = root / "read_image" / "config.py"
    if config_path.is_file():
        config_text = config_path.read_text(encoding="utf-8")
        if "HARDCODED_API_KEY" in config_text:
            errors.append("read_image/config.py must not contain HARDCODED_API_KEY")

    gitignore_path = root / ".gitignore"
    if gitignore_path.is_file():
        gitignore_text = gitignore_path.read_text(encoding="utf-8")
        if ".env" not in gitignore_text:
            errors.append(".gitignore must ignore .env")

    if args.public:
        for path in _iter_text_files(root):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if KEY_PREFIX in text.lower():
                errors.append(f"Public copy contains API key text: {path}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print("Plugin validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
