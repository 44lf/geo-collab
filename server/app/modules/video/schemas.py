"""Storyboard 结构校验（Claude 写、GEO 渲染的契约）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from server.app.modules.image_library.models import StockImage
from server.app.shared.errors import ValidationError


class Shot(BaseModel):
    subtitle: str = Field(min_length=1, max_length=200)
    narration: str = Field(min_length=1, max_length=1000)
    asset_id: int | None = None
    duration_hint: float | None = Field(default=None, ge=0.5, le=30.0)


class Storyboard(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=5000)
    tags: list[str] = Field(default_factory=list)
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
    bgm: Literal["default", "none"] = "default"
    shots: list[Shot] = Field(min_length=1, max_length=30)


class ComposeVideoRequest(BaseModel):
    article_id: int
    storyboard: Storyboard
    engine: str | None = None
    model_label: str | None = None


def validate_asset_ids(db: Session, storyboard: Storyboard) -> None:
    """任一 shot.asset_id 指向的图不存在 → ValidationError（service 层命名异常）。"""
    wanted = {s.asset_id for s in storyboard.shots if s.asset_id is not None}
    if not wanted:
        return
    found = {
        row[0] for row in db.query(StockImage.id).filter(StockImage.id.in_(wanted)).all()
    }
    missing = wanted - found
    if missing:
        raise ValidationError(f"storyboard 引用了不存在的图片 asset_id: {sorted(missing)}")
