from __future__ import annotations

from server.mcp.server import mcp


def test_video_tools_registered():
    names = set(mcp._tool_manager._tools.keys())
    assert {"compose_video", "get_video_status", "list_stock_images"} <= names


def test_total_tools_at_least_24():
    assert len(mcp._tool_manager._tools) >= 24
