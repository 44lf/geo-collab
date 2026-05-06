from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.app.api.routes.accounts import router as accounts_router
from server.app.api.routes.article_groups import router as article_groups_router
from server.app.api.routes.articles import router as articles_router
from server.app.api.routes.assets import router as assets_router
from server.app.api.routes.system import router as system_router
from server.app.core.paths import ensure_data_dirs

WEB_DIST_DIR = "web/dist"


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
    app.include_router(accounts_router, prefix="/api/accounts", tags=["accounts"])
    app.include_router(article_groups_router, prefix="/api/article-groups", tags=["article-groups"])
    app.include_router(articles_router, prefix="/api/articles", tags=["articles"])
    app.include_router(assets_router, prefix="/api/assets", tags=["assets"])
    app.include_router(system_router, prefix="/api/system", tags=["system"])

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
