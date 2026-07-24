from __future__ import annotations

from server.app.modules.mcp_catalog.connect_router import MCP_TOOLS_COUNT


def test_mcp_tools_count_includes_video_tools():
    # 下限断言:视频 3 工具并入后至少 24。不要写死精确值——每加一个新 tool
    # 都会把这里炸红(2026-07-07 就因 "== 24" 撞上第 25 个工具红过一次 main);
    # 精确一致性由 /api/mcp/status 相关测试对照 MCP_TOOLS_COUNT 校验。
    assert MCP_TOOLS_COUNT >= 24


def test_video_routers_importable_and_paths():
    from server.app.modules.video.router import video_files_router, video_mcp_router

    mcp_paths = {r.path for r in video_mcp_router.routes}
    file_paths = {r.path for r in video_files_router.routes}
    assert "/compose" in mcp_paths
    assert "/status/{job_id}" in mcp_paths
    assert "/file/{job_id}" in file_paths
    assert "/srt/{job_id}" in file_paths
