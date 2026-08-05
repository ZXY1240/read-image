from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

SECRET_KEY_RE = re.compile(
    r"(?i)\b(ark|sk)-[a-z0-9_-]{12,}\b|"
    r"api[_-]?key\s*[:=]\s*['\"]?[a-z0-9_-]{20,}"
)

IGNORE_PATTERNS = shutil.ignore_patterns(
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".worktrees",
    "worktrees",
    ".env",
    ".env.local",
    "config/local.json",
    "*.egg-info",
    "*.pyc",
)


def _scan_for_secrets(root: Path) -> list[str]:
    findings: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in {".pyc", ".png", ".jpg", ".jpeg", ".mp4", ".pyo"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if path.name != ".env.example" and SECRET_KEY_RE.search(text):
            findings.append(str(path.relative_to(root)))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a public release copy without API keys.")
    parser.add_argument(
        "--source",
        default=None,
        help="Private source repo. Defaults to this repository root.",
    )
    parser.add_argument("--output", required=True, help="Public release directory.")
    args = parser.parse_args()

    source = Path(args.source).resolve() if args.source else Path(__file__).resolve().parents[1]
    output = Path(args.output).expanduser().resolve()

    if output.exists() and any(output.iterdir()):
        print(
            f"ERROR: output directory must be empty or non-existent: {output}",
            file=sys.stderr,
        )
        return 1

    output.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, output, dirs_exist_ok=True, ignore=IGNORE_PATTERNS)

    for sensitive in (
        output / ".env",
        output / ".env.local",
        output / ".coverage",
        output / "config" / "local.json",
    ):
        if sensitive.is_file():
            sensitive.unlink()

    config_path = output / "omnimodal" / "config.py"
    if config_path.is_file() and "HARDCODED_API_KEY" in config_path.read_text(encoding="utf-8"):
        print(
            "ERROR: public config.py must not contain HARDCODED_API_KEY",
            file=sys.stderr,
        )
        return 1

    findings = _scan_for_secrets(output)
    if findings:
        for finding in findings:
            print(f"ERROR: secret found in {finding}", file=sys.stderr)
        return 1

    print(f"Public release prepared at {output}")
    print("Verified: no API key in public release.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
