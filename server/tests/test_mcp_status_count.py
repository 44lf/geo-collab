"""MCP tool 数守卫 + notify_review_card 注册验证。

test_mcp_tools_count_is_22：无 DB 依赖，只 import 常量。
test_notify_review_card_tool_registered：内省 FastMCP 真实注册表
`mcp._tool_manager._tools`（`server/mcp/server.py` 的启动断言、
`connect_router.py` 的 `tools_count` 都读同一个属性——已确认是这个
FastMCP 版本的真实内部结构，不是猜测），断言工具确实注册了，而不是
退化成一个只验证「模块能 import」的重言式断言。
"""

from __future__ import annotations


def test_mcp_tools_count_is_22():
    from server.app.modules.mcp_catalog.connect_router import MCP_TOOLS_COUNT

    assert MCP_TOOLS_COUNT == 22


def test_notify_review_card_tool_registered():
    import server.mcp.tools.action  # noqa: F401  触发注册
    from server.mcp.server import mcp

    assert "notify_review_card" in mcp._tool_manager._tools
