"""游戏库 MCP-token 只读检索端点。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from server.app.core.mcp_auth import require_mcp_token
from server.app.core.mcp_errors import mcp_exception_response
from server.app.db.session import get_db
from server.app.modules.game_library import service
from server.app.modules.game_library.schemas import GameCard, GameTagOut, QueryGamesRequest

game_library_mcp_router = APIRouter(
    prefix="/api/mcp/game-library",
    tags=["mcp-game-library"],
    dependencies=[Depends(require_mcp_token)],
)


@game_library_mcp_router.get("/tags", response_model=list[GameTagOut])
def mcp_list_game_tags(limit: int = 200, db: Session = Depends(get_db)) -> list[dict]:
    try:
        return service.list_game_tags(db, limit=limit)
    except Exception as exc:
        raise mcp_exception_response(exc, context="list_game_tags") from exc


@game_library_mcp_router.post("/query", response_model=list[GameCard])
def mcp_query_games(req: QueryGamesRequest, db: Session = Depends(get_db)) -> list[dict]:
    try:
        return service.query_games_by_tags(
            db,
            relevant_tags=req.relevant_tags,
            diversity_tags=req.diversity_tags,
            exclude_tags=req.exclude_tags,
            min_score=req.min_score,
            limit=req.limit,
        )
    except Exception as exc:
        raise mcp_exception_response(exc, context="query_games_by_tags") from exc
