"""
Geo Collab launcher — Docker entrypoint.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def _setup_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.FileHandler(log_file, encoding="utf-8")]
    try:
        if sys.stdout and sys.stdout.isatty():
            handlers.append(logging.StreamHandler(sys.stdout))
    except Exception:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def _run_migrations(alembic_dir: Path) -> None:
    from server.app.core.paths import get_database_url

    from alembic import command
    from alembic.config import Config

    logging.info("Running DB migrations from %s", alembic_dir)
    cfg = Config()
    cfg.set_main_option("script_location", str(alembic_dir))
    cfg.set_main_option("sqlalchemy.url", get_database_url())
    cfg.set_main_option("prepend_sys_path", ".")
    command.upgrade(cfg, "head")
    logging.info("DB migrations complete")


def _read_or_generate_token(data_dir: Path) -> str:
    import secrets

    token_path = data_dir / "local_token.txt"
    if token_path.exists():
        token = token_path.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_hex(32)
    token_path.write_text(token, encoding="utf-8")
    return token


def main() -> None:
    if hasattr(sys, "_MEIPASS"):
        project_root = Path(sys._MEIPASS)
    else:
        project_root = Path(__file__).resolve().parent

    from server.app.core.paths import ensure_data_dirs
    data_dir = ensure_data_dirs()
    _setup_logging(data_dir / "logs" / "launcher.log")
    logging.info("Geo Collab starting — data dir: %s", data_dir)

    alembic_dir = project_root / "server" / "alembic"
    _run_migrations(alembic_dir)

    from server.app.main import app
    import asyncio
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    config = uvicorn.Config(app, host=host, port=port, log_config=None)
    server = uvicorn.Server(config)
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()
