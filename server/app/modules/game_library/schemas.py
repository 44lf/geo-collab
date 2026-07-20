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
