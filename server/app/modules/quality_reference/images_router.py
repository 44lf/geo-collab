"""qref 图片公开只读代理：GET /images/{id}。仿 stock-images/{id}/file。

嵌入参考正文的内链需公开可访问（reader 里 <img src> 由浏览器直接拉）。只按 id 读
MinIO 字节、不接受任意 key，避免越权读桶（见 plan §9）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from server.app.db.session import get_db
from server.app.modules.quality_reference import image_store
from server.app.shared.errors import ClientError

logger = logging.getLogger(__name__)

quality_reference_images_router = APIRouter()


@quality_reference_images_router.get("/quality-reference/images/{image_id}")
def serve_qref_image(image_id: int, db: Session = Depends(get_db)) -> Response:
    try:
        data, mime = image_store.read_image_bytes(db, image_id)
    except ClientError:
        raise HTTPException(status_code=404, detail="图片不存在") from None
    except Exception as exc:
        logger.exception("qref image proxy MinIO 读取失败: image_id=%s", image_id)
        raise HTTPException(status_code=502, detail=f"图片读取失败: {exc}") from exc
    return Response(content=data, media_type=mime or "image/jpeg")
