from pydantic import BaseModel, Field


class GameTagOut(BaseModel):
    tag: str
    game_count: int


class GameCard(BaseModel):
    game_id: int
    name: str
    score: float | None = None
    tags: list[str] = Field(default_factory=list)
    description: str | None = None
    stock_category_id: int | None = None
    icon_url: str | None = None
    screenshot_urls: list[str] = Field(default_factory=list)
    highlight_comments: list | None = None
    related_hotspots: list | None = None
    use_count: int = 0
    last_used_at: str | None = None


class QueryGamesRequest(BaseModel):
    relevant_tags: list[str] = Field(min_length=1)
    diversity_tags: list[str] | None = None
    exclude_tags: list[str] | None = None
    min_score: float | None = None
    limit: int = Field(default=20, ge=1, le=100)


class GameIngestConfigRead(BaseModel):
    enabled: bool
    window_start: str
    window_end: str
    batch_size: int
    min_gap_seconds: int
    max_gap_seconds: int
    source_order: str
    max_shots: int
    running: bool = False
    last_run_started_at: str | None = None
    last_run_finished_at: str | None = None
    last_run_summary: dict | None = None
    last_run_trigger: str | None = None


class GameIngestConfigPatch(BaseModel):
    enabled: bool | None = None
    window_start: str | None = None
    window_end: str | None = None
    batch_size: int | None = Field(default=None, ge=1)
    min_gap_seconds: int | None = Field(default=None, ge=1)
    max_gap_seconds: int | None = Field(default=None, ge=1)
    source_order: str | None = None
    max_shots: int | None = Field(default=None, ge=1)


class GameListItem(BaseModel):
    game_id: int
    name: str
    score: float | None = None
    tags: list[str] = Field(default_factory=list)
    icon_url: str | None = None
    screenshot_count: int = 0
    use_count: int = 0
    last_used_at: str | None = None
    stock_category_id: int | None = None
    sources: list[str] = Field(default_factory=list)
    kind: str | None = None


class GameListResponse(BaseModel):
    items: list[GameListItem] = Field(default_factory=list)
    total: int = 0


class GameDetail(BaseModel):
    game_id: int
    name: str
    name_normalized: str
    score: float | None = None
    comment_count: int | None = None
    tags: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    sources: list = Field(default_factory=list)
    icon_url: str | None = None
    screenshot_urls: list[str] = Field(default_factory=list)
    description: str | None = None
    stock_category_id: int | None = None
    kind: str | None = None
    use_count: int = 0
    last_used_at: str | None = None
    last_used_article_id: int | None = None
    first_seen_at: str | None = None
    last_verified_at: str | None = None
    highlight_comments: list | None = None
    related_hotspots: list | None = None
    is_active: bool = True
