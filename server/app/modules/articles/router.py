"""Articles 路由兼容入口；具体端点按职责位于 routers/。"""

from .routers.articles import articles_router
from .routers.assets import assets_router
from .routers.chunked_assets import chunked_assets_router
from .routers.groups import article_groups_router
from .routers.mcp import articles_mcp_router

__all__ = [
    "articles_router",
    "article_groups_router",
    "assets_router",
    "chunked_assets_router",
    "articles_mcp_router",
]
