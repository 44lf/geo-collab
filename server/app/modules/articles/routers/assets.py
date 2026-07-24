"""素材（资产）上传、统计、清理与文件读取路由。"""

import os
from pathlib import Path
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from server.app.core.paths import get_data_dir
from server.app.core.security import get_current_user, require_admin
from server.app.db.session import get_db
from server.app.modules.articles.models import Asset
from server.app.modules.articles.schemas import AssetRead
from server.app.modules.articles.store import (
    asset_url,
    find_orphan_asset_ids,
    get_asset_stats,
    resolve_asset_path,
    soft_delete_assets,
    store_upload,
)
from server.app.modules.audit.service import add_audit_entry
from server.app.modules.system.models import User
from server.app.shared.errors import ClientError

assets_router = APIRouter()


# ── 资产辅助函数 ────────────────────────────────────────────────────────────


def resolve_asset_path_from_storage_key(storage_key: str) -> Path | None:
    """根据 storage_key 解析磁盘路径；路径逃逸时返回 None。"""
    try:
        data_dir = get_data_dir().resolve()
        path = (data_dir / storage_key).resolve()
        if data_dir != path and data_dir not in path.parents:
            return None
        return path
    except Exception:
        return None


def to_asset_read(asset: Asset) -> AssetRead:
    return AssetRead(
        id=asset.id,
        filename=asset.filename,
        ext=asset.ext,
        mime_type=asset.mime_type,
        size=asset.size,
        sha256=asset.sha256,
        storage_key=asset.storage_key,
        width=asset.width,
        height=asset.height,
        created_at=asset.created_at,
        url=asset_url(asset.id),
    )


# ── 资产路由 ────────────────────────────────────────────────────────────────


@assets_router.post("", response_model=AssetRead)
async def upload_asset(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    stored = await store_upload(db, current_user.id, file)
    return Response(
        content=to_asset_read(stored.asset).model_dump_json(),
        media_type="application/json",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@assets_router.get("/stats")
def asset_stats(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> dict:
    """磁盘资产统计（总量、孤儿数、已删除数、缩略图缓存大小）。"""
    return get_asset_stats(db)


@assets_router.post("/cleanup-orphans")
def cleanup_orphan_assets(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> dict:
    """将所有孤儿资产（未被任何文章引用）标记为逻辑删除。不删除磁盘文件。"""
    orphan_ids = find_orphan_asset_ids(db)
    marked = soft_delete_assets(db, orphan_ids)
    add_audit_entry(
        db,
        user=current_user,
        action="asset.cleanup_orphans",
        target_type="asset",
        target_id=None,
        payload={"deleted_count": marked},
        request=request,
    )
    return {"orphan_count": len(orphan_ids), "marked_deleted": marked}


@assets_router.get("/{asset_id}/meta", response_model=AssetRead)
def read_asset_meta(asset_id: str, db: Session = Depends(get_db)) -> AssetRead:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    return to_asset_read(asset)


@assets_router.get("/{asset_id}/thumbnail")
async def read_asset_thumbnail(
    asset_id: str,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    """获取资产缩略图，如果缩略图不存在则 302 重定向到原图"""
    asset = db.get(Asset, asset_id)
    if asset is None or asset.is_deleted:
        raise HTTPException(status_code=404)

    # 优先返回缩略图
    if asset.thumb_storage_key:
        thumb_path = resolve_asset_path_from_storage_key(asset.thumb_storage_key)
        if thumb_path and thumb_path.exists():
            if os.environ.get("GEO_NGINX_ACCEL"):
                rel = thumb_path.relative_to(get_data_dir())
                return Response(
                    status_code=200,
                    headers={
                        "X-Accel-Redirect": f"/internal_data/{rel}",
                        "Content-Type": "image/webp",
                        "Cache-Control": "public, max-age=31536000, immutable",
                    },
                )
            return FileResponse(str(thumb_path), media_type="image/webp")

    # 回退：缩略图不存在则 302 重定向到原图
    return RedirectResponse(url=f"/api/assets/{asset_id}", status_code=302)


@assets_router.get("/{asset_id}")
def read_asset_file(
    asset_id: str,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    """返回资产原图。Accept 带 image/webp 且有 webp 派生时改发 webp；GEO_NGINX_ACCEL 下走 X-Accel 卸载给 nginx。"""
    asset = db.get(Asset, asset_id)
    if asset is None or asset.is_deleted:
        raise HTTPException(status_code=404, detail="资源不存在")

    try:
        path = resolve_asset_path(asset)
    except (ClientError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if not path.exists():
        raise HTTPException(status_code=404, detail="资源文件不存在")

    # WebP 内容协商
    accept = request.headers.get("accept", "")
    mime_type = asset.mime_type
    if "image/webp" in accept and asset.webp_storage_key:
        webp_path = resolve_asset_path_from_storage_key(asset.webp_storage_key)
        if webp_path and webp_path.exists():
            path = webp_path
            mime_type = "image/webp"

    if os.environ.get("GEO_NGINX_ACCEL"):
        rel = path.relative_to(get_data_dir())
        filename_rfc5987 = quote(asset.filename.encode("utf-8"), safe="")
        return Response(
            status_code=200,
            headers={
                "X-Accel-Redirect": f"/internal_data/{rel}",
                "Content-Type": mime_type,
                "Content-Disposition": f"inline; filename*=UTF-8''{filename_rfc5987}",
                "Cache-Control": "public, max-age=31536000, immutable",
            },
        )

    filename_rfc5987 = quote(asset.filename.encode("utf-8"), safe="")
    return FileResponse(
        path,
        media_type=mime_type,
        filename=filename_rfc5987,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
