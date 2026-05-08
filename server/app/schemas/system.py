from pydantic import BaseModel


# 系统状态响应体
class SystemStatus(BaseModel):
    service: str
    directories_ready: bool
    article_count: int = 0
    account_count: int = 0
    task_count: int = 0
    browser_ready: bool = False  # 是否检测到 Chrome 浏览器
