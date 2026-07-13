"""文章 / 文章分组业务逻辑兼容入口；具体实现按用例簇位于 services/。

历史上本模块是一个大 Service；现已按用例簇拆到 services/ 下：
  - services/articles.py     单篇文章 CRUD、封面、状态常量与校验
  - services/body_assets.py  正文素材同步 / 存在性校验
  - services/feed.py         列表 / 全文检索 / 内容 Feed
  - services/review.py       文章审核（通过 / 撤销）
  - services/groups.py       文章分组 CRUD + 整组审核
  - services/daily_groups.py 每日分组与流式追加

本文件仅显式重导出，保持 `articles.service` / `articles` 包入口的旧导入路径兼容。
禁止在此重新堆回业务实现（见 test_articles_module_contract 门禁）。
"""

from server.app.modules.articles.services.articles import (
    VALID_ARTICLE_STATUSES,
    VALID_REVIEW_STATUSES,
    create_article,
    delete_article,
    get_article,
    set_article_cover,
    update_article,
    validate_article_status,
)
from server.app.modules.articles.services.body_assets import (
    ensure_asset_exists,
    sync_article_body_assets,
)
from server.app.modules.articles.services.daily_groups import (
    append_article_to_group_pending,
    mark_pending_and_append_daily,
    mark_pending_and_group,
    resolve_or_create_daily_group,
)
from server.app.modules.articles.services.feed import (
    list_article_feed,
    list_articles,
    serialize_article_summaries,
)
from server.app.modules.articles.services.groups import (
    approve_group,
    compute_group_review_summary,
    create_group,
    delete_group,
    get_group,
    list_groups,
    replace_group_items,
    update_group,
)
from server.app.modules.articles.services.review import (
    approve_article,
    revoke_article_approval,
)

__all__ = [
    # 文章状态常量与校验（services/articles.py）
    "VALID_ARTICLE_STATUSES",
    "VALID_REVIEW_STATUSES",
    "validate_article_status",
    # 单篇文章 CRUD / 封面（services/articles.py）
    "get_article",
    "create_article",
    "update_article",
    "set_article_cover",
    "delete_article",
    # 正文素材（services/body_assets.py）
    "ensure_asset_exists",
    "sync_article_body_assets",
    # 列表 / 检索 / Feed（services/feed.py）
    "list_articles",
    "serialize_article_summaries",
    "list_article_feed",
    # 文章审核（services/review.py）
    "approve_article",
    "revoke_article_approval",
    # 文章分组（services/groups.py）
    "get_group",
    "list_groups",
    "create_group",
    "update_group",
    "replace_group_items",
    "delete_group",
    "compute_group_review_summary",
    "approve_group",
    # 每日分组与流式追加（services/daily_groups.py）
    "mark_pending_and_group",
    "mark_pending_and_append_daily",
    "resolve_or_create_daily_group",
    "append_article_to_group_pending",
]
