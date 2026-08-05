from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SECRET_KEY_RE = re.compile(
    r"(?i)\b(ark|sk)-[a-z0-9_-]{12,}\b|"
    r"api[_-]?key\s*[:=]\s*['\"]?[a-z0-9_-]{20,}"
)
EXPECTED_VERSION = "3.1.0"
EXPECTED_MCP_SERVERS = {
    "omnimodal-recognize",
    "omnimodal-capture-page",
    "omnimodal-windows-capture",
    "omnimodal-generation",
}


def _iter_text_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(
            part
            in {
                ".git",
                "__pycache__",
                ".venv",
                ".pytest_cache",
                ".mypy_cache",
                ".ruff_cache",
            }
            for part in relative.parts
        ):
            continue
        if path.suffix.lower() in {".pyc", ".png", ".jpg", ".jpeg", ".mp4", ".mp3", ".pyo"}:
            continue
        yield path


def _check_mcp_json(path: Path, errors: list[str]) -> None:
    if not path.is_file():
        errors.append(f"Missing {path}")
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        servers = data.get("mcpServers", {})
        if not EXPECTED_MCP_SERVERS.issubset(servers):
            errors.append(f"{path} must include servers {sorted(EXPECTED_MCP_SERVERS)}")
        if path.name == ".mcp.json":
            recognize_env = set(servers.get("omnimodal-recognize", {}).get("env_vars", []))
            for required in {
                "OMNIMODAL_API_KEY",
                "OMNIMODAL_ENV_FILE",
                "OMNIMODAL_PROVIDER",
                "OMNIMODAL_IMAGE_MODEL",
                "OMNIMODAL_VIDEO_MODEL",
                "OMNIMODAL_AUDIO_MODEL_STANDARD",
                "OMNIMODAL_ALLOWED_OUTPUT_DIRS",
                "OMNIMODAL_ALLOW_PRIVATE_URLS",
            }:
                if required not in recognize_env:
                    errors.append(f".mcp.json recognize server missing env {required}")
    except json.JSONDecodeError as exc:
        errors.append(f"{path} is invalid JSON: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate omnimodal plugin layout.")
    parser.add_argument("--root", default=None, help="Plugin repository root.")
    parser.add_argument(
        "--public",
        action="store_true",
        help="Also enforce that no API key is present.",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    errors: list[str] = []

    for manifest_name in (".codex-plugin/plugin.json", ".claude-plugin/plugin.json"):
        manifest_path = root / manifest_name
        if not manifest_path.is_file():
            errors.append(f"Missing {manifest_name}")
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("name") != "omnimodal":
                errors.append(f"{manifest_name} name must be omnimodal")
            if manifest.get("version") != EXPECTED_VERSION:
                errors.append(f"{manifest_name} version must be {EXPECTED_VERSION}")
            author = manifest.get("author", {})
            if isinstance(author, dict) and author.get("name") != "good-boy4069":
                errors.append(f"{manifest_name} author must be good-boy4069")
            if (
                manifest_name == ".codex-plugin/plugin.json"
                and manifest.get("interface", {}).get("developerName") != "good-boy4069"
            ):
                errors.append(".codex-plugin developerName must be good-boy4069")
        except json.JSONDecodeError as exc:
            errors.append(f"{manifest_name} is invalid JSON: {exc}")

    _check_mcp_json(root / ".mcp.json", errors)
    _check_mcp_json(root / ".claude-mcp.json", errors)

    if not (root / "config" / "model_catalog.json").is_file():
        errors.append("Missing config/model_catalog.json")
    if not (root / "config" / "profiles.json").is_file():
        errors.append("Missing config/profiles.json")
    if not (root / "omnimodal" / "mcp" / "read_image_server.py").is_file():
        errors.append("Missing omnimodal.mcp.read_image_server")
    if not (root / "omnimodal" / "mcp" / "generation_server.py").is_file():
        errors.append("Missing omnimodal.mcp.generation_server")
    if not (root / "skills" / "omnimodal" / "SKILL.md").is_file():
        errors.append("Missing skills/omnimodal/SKILL.md")
    if not (root / "CLAUDE.md").is_file():
        errors.append("Missing CLAUDE.md")
    if not (root / "README.md").is_file() or not (root / "README.en.md").is_file():
        errors.append("Missing README.md or README.en.md")
    if not (root / "docs" / "demo-source.png").is_file():
        errors.append("Missing docs/demo-source.png")
    if not (root / "docs" / "demo-output.txt").is_file():
        errors.append("Missing docs/demo-output.txt")

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8")
        if 'version = "3.1.0"' not in text:
            errors.append("pyproject.toml version must be 3.1.0")
        for script in (
            "omnimodal-recognize",
            "omnimodal-generation",
            "omnimodal-capture-page",
            "omnimodal-windows-capture",
        ):
            if f"{script} =" not in text:
                errors.append(f"pyproject.toml missing script {script}")
    else:
        errors.append("Missing pyproject.toml")

    init_path = root / "omnimodal" / "__init__.py"
    if init_path.is_file() and '__version__ = "3.1.0"' not in init_path.read_text(encoding="utf-8"):
        errors.append("omnimodal/__init__.py version must be 3.1.0")

    readme_text = (root / "README.md").read_text(encoding="utf-8", errors="replace")
    if r"C:\Users\admin" in readme_text or "C:/Users/admin" in readme_text:
        errors.append("README.md must not contain local machine absolute paths")

    gitignore = root / ".gitignore"
    if gitignore.is_file():
        ignore_text = gitignore.read_text(encoding="utf-8")
        if ".env" not in ignore_text or "config/local.json" not in ignore_text:
            errors.append(".gitignore must ignore .env and config/local.json")

    if args.public:
        for path in _iter_text_files(root):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if path.name != ".env.example" and SECRET_KEY_RE.search(text):
                errors.append(f"Public copy contains API key text: {path}")
            if path.name == ".env":
                errors.append("Public copy must not contain .env")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print("Plugin validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
