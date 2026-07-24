"""Plan B 标准游戏结构，字段对齐 GEO games 表（+ 应用宝能补的扩展字段）。

`adapter.to_types_game` 把它映射到 game_library.types.Game 喂 upsert_game：
  source/source_id → sources(JSON) 溯源；name/score/tags/platforms/comment_count/
  icon_url/screenshot_urls/description 一一对应；highlight_comments 落库时在返回 row 上补写
  （upsert_game 不写此字段）。
"""

from dataclasses import dataclass, field


@dataclass
class Game:
    source: str  # 数据源，恒 "yingyongbao"
    source_id: str  # 源内唯一 id：应用宝用 pkg_name
    name: str
    score: float | None = None  # -> games.score（应用宝 average_rating）
    tags: list = field(default_factory=list)  # -> game_tags（tags_st 的 tag_name）
    platforms: list = field(default_factory=list)  # -> games.platforms
    comment_count: int | None = None  # -> games.comment_count
    icon_url: str | None = None  # -> games.icon_url
    screenshot_urls: list = field(default_factory=list)  # -> games.screenshot_urls
    description: str | None = None  # -> games.description（需详情页补）
    category: str | None = None  # 应用宝 cate_name_new（额外维度）
    highlight_comments: list = field(default_factory=list)  # -> games.highlight_comments
    detail_fetched: bool = False  # 是否已进详情页补全
    source_url: str | None = None  # 溯源 URL
