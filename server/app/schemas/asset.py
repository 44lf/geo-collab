from datetime import datetime

from pydantic import BaseModel


class AssetRead(BaseModel):
    id: str
    filename: str
    ext: str
    mime_type: str
    size: int
    sha256: str
    storage_key: str
    width: int | None
    height: int | None
    created_at: datetime
    url: str

    model_config = {"from_attributes": True}

