"""图片库 MCP 端点：search-web-image（第三层配图兜底）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from server.app.core.mcp_auth import require_mcp_token
from server.app.core.mcp_errors import mcp_exception_response
from server.app.db.session import get_db
from server.app.modules.image_library.service import search_and_store_web_image

image_mcp_router = APIRouter(dependencies=[Depends(require_mcp_token)])


class SearchWebImageReq(BaseModel):
    keyword: str


@image_mcp_router.post("/search-web-image")
def search_web_image(req: SearchWebImageReq, db: Session = Depends(get_db)) -> dict:
    try:
        res = search_and_store_web_image(db, req.keyword)
    except HTTPException:
        raise
    except Exception as exc:
        raise mcp_exception_response(exc, context=f"search_web_image kw={req.keyword}") from exc
    if res is None:
        return {"ok": True, "data": {"url": None, "stock_image_id": None}, "error": None}
    url, sid = res
    return {"ok": True, "data": {"url": url, "stock_image_id": sid}, "error": None}
