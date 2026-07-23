"""多平台游戏分类搜索的统一数据结构与常量。

Game 的字段基于 baidu_category.py 已验证过的真实响应设计,不是猜的。
GameSource 只是 Protocol,给类型提示用,各平台模块不强制继承它。
"""

from dataclasses import dataclass, field
from typing import Protocol

SOURCE_BAIDU = "baidu"
SOURCE_NINEGAME = "ninegame"
SOURCE_YINGYONGBAO = "yingyongbao"

ORDER_HOT = "hot"
ORDER_NEW = "new"

PLATFORM_ANDROID = "android"
PLATFORM_IOS = "ios"


@dataclass
class Game:
    source: str
    game_id: str
    name: str
    score: float | None = None
    tags: list = field(default_factory=list)
    platforms: list = field(default_factory=list)
    comment_count: int | None = None
    icon_url: str | None = None
    screenshot_urls: list = field(default_factory=list)
    android_package: str | None = None  # baidu 响应无此字段,恒为 None（备未来源填充）
    description: str | None = None
    raw: dict = field(default_factory=dict)


class GameSource(Protocol):
    """约定接口(仅类型提示,不强制继承)。"""

    CATEGORIES: dict
    FETCH_MODE: str  # "http_direct" | "browser_intercept",纯文档用途

    def search(
        self,
        category,
        *,
        platform=None,
        order=ORDER_HOT,
        page=1,
        page_size=20,
    ) -> list: ...
