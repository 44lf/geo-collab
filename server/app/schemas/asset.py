from datetime import datetime

from pydantic import BaseModel


# 资源文件响应体
class AssetRead(BaseModel):
    id: str
    filename: str
    ext: str
    mime_type: str
    size: int
    sha256: str
    storage_key: str  # 相对 data_dir 的存储路径
    width: int | None
    height: int | None
    created_at: datetime
    url: str  # 可访问的 API URL

    model_config = {"from_attributes": True}

