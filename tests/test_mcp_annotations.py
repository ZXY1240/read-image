from __future__ import annotations

import asyncio

from omnimodal.mcp.capture_page_server import mcp as capture_page_mcp
from omnimodal.mcp.read_image_server import mcp as read_image_mcp
from omnimodal.mcp.windows_capture_server import mcp as windows_capture_mcp


def _collect_annotations(mcp_servers: list[object]) -> dict[str, tuple[bool, bool, bool]]:
    async def collect() -> dict[str, tuple[bool, bool, bool]]:
        result: dict[str, tuple[bool, bool, bool]] = {}
        for server in mcp_servers:
            for tool in await server.list_tools():  # type: ignore[attr-defined]
                result[tool.name] = (
                    tool.annotations.readOnlyHint,
                    tool.annotations.openWorldHint,
                    tool.annotations.destructiveHint,
                )
        return result

    return asyncio.run(collect())


def test_mcp_tool_annotations_are_explicit() -> None:
    annotations = _collect_annotations([read_image_mcp, capture_page_mcp, windows_capture_mcp])

    assert annotations == {
        "omnimodal_recognize_image": (False, True, False),
        "omnimodal_recognize_images_batch": (False, True, False),
        "omnimodal_recognize_video": (False, True, False),
        "omnimodal_recognize_videos_batch": (False, True, False),
        "omnimodal_recognize_audio": (False, True, False),
        "omnimodal_recognize_audios_batch": (False, True, False),
        "omnimodal_read_clipboard_image": (False, True, False),
        "omnimodal_read_dragged_image": (False, True, False),
        "omnimodal_read_dragged_video": (False, True, False),
        "omnimodal_read_dragged_audio": (False, True, False),
        "omnimodal_capture_page": (False, True, False),
        "omnimodal_list_windows": (True, False, False),
        "omnimodal_capture_windows": (False, False, False),
    }
