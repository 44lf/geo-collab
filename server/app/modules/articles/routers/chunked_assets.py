"""分块上传路由。"""

import logging
from typing import Any

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from server.app.core.security import get_current_user
from server.app.db.session import get_db
from server.app.modules.articles.store import (
    _create_asset_from_path,
    guess_image_size,
    normalize_ext,
)
from server.app.modules.articles.uploader import (
    CHUNK_SIZE,
    MAGIC_BYTES_CHECK_SIZE,
    get_upload_manager,
)
from server.app.modules.system.models import User

from .assets import to_asset_read

chunked_assets_router = APIRouter()

_logger = logging.getLogger(__name__)


# ── 分块上传请求模型 ────────────────────────────────────────────────────────


class ChunkedUploadStartRequest(BaseModel):
    total_size: int
    file_hash: str | None = None  # 已弃用：仅为旧客户端保留。


class ChunkedUploadCompleteRequest(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"


# ── 分块资产路由 ────────────────────────────────────────────────────────────


@chunked_assets_router.post("/upload-start")
async def start_chunked_upload(
    payload: ChunkedUploadStartRequest | None = Body(default=None),
    total_size: int | None = Query(default=None),
    file_hash: str | None = Query(default=None),  # noqa: ARG001
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """初始化分块上传。"""
    from server.app.core.config import MAX_ASSET_BYTES

    if payload is not None:
        total_size = payload.total_size
    if total_size is None:
        raise HTTPException(status_code=422, detail="请提供文件大小")
    if total_size <= 0:
        raise HTTPException(status_code=400, detail="文件不能为空")
    if total_size > MAX_ASSET_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件超过 {MAX_ASSET_BYTES // (1024 * 1024)}MB 限制",
        )

    manager = get_upload_manager()
    session = manager.init_session(total_size)

    return {
        "upload_id": session.upload_id,
        "chunk_size": CHUNK_SIZE,
        "chunk_count": session.chunk_count,
    }


@chunked_assets_router.post("/upload-chunk/{upload_id}")
async def upload_chunk(
    upload_id: str,
    chunk_index: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """上传单个分块。"""
    manager = get_upload_manager()
    session = manager.get_session(upload_id)

    if not session:
        raise HTTPException(status_code=404, detail="上传会话不存在")

    if chunk_index < 0 or chunk_index >= session.chunk_count:
        raise HTTPException(status_code=400, detail="无效的分块索引")

    chunk_data = await file.read()

    if chunk_index < session.chunk_count - 1:
        if len(chunk_data) != CHUNK_SIZE:
            raise HTTPException(status_code=400, detail="分块大小不正确")
    else:
        expected_last_size = session.total_size - (session.chunk_count - 1) * CHUNK_SIZE
        if len(chunk_data) != expected_last_size:
            raise HTTPException(status_code=400, detail="最后一个分块大小不正确")

    await manager.save_chunk(upload_id, chunk_index, chunk_data)

    return {"status": "ok"}


@chunked_assets_router.post("/upload-status/{upload_id}")
async def get_upload_status(
    upload_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """获取上传进度。"""
    manager = get_upload_manager()
    session = manager.get_session(upload_id)

    if not session:
        raise HTTPException(status_code=404, detail="上传会话不存在")

    uploaded = manager.get_uploaded_chunks(upload_id)

    return {
        "chunk_count": session.chunk_count,
        "uploaded_chunks": sorted(list(uploaded)),
        "is_complete": manager.is_complete(upload_id),
    }


@chunked_assets_router.post("/upload-complete/{upload_id}")
async def complete_chunked_upload(
    upload_id: str,
    payload: ChunkedUploadCompleteRequest | None = Body(default=None),
    filename: str | None = Query(default=None),
    content_type: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """完成分块上传，合并所有分块并创建资源。"""
    if payload is not None:
        filename = payload.filename
        content_type = payload.content_type
    if filename is None:
        raise HTTPException(status_code=422, detail="请提供文件名")
    content_type = content_type or "application/octet-stream"

    manager = get_upload_manager()
    session = manager.get_session(upload_id)

    if not session:
        raise HTTPException(status_code=404, detail="上传会话不存在")

    if not manager.is_complete(upload_id):
        raise HTTPException(status_code=400, detail="文件尚未上传完毕")

    try:
        import asyncio

        loop = asyncio.get_event_loop()
        merged_path, sha256_hash, is_valid_format, format_error = await loop.run_in_executor(
            None, manager.merge_chunks, upload_id
        )

        if not is_valid_format:
            merged_path.unlink()
            raise HTTPException(status_code=415, detail=format_error or "Unsupported file type")

        file_header = merged_path.read_bytes()[:MAGIC_BYTES_CHECK_SIZE]

        ext = normalize_ext(filename, content_type, file_header)
        width, height = guess_image_size(file_header)

        stored = await loop.run_in_executor(
            None,
            _create_asset_from_path,
            db,
            current_user.id,
            merged_path,
            filename,
            content_type,
            sha256_hash,
            session.total_size,
            ext,
            width,
            height,
            True,
        )

        return to_asset_read(stored.asset).model_dump()

    except HTTPException:
        # 必须重新抛出（如 415），不要包成 500（见 CLAUDE.md「complete_chunked_upload」约束）
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        try:
            manager.cleanup_session(upload_id)
        except Exception:
            _logger.warning("Failed to cleanup chunked upload session %s", upload_id, exc_info=True)
