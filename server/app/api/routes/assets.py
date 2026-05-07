from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from server.app.db.session import get_db
from server.app.models import Asset
from server.app.schemas.asset import AssetRead
from server.app.services.assets import asset_url, resolve_asset_path, store_upload

router = APIRouter()


# 将 ORM Asset 转为响应体
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


# 上传资源文件（图片等）
@router.post("", response_model=AssetRead)
async def upload_asset(file: UploadFile = File(...), db: Session = Depends(get_db)) -> AssetRead:
    stored = await store_upload(db, file)
    return to_asset_read(stored.asset)


# 获取资源元数据
@router.get("/{asset_id}/meta", response_model=AssetRead)
def read_asset_meta(asset_id: str, db: Session = Depends(get_db)) -> AssetRead:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return to_asset_read(asset)


# 获取资源文件内容（返回图片二进制数据）
@router.get("/{asset_id}")
def read_asset_file(asset_id: str, db: Session = Depends(get_db)) -> FileResponse:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")

    try:
        path = resolve_asset_path(asset)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if not path.exists():
        raise HTTPException(status_code=404, detail="Asset file not found")

    return FileResponse(path, media_type=asset.mime_type, filename=asset.filename)
