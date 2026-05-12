"""
Geo Collab API 应用工厂与全局配置。

入口点：
  - 开发: uvicorn server.app.main:app --reload
  - 打包: PyInstaller geo.spec → launcher.py → create_app()

阅读顺序建议：
  1. create_app() → 了解路由注册、全局异常处理、启动行为
  2. models/publish.py → PublishTask / PublishRecord 状态机
  3. services/tasks.py → 任务执行引擎
  4. services/toutiao_publisher.py → 头条浏览器自动化
"""
import os
import sys
from datetime import datetime
from pathlib import Path

# ── datetime 序列化补丁 ──
# 在 Pydantic 模型定义之前安装，确保所有 naive datetime 输出带 "Z" 后缀
# 这样前端 new Date("2026-05-12T14:00:00Z") 能正确识别为 UTC
# 涉及三个层级：Pydantic 模型、FastAPI 内置编码器、FastAPI 构造函数

from pydantic import BaseModel

_orig_init_subclass = BaseModel.__init_subclass__


def _init_subclass_patch(cls, **kwargs):
    _orig_init_subclass(**kwargs)
    if cls.__name__ == "BaseModel":
        return
    encoders = dict(cls.model_config.get("json_encoders", {}))
    encoders.setdefault(
        datetime,
        lambda dt: dt.isoformat() + ("Z" if dt.tzinfo is None else ""),
    )
    cls.model_config["json_encoders"] = encoders


BaseModel.__init_subclass__ = classmethod(_init_subclass_patch)

import fastapi.encoders

fastapi.encoders.ENCODERS_BY_TYPE[datetime] = lambda dt: dt.isoformat() + ("Z" if dt.tzinfo is None else "")

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from server.app.api.routes.accounts import router as accounts_router
from server.app.api.routes.article_groups import router as article_groups_router
from server.app.api.routes.articles import router as articles_router
from server.app.api.routes.assets import router as assets_router
from server.app.api.routes.publish_records import router as publish_records_router
from server.app.api.routes.system import router as system_router
from server.app.api.routes.tasks import router as tasks_router
from server.app.core.paths import ensure_data_dirs
from server.app.core.security import require_local_token
from server.app.services.errors import AccountError, ConflictError, ValidationError

# PyInstaller 打包后 sys._MEIPASS 指向解压目录
# 开发模式下从当前文件路径（server/app/main.py）上溯到项目根目录
_BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent))
WEB_DIST_DIR = str(_BASE_DIR / "web" / "dist")


def create_app() -> FastAPI:
    # 确保数据目录存在（assets/ browser_states/ logs/ exports/）
    ensure_data_dirs()

    from server.app.services.browser_sessions import _start_idle_cleanup
    _start_idle_cleanup()

    app = FastAPI(
        title="Geo Collab API",
        version="0.1.0",
        json_encoders={
            datetime: lambda dt: dt.isoformat() + ("Z" if dt.tzinfo is None else "")
        },
    )
    # CORS 仅允许本地开发服务器（桌面应用无跨域风险）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-Geo-Token"],
    )
    # 启动时恢复卡住的记录（上次运行时 crash 导致 status='running' 的记录）
    from server.app.db.session import SessionLocal
    from server.app.services.tasks import recover_stuck_records
    try:
        recover_db = SessionLocal()
        recover_stuck_records(recover_db)
        recover_db.close()
    except Exception:
        pass

    # 全局异常处理：业务层统一 raise ValueError → 400
    @app.exception_handler(ValueError)
    async def _value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    # ConflictError(ValueError) 有更具体的含义 → 409，优先于 ValueError 处理器
    @app.exception_handler(ConflictError)
    async def _conflict_error_handler(request: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ValidationError)
    async def _validation_error_handler(request: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(AccountError)
    async def _account_error_handler(request: Request, exc: AccountError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    # 无鉴权端点：前端启动时拉取本次会话 token
    @app.get("/api/bootstrap", include_in_schema=False)
    async def bootstrap() -> dict:
        return {"token": os.environ.get("GEO_LOCAL_API_TOKEN", "")}

    # 注册 7 个 API 路由模块（全部需要本地 token 鉴权）
    app.include_router(accounts_router, prefix="/api/accounts", tags=["accounts"], dependencies=[Depends(require_local_token)])
    app.include_router(article_groups_router, prefix="/api/article-groups", tags=["article-groups"], dependencies=[Depends(require_local_token)])
    app.include_router(articles_router, prefix="/api/articles", tags=["articles"], dependencies=[Depends(require_local_token)])
    app.include_router(assets_router, prefix="/api/assets", tags=["assets"], dependencies=[Depends(require_local_token)])
    app.include_router(publish_records_router, prefix="/api/publish-records", tags=["publish-records"], dependencies=[Depends(require_local_token)])
    app.include_router(system_router, prefix="/api/system", tags=["system"], dependencies=[Depends(require_local_token)])
    app.include_router(tasks_router, prefix="/api/tasks", tags=["tasks"], dependencies=[Depends(require_local_token)])

    try:
        # 挂载前端静态文件（Vite 构建产物）
        app.mount("/assets", StaticFiles(directory=f"{WEB_DIST_DIR}/assets"), name="web-assets")

        # SPA 兜底路由：所有非 API 路径返回 index.html
        @app.get("/{full_path:path}", include_in_schema=False)
        def serve_web_app(full_path: str) -> FileResponse:
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="API route not found")
            return FileResponse(f"{WEB_DIST_DIR}/index.html")

    except RuntimeError:
        # 开发模式下 static 目录可能不存在，静默跳过
        pass

    return app


# 模块级 app 实例，uvicorn 直接引用
app = create_app()
