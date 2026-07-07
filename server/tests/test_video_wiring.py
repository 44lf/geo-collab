from __future__ import annotations

from server.app.modules.mcp_catalog.connect_router import MCP_TOOLS_COUNT


def test_mcp_tools_count_is_24():
    assert MCP_TOOLS_COUNT == 24


def test_video_routers_importable_and_paths():
    from server.app.modules.video.router import video_files_router, video_mcp_router

    mcp_paths = {r.path for r in video_mcp_router.routes}
    file_paths = {r.path for r in video_files_router.routes}
    assert "/compose" in mcp_paths
    assert "/status/{job_id}" in mcp_paths
    assert "/file/{job_id}" in file_paths
    assert "/srt/{job_id}" in file_paths
