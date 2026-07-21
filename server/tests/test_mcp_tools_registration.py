"""MCP 工具注册守卫：list_skills 已注册 + 注册总数 ≥ MCP_TOOLS_COUNT。"""


def test_list_skills_tool_registered():
    import server.mcp.tools.action  # noqa: F401  触发注册
    import server.mcp.tools.catalog  # noqa: F401
    from server.mcp.server import mcp

    names = set(mcp._tool_manager._tools.keys())
    assert "list_skills" in names
    assert "install_loop_skills" in names


def test_registered_count_meets_floor():
    import server.mcp.tools.action  # noqa: F401
    import server.mcp.tools.catalog  # noqa: F401
    import server.mcp.tools.meta  # noqa: F401
    from server.app.modules.mcp_catalog.connect_router import MCP_TOOLS_COUNT
    from server.mcp.server import mcp

    assert len(mcp._tool_manager._tools) >= MCP_TOOLS_COUNT
    assert MCP_TOOLS_COUNT == 35


def test_search_articles_by_title_tool_registered():
    import server.mcp.tools.catalog  # noqa: F401  触发注册
    from server.mcp.server import mcp

    assert "search_articles_by_title" in mcp._tool_manager._tools
