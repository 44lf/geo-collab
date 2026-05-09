"""
Geo Collab launcher — starts the local server and opens the browser.
Run directly:  python launcher.py
Bundle:        pyinstaller geo.spec  →  dist/GeoCollab.exe
"""
from __future__ import annotations

import logging
import os
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


def _show_chrome_missing_error() -> None:
    message = (
        "未检测到 Google Chrome 浏览器。\n\n"
        "Chrome 浏览器是账号登录和文章发布功能的必要依赖。\n"
        "请安装 Chrome 浏览器后重新启动 GeoCollab。\n\n"
        "下载地址：https://www.google.com/chrome/"
    )
    gui_shown = False
    try:
        import tkinter.messagebox
        try:
            import tkinter
            root = tkinter.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            tkinter.messagebox.showerror("GeoCollab — 缺少 Chrome", message)
            root.destroy()
            gui_shown = True
        except Exception:
            pass
    except ImportError:
        pass
    if gui_shown:
        sys.exit(1)
    raise RuntimeError(message)


def _ensure_chromium() -> None:
    if not _check_chrome():
        _show_chrome_missing_error()


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
    logging.info("Step 1: Running DB migrations...")
    _run_migrations(alembic_dir)

    logging.info("Step 2: Checking Chrome availability...")
    _ensure_chromium()

    if "GEO_LOCAL_API_TOKEN" not in os.environ:
        logging.info("Step 3: Initializing local token...")
        token = _read_or_generate_token(data_dir)
        os.environ["GEO_LOCAL_API_TOKEN"] = token

    port = _find_free_port()
    logging.info("Step 4: Starting server on port %d", port)

    threading.Thread(target=_open_browser, args=(port,), daemon=True).start()

    # Import app object directly so PyInstaller bundles the full server package.
    logging.info("Step 5: Importing server app...")
    from server.app.main import app  # noqa: PLC0415

    import uvicorn

    logging.info("Step 6: Starting uvicorn...")
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback as _tb
        crash_msg = _tb.format_exc()
        crash_log = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "GeoCollab" / "logs" / "crash.log"
        try:
            crash_log.parent.mkdir(parents=True, exist_ok=True)
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s %(levelname)s %(message)s",
                handlers=[logging.FileHandler(crash_log, encoding="utf-8")],
            )
            logging.critical("GeoCollab crashed on startup\n%s", crash_msg)
        except Exception:
            pass
        try:
            import tkinter.messagebox
            tkinter.messagebox.showerror(
                "GeoCollab — 启动失败",
                f"GeoCollab 启动时遇到错误，请查看日志：\n\n{crash_log}\n\n{crash_msg[-2000:]}",
            )
        except Exception:
            pass
        sys.exit(1)
