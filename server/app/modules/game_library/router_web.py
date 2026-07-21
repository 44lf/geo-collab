"""游戏库前台(user JWT)接口：浏览 + 图库导入 + 抓取配置/触发。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from server.app.core.security import get_current_user
from server.app.db.session import get_db
from server.app.modules.game_library import ingest_service, service
from server.app.modules.game_library.schemas import (
    GameDetail,
    GameIngestConfigPatch,
    GameIngestConfigRead,
    GameIngestRunStartResponse,
    GameListResponse,
    GameTagOut,
    ImageCategoryImportRequest,
    ImageCategoryImportResponse,
)

bg_session_factory: Any = None

game_library_web_router = APIRouter(
    prefix="/api/game-library",
    tags=["game-library"],
    dependencies=[Depends(get_current_user)],
)


@game_library_web_router.get("/tags", response_model=list[GameTagOut])
def web_list_game_tags(limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db)):
    return service.list_game_tags(db, limit=limit)


@game_library_web_router.get("/games", response_model=GameListResponse)
def web_list_games(
    tag: str | None = None,
    min_score: float | None = None,
    q: str | None = None,
    kind: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return service.list_games(
        db, tag=tag, min_score=min_score, q=q, kind=kind, limit=limit, offset=offset
    )


@game_library_web_router.get("/games/{game_id}", response_model=GameDetail)
def web_get_game(game_id: int, db: Session = Depends(get_db)):
    g = service.get_game(db, game_id)
    if g is None:
        raise HTTPException(status_code=404, detail="游戏不存在")
    return g


@game_library_web_router.post(
    "/import-image-categories", response_model=ImageCategoryImportResponse
)
def web_import_image_categories(payload: ImageCategoryImportRequest, db: Session = Depends(get_db)):
    from server.app.modules.game_library import importer

    result = importer.import_image_categories_as_games(
        db, kind=payload.kind, only_with_images=payload.only_with_images, limit=payload.limit
    )
    db.commit()
    return result


@game_library_web_router.get("/ingest/config", response_model=GameIngestConfigRead)
def web_get_ingest_config(db: Session = Depends(get_db)):
    from server.app.modules.game_library import scheduler

    cfg = ingest_service.get_or_create_ingest_config(db)
    db.commit()
    return ingest_service.ingest_config_to_dict(
        cfg, running=scheduler.is_configured_ingest_running()
    )


@game_library_web_router.patch("/ingest/config", response_model=GameIngestConfigRead)
def web_patch_ingest_config(payload: GameIngestConfigPatch, db: Session = Depends(get_db)):
    from server.app.modules.game_library import scheduler

    cfg = ingest_service.update_ingest_config(db, payload.model_dump(exclude_unset=True))
    db.commit()
    if cfg.enabled and bg_session_factory is not None:
        scheduler.start_game_ingest(bg_session_factory)
    return ingest_service.ingest_config_to_dict(
        cfg, running=scheduler.is_configured_ingest_running()
    )


@game_library_web_router.post(
    "/ingest/run", response_model=GameIngestRunStartResponse, status_code=202
)
def web_start_ingest_run(db: Session = Depends(get_db)):
    from server.app.modules.game_library import scheduler

    if bg_session_factory is None:
        raise HTTPException(status_code=503, detail="ingest executor 未就绪")
    if scheduler.is_configured_ingest_running():
        ingest_service.get_or_create_ingest_config(db)
        db.commit()
        raise HTTPException(status_code=409, detail="抓取正在进行中")
    started = scheduler.start_configured_ingest(bg_session_factory, trigger="manual")
    cfg = ingest_service.get_or_create_ingest_config(db)
    db.commit()
    return {
        "started": started,
        "status": ingest_service.ingest_config_to_dict(
            cfg, running=scheduler.is_configured_ingest_running()
        ),
    }
