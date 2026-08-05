from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from mcp.server.fastmcp import FastMCP
from playwright.async_api import (
    Browser,
    Page,
    Playwright,
    async_playwright,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)
from pydantic import Field

from omnimodal.errors import CapturePageError, ReadImageError, tr, trf
from omnimodal.logging import configure_logging
from omnimodal.mcp.common import EXTERNAL_SEND_ANNOTATIONS, run_cli
from omnimodal.paths import ensure_allowed_output_dir
from omnimodal.urls import validate_remote_url

mcp = FastMCP("omnimodal-capture-page")
logger = configure_logging("omnimodal-capture-page")

DEFAULT_VIEWPORT = "1280x800"
DEFAULT_TIMEOUT_SEC = 60
WaitUntil = Literal["commit", "domcontentloaded", "load", "networkidle"]
DEFAULT_WAIT_UNTIL: WaitUntil = "domcontentloaded"
DEFAULT_SETTLE_MS = 500
DEFAULT_MAX_FULL_PAGE_HEIGHT = 12000
VALID_ACTIONS = {"click", "hover", "scroll", "wait", "type", "press"}


def _parse_viewport(viewport: str) -> tuple[int, int]:
    raw = str(viewport or DEFAULT_VIEWPORT).strip().lower().replace("x", " ")
    parts = raw.split()
    if len(parts) != 2:
        raise CapturePageError(
            tr(
                f"viewport 格式应为 宽x高，例如 1280x800；收到：{viewport}",
                f"viewport must be WIDTHxHEIGHT, e.g. 1280x800; got: {viewport}",
            )
        )
    try:
        width = int(parts[0])
        height = int(parts[1])
    except ValueError as exc:
        raise CapturePageError(
            tr(
                f"viewport 必须包含两个数字，例如 1280x800；收到：{viewport}",
                f"viewport must contain two numbers, e.g. 1280x800; got: {viewport}",
            )
        ) from exc
    if width <= 0 or height <= 0:
        raise CapturePageError(
            tr(
                f"viewport 宽高必须大于 0；收到：{viewport}",
                f"viewport width/height must be positive; got: {viewport}",
            )
        )
    return width, height


def _wait_until() -> WaitUntil:
    value = os.environ.get("OMNIMODAL_CAPTURE_PAGE_WAIT_UNTIL", "").strip().lower()
    if value in {"commit", "domcontentloaded", "load", "networkidle"}:
        return cast(WaitUntil, value)
    return DEFAULT_WAIT_UNTIL


def _settle_ms() -> int:
    try:
        return max(0, int(os.environ.get("OMNIMODAL_CAPTURE_PAGE_SETTLE_MS", DEFAULT_SETTLE_MS)))
    except (TypeError, ValueError):
        return DEFAULT_SETTLE_MS


def _max_full_page_height() -> int:
    try:
        return max(
            1,
            int(
                os.environ.get(
                    "OMNIMODAL_CAPTURE_PAGE_MAX_FULL_PAGE_HEIGHT",
                    DEFAULT_MAX_FULL_PAGE_HEIGHT,
                )
            ),
        )
    except (TypeError, ValueError):
        return DEFAULT_MAX_FULL_PAGE_HEIGHT


def _normalize_actions(actions: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not actions:
        return []
    if not isinstance(actions, list):
        raise CapturePageError(
            tr("actions 必须是动作对象列表。", "actions must be a list of action objects.")
        )

    normalized: list[dict[str, Any]] = []
    for index, action in enumerate(actions, start=1):
        if not isinstance(action, dict):
            raise CapturePageError(
                tr(
                    f"第 {index} 个 action 必须是对象。",
                    f"Action {index} must be an object.",
                )
            )
        name = str(action.get("action", "")).strip().lower()
        if name not in VALID_ACTIONS:
            raise CapturePageError(
                tr(
                    f"第 {index} 个 action 不支持：{name}。可选："
                    + "、".join(sorted(VALID_ACTIONS)),
                    f"Action {index} is not supported: {name}. Choices: "
                    + ", ".join(sorted(VALID_ACTIONS)),
                )
            )

        item: dict[str, Any] = {"action": name}
        if name in {"click", "hover", "type"}:
            selector = action.get("selector")
            if not selector or not isinstance(selector, str):
                raise CapturePageError(
                    tr(
                        f"第 {index} 个 {name} action 缺少 selector。",
                        f"Action {index} ({name}) requires selector.",
                    )
                )
            item["selector"] = selector

        if name == "scroll":
            if action.get("selector") is not None:
                item["selector"] = str(action["selector"])
            try:
                amount = int(action.get("amount", 600))
            except (TypeError, ValueError) as exc:
                raise CapturePageError(
                    tr(
                        f"第 {index} 个 scroll action 的 amount 必须是整数。",
                        f"Action {index} scroll amount must be an integer.",
                    )
                ) from exc
            item["amount"] = amount

        if name == "wait":
            try:
                ms = int(action.get("ms", 300))
            except (TypeError, ValueError) as exc:
                raise CapturePageError(
                    tr(
                        f"第 {index} 个 wait action 的 ms 必须是整数。",
                        f"Action {index} wait ms must be an integer.",
                    )
                ) from exc
            item["ms"] = max(0, ms)

        if name == "type":
            text = action.get("text")
            if not isinstance(text, str):
                raise CapturePageError(
                    tr(
                        f"第 {index} 个 type action 缺少 text。",
                        f"Action {index} (type) requires text.",
                    )
                )
            item["text"] = text
            if action.get("press") is not None:
                item["press"] = str(action["press"])

        if name == "press":
            key = action.get("key")
            if not key or not isinstance(key, str):
                raise CapturePageError(
                    tr(
                        f"第 {index} 个 press action 缺少 key。",
                        f"Action {index} (press) requires key.",
                    )
                )
            item["key"] = key

        normalized.append(item)
    return normalized


async def _screenshot(page: Page, output_dir: Path, index: int) -> Path:
    height = await page.evaluate("() => document.documentElement.scrollHeight")
    viewport_only = height > _max_full_page_height()
    suffix = "-viewport-only" if viewport_only else ""
    path = output_dir / f"state-{index:02d}{suffix}.png"
    await page.screenshot(path=str(path), full_page=not viewport_only)
    return path


async def _launch_browser(playwright: Playwright) -> Browser:
    channel = os.environ.get("OMNIMODAL_CAPTURE_PAGE_BROWSER", "").strip().lower()
    if channel:
        return await playwright.chromium.launch(channel=channel, headless=True)
    if os.name == "nt":
        try:
            return await playwright.chromium.launch(channel="msedge", headless=True)
        except Exception:
            pass
    else:
        try:
            return await playwright.chromium.launch(channel="chrome", headless=True)
        except Exception:
            pass
    try:
        return await playwright.chromium.launch(headless=True)
    except Exception as exc:
        raise CapturePageError(
            tr(
                "无法启动浏览器。可先运行：uv run --project . --with playwright "
                "playwright install chromium，或设置 OMNIMODAL_CAPTURE_PAGE_BROWSER "
                "使用已安装的 Chrome/Edge 通道。",
                "Cannot launch browser. Run: uv run --project . --with playwright "
                "playwright install chromium, or set OMNIMODAL_CAPTURE_PAGE_BROWSER to a "
                "Chrome/Edge channel.",
            )
        ) from exc


async def _apply_action(page: Page, action: dict[str, Any], timeout_ms: int) -> None:
    name = action["action"]
    if name == "click":
        await page.locator(action["selector"]).first.click(timeout=timeout_ms)
    elif name == "hover":
        await page.locator(action["selector"]).first.hover(timeout=timeout_ms)
    elif name == "scroll":
        if "selector" in action:
            locator = page.locator(action["selector"]).first
            await locator.scroll_into_view_if_needed(timeout=timeout_ms)
            await locator.evaluate(
                "(el, amount) => el.scrollBy(0, amount)",
                action["amount"],
            )
        else:
            await page.mouse.wheel(0, action["amount"])
    elif name == "wait":
        await page.wait_for_timeout(action["ms"])
    elif name == "type":
        locator = page.locator(action["selector"]).first
        await locator.click(timeout=timeout_ms)
        await locator.fill(action["text"])
        if "press" in action:
            await locator.press(action["press"])
    elif name == "press":
        await page.keyboard.press(action["key"])


def _finalize_capture_dir(path: Path) -> None:
    """处理临时截图目录：成功时保留交付物，失败时清理，并清理过期旧目录。"""
    try:
        has_files = any(path.iterdir()) if path.exists() else False
    except OSError:
        has_files = False
    if not has_files:
        shutil.rmtree(path, ignore_errors=True)
    stale_before = time.time() - 24 * 3600
    try:
        for entry in Path(tempfile.gettempdir()).glob("omnimodal-capture-*"):
            try:
                if entry.is_dir() and entry.stat().st_mtime < stale_before:
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                continue
    except OSError:
        pass


async def _capture_page(
    url: str,
    actions: list[dict[str, Any]] | None,
    viewport: str,
    output_dir: str | None,
) -> str:
    width, height = _parse_viewport(viewport)
    normalized_actions = _normalize_actions(actions)
    try:
        await asyncio.to_thread(validate_remote_url, url)
    except ReadImageError as exc:
        raise CapturePageError(str(exc)) from exc
    timeout_ms = max(
        1000,
        int(os.environ.get("OMNIMODAL_CAPTURE_PAGE_TIMEOUT_SEC", DEFAULT_TIMEOUT_SEC)) * 1000,
    )

    cleanup_dir: Path | None = None
    if output_dir:
        try:
            output_path = await asyncio.to_thread(ensure_allowed_output_dir, output_dir)
        except ReadImageError as exc:
            raise CapturePageError(str(exc)) from exc
    else:
        cleanup_dir = output_path = Path(tempfile.mkdtemp(prefix="omnimodal-capture-"))

    try:
        try:
            async with async_playwright() as playwright:
                browser = await _launch_browser(playwright)
                try:
                    page = await browser.new_page(viewport={"width": width, "height": height})
                    await page.goto(
                        url,
                        wait_until=_wait_until(),
                        timeout=timeout_ms,
                    )
                    await page.wait_for_timeout(_settle_ms())
                    paths = [await _screenshot(page, output_path, 1)]
                    for index, action in enumerate(normalized_actions, start=2):
                        await _apply_action(page, action, timeout_ms)
                        paths.append(await _screenshot(page, output_path, index))
                finally:
                    await browser.close()
            return "\n".join(f"{index}. {path}" for index, path in enumerate(paths, start=1))
        except CapturePageError:
            raise
        except PlaywrightTimeoutError as exc:
            raise CapturePageError(
                trf("网页操作超时：{exc}", "Web page operation timed out: {exc}", exc=exc)
            ) from exc
        except Exception as exc:
            raise CapturePageError(
                trf("网页截图失败：{exc}", "Web page capture failed: {exc}", exc=exc)
            ) from exc
    finally:
        # 临时输出目录（未传 output_dir 时创建）：截图文件是交付物不能立即删；
        # 失败时目录不完整可删；无论成败都顺带清理 24h 前的旧临时目录防累积。
        if cleanup_dir is not None:
            _finalize_capture_dir(cleanup_dir)


@mcp.tool(name="omnimodal_capture_page", annotations=EXTERNAL_SEND_ANNOTATIONS)
async def omnimodal_capture_page(
    url: Annotated[str, Field(description="要打开并截图的网页 URL。")],
    actions: Annotated[
        list[dict[str, Any]] | None,
        Field(
            description=(
                "可选交互动作列表：click/hover/scroll/wait/type/press。"
                '例如 [{"action":"click","selector":"#menu"}]。'
            )
        ),
    ] = None,
    viewport: Annotated[
        str,
        Field(description="浏览器视口，例如 1280x800。"),
    ] = DEFAULT_VIEWPORT,
    output_dir: Annotated[
        str | None,
        Field(description="截图输出目录；未传时使用临时目录。"),
    ] = None,
) -> str:
    """用 Playwright 打开网页，执行交互并在每个状态截取全页 PNG。"""
    return await _capture_page(url, actions, viewport, output_dir)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture interactive webpage states as PNG files.")
    parser.add_argument("--url", required=True, help="Webpage URL to open.")
    parser.add_argument(
        "--actions-json",
        default=None,
        help="JSON list of interaction actions.",
    )
    parser.add_argument("--viewport", default=DEFAULT_VIEWPORT)
    parser.add_argument("--output-dir", default=None)
    return parser


def _run_cli_handler(args: argparse.Namespace) -> int:
    actions: list[dict[str, Any]] | None = None
    if args.actions_json:
        parsed = json.loads(args.actions_json)
        if not isinstance(parsed, list):
            raise CapturePageError(
                tr(
                    "--actions-json 必须是 JSON 数组。",
                    "--actions-json must be a JSON array.",
                )
            )
        actions = parsed
    print(asyncio.run(_capture_page(args.url, actions, args.viewport, args.output_dir)))
    return 0


def main() -> None:
    if len(sys.argv) > 1:
        sys.exit(run_cli(_build_parser(), _run_cli_handler))
    else:
        mcp.run()


if __name__ == "__main__":
    main()
