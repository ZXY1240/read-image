from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

KEY_PREFIX = "ark" + "-"
HARDCODED_ASSIGNMENT = "HARDCODED_API_" + "KEY = "
HARDCODED_EMPTY = "HARDCODED_API_" + 'KEY = ""'


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
                "READ_IMAGE_BATCH_TIMEOUT_SEC",
                "READ_VIDEO_BASE64_MAX_MB",
                "READ_VIDEO_DOWNLOAD_MAX_MB",
                "READ_VIDEO_FILES_API_TIMEOUT_SEC",
            }
            if not required_read_image_env.issubset(read_image_env):
                errors.append(
                    "read-image mcp env_vars missing video/batch timeout vars"
                )
            capture_env = set(servers.get("capture-page", {}).get("env_vars", []))
            required_capture_env = {
                "CAPTURE_PAGE_WAIT_UNTIL",
                "CAPTURE_PAGE_SETTLE_MS",
                "CAPTURE_PAGE_MAX_FULL_PAGE_HEIGHT",
            }
            if not required_capture_env.issubset(capture_env):
                errors.append(
                    "capture-page mcp env_vars missing capture tuning vars"
                )
        except json.JSONDecodeError as exc:
            errors.append(f".mcp.json is invalid JSON: {exc}")

    if not (root / "pyproject.toml").is_file():
        errors.append("Missing pyproject.toml")
    if not (root / "read_image" / "mcp" / "read_image_server.py").is_file():
        errors.append("Missing read_image.mcp.read_image_server")
    if not (root / "skills" / "read-image" / "SKILL.md").is_file():
        errors.append("Missing skills/read-image/SKILL.md")

    if args.public:
        for path in _iter_text_files(root):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if KEY_PREFIX in text.lower():
                errors.append(f"Public copy contains API key text: {path}")
            if HARDCODED_ASSIGNMENT in text and HARDCODED_EMPTY not in text:
                errors.append(f"Public copy contains non-empty hardcoded key: {path}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print("Plugin validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
