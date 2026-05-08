import os
import sys
from pathlib import Path

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

# PyInstaller 打包后 sys._MEIPASS 指向解压目录
# 开发模式下从当前文件路径（server/app/main.py）上溯到项目根目录
_BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent))
WEB_DIST_DIR = str(_BASE_DIR / "web" / "dist")


def create_app() -> FastAPI:
    # 确保数据目录存在
    ensure_data_dirs()

    app = FastAPI(title="Geo Collab API", version="0.1.0")
    # CORS 仅允许本地开发服务器（桌面应用无跨域风险）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-Geo-Token"],
    )
    # 启动时恢复卡住的记录
    from server.app.db.session import SessionLocal
    from server.app.services.tasks import recover_stuck_records
    try:
        recover_db = SessionLocal()
        recover_stuck_records(recover_db)
        recover_db.close()
    except Exception:
        pass

    # 全局捕获 ValueError 返回 400
    @app.exception_handler(ValueError)
    async def _value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
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
