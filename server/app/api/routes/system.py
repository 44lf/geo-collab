import shutil
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.app.db.session import get_db
from server.app.models import Account, Article, PublishTask
from server.app.schemas.system import SystemStatus
from server.app.services.system_status import get_system_status

router = APIRouter()

# Chrome 浏览器可能的位置
_CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "chrome",
    "google-chrome",
    "chromium",
    "chromium-browser",
    "/usr/bin/chromium-browser",
]


# 检测 Chrome 浏览器是否可访问
def _browser_ready() -> bool:
    return any(Path(c).exists() or shutil.which(c) for c in _CHROME_CANDIDATES)


# 获取系统运行状态
@router.get("/status", response_model=SystemStatus)
def read_system_status(db: Session = Depends(get_db)) -> SystemStatus:
    base = get_system_status()
    data = base.model_dump()
    try:
        data["article_count"] = db.scalar(select(func.count()).select_from(Article)) or 0
        data["account_count"] = db.scalar(select(func.count()).select_from(Account)) or 0
        data["task_count"] = db.scalar(select(func.count()).select_from(PublishTask)) or 0
    except Exception:
        # 数据库查询失败时返回 -1
        data["article_count"] = data["account_count"] = data["task_count"] = -1
    data["browser_ready"] = _browser_ready()
    return SystemStatus(**data)
