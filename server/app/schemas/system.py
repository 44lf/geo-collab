from pydantic import BaseModel


class SystemStatus(BaseModel):
    service: str
    version: str
    data_dir: str
    database_path: str
    directories_ready: bool

