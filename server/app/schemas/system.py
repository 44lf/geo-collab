from pydantic import BaseModel


class SystemStatus(BaseModel):
    service: str
    version: str
    data_dir: str
    database_path: str
    directories_ready: bool
    article_count: int = 0
    account_count: int = 0
    task_count: int = 0
    browser_ready: bool = False
