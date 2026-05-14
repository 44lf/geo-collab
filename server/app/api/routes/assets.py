import os

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from sqlalchemy.orm import Session

from server.app.core.paths import get_data_dir
from server.app.core.security import get_current_user
from server.app.db.session import get_db
from server.app.models import Asset, User
from server.app.schemas.asset import AssetRead
from server.app.services.assets import asset_url, resolve_asset_path, store_upload

router = APIRouter()


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


@router.post("", response_model=AssetRead)
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


@router.get("/{asset_id}/meta", response_model=AssetRead)
def read_asset_meta(asset_id: str, db: Session = Depends(get_db)) -> AssetRead:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return to_asset_read(asset)


@router.get("/{asset_id}")
def read_asset_file(asset_id: str, width: int | None = None, db: Session = Depends(get_db)) -> Response:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")

    try:
        path = resolve_asset_path(asset)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if not path.exists():
        raise HTTPException(status_code=404, detail="Asset file not found")

    if width is not None and asset.mime_type.startswith("image/"):
        cache_dir = get_data_dir() / "thumbnail_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{asset_id}_w{width}.jpg"

        if not cache_file.exists():
            with Image.open(path) as img:
                ratio = width / img.width
                new_height = int(img.height * ratio)
                img = img.resize((width, new_height), Image.LANCZOS)
                img = img.convert("RGB")
                img.save(cache_file, "JPEG", quality=85)

        return FileResponse(
            cache_file,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    if os.environ.get("GEO_NGINX_ACCEL"):
        rel = path.relative_to(get_data_dir())
        return Response(
            status_code=200,
            headers={
                "X-Accel-Redirect": f"/internal_data/{rel}",
                "Content-Type": asset.mime_type,
                "Content-Disposition": f'inline; filename="{asset.filename}"',
            },
        )

    return FileResponse(path, media_type=asset.mime_type, filename=asset.filename)
