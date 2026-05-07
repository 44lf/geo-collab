"""
Geo Collab launcher — starts the local server and opens the browser.
Run directly:  python launcher.py
Bundle:        pyinstaller geo.spec  →  dist/GeoCollab.exe
"""
from __future__ import annotations

import logging
import socket
import sys
import threading
import time
import webbrowser
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


def _find_free_port(start: int = 8765) -> int:
    for port in range(start, start + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("No free port found in range 8765-8864")


def _open_browser(port: int, delay: float = 1.8) -> None:
    time.sleep(delay)
    url = f"http://127.0.0.1:{port}"
    logging.info("Opening browser: %s", url)
    webbrowser.open(url)


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


def _check_chrome() -> bool:
    import shutil

    chrome_candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "chrome",
        "google-chrome",
    ]
    for candidate in chrome_candidates:
        if Path(candidate).exists() or shutil.which(candidate):
            return True
    return False


def main() -> None:
    # Resolve project root — works both in dev and inside a PyInstaller bundle
    if hasattr(sys, "_MEIPASS"):
        project_root = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        project_root = Path(__file__).resolve().parent

    from server.app.core.paths import ensure_data_dirs

    data_dir = ensure_data_dirs()
    _setup_logging(data_dir / "logs" / "launcher.log")
    logging.info("Geo Collab starting — data dir: %s", data_dir)

    alembic_dir = project_root / "server" / "alembic"
    _run_migrations(alembic_dir)

    if not _check_chrome():
        logging.warning(
            "Google Chrome not found. Browser automation (account login / article publish) "
            "requires Chrome to be installed."
        )

    port = _find_free_port()
    logging.info("Starting server on port %d", port)

    threading.Thread(target=_open_browser, args=(port,), daemon=True).start()

    # Import app object directly so PyInstaller bundles the full server package.
    # Do NOT use the string form "server.app.main:app" — PyInstaller cannot trace
    # dynamic string imports and would leave the entire server out of the bundle.
    from server.app.main import app  # noqa: PLC0415

    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
