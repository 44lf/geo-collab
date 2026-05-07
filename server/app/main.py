import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
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

# In PyInstaller bundle sys._MEIPASS points to the extraction dir;
# in dev mode resolve from this file's location (server/app/main.py → project root)
_BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent))
WEB_DIST_DIR = str(_BASE_DIR / "web" / "dist")


def create_app() -> FastAPI:
    ensure_data_dirs()

    app = FastAPI(title="Geo Collab API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    @app.exception_handler(ValueError)
    async def _value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    app.include_router(accounts_router, prefix="/api/accounts", tags=["accounts"])
    app.include_router(article_groups_router, prefix="/api/article-groups", tags=["article-groups"])
    app.include_router(articles_router, prefix="/api/articles", tags=["articles"])
    app.include_router(assets_router, prefix="/api/assets", tags=["assets"])
    app.include_router(publish_records_router, prefix="/api/publish-records", tags=["publish-records"])
    app.include_router(system_router, prefix="/api/system", tags=["system"])
    app.include_router(tasks_router, prefix="/api/tasks", tags=["tasks"])

    try:
        app.mount("/assets", StaticFiles(directory=f"{WEB_DIST_DIR}/assets"), name="web-assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def serve_web_app(full_path: str) -> FileResponse:
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="API route not found")
            return FileResponse(f"{WEB_DIST_DIR}/index.html")

    except RuntimeError:
        pass

    return app


app = create_app()
