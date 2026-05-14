"""
Remote browser session management for the Linux server deployment.

The runtime pipeline is Xvfb -> x11vnc -> websockify -> noVNC, with
Playwright Chromium attached to the X display. The system is deployed on Linux
servers, so this module does not need platform-specific branching.
"""
from __future__ import annotations

import re
import logging
import shutil
import socket
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Iterator

from server.app.core.config import get_settings
from server.app.core.paths import get_data_dir

_logger = logging.getLogger(__name__)


@dataclass
class ManagedProcess:
    name: str
    process: subprocess.Popen
    log_handle: BinaryIO


@dataclass
class RemoteBrowserSession:
    id: str
    account_key: str
    display_number: int
    display: str
    vnc_port: int
    novnc_port: int
    novnc_url: str
    log_dir: Path
    processes: list[ManagedProcess] = field(default_factory=list, repr=False)
    playwright: Any | None = field(default=None, repr=False)
    browser_context: Any | None = field(default=None, repr=False)
    page: Any | None = field(default=None, repr=False)
    operation_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    started_at: float = field(default_factory=time.monotonic)  # 用于空闲超时判断


_sessions_lock = threading.Lock()
_active_sessions: dict[str, RemoteBrowserSession] = {}
_reserved_displays: set[int] = set()
_reserved_vnc_ports: set[int] = set()
_reserved_novnc_ports: set[int] = set()

# 人工介入（waiting_user_input）时的关联记录：
# record_id → session_id — 知道哪个 record 关联到哪个远程浏览器
_record_to_session: dict[int, str] = {}
# session_ids that survive context manager exit（不随 finally 清理）
_session_keep_alive: set[str] = set()

_idle_cleanup_thread: threading.Thread | None = None
_idle_cleanup_stop = threading.Event()


def associate_record_with_session(record_id: int, session_id: str) -> None:
    """将发布记录关联到远程浏览器 session（用于 waiting_user_input 场景）。"""
    with _sessions_lock:
        _record_to_session[record_id] = session_id


def get_session_for_record(record_id: int) -> RemoteBrowserSession | None:
    """根据 record_id 查找关联的远程浏览器 session。"""
    session_id = _record_to_session.get(record_id)
    if session_id is None:
        return None
    return get_session(session_id)


def get_session(session_id: str) -> RemoteBrowserSession | None:
    """通过 session_id 查找活跃的远程浏览器 session。"""
    with _sessions_lock:
        return _active_sessions.get(session_id)


def attach_browser_handles(session_id: str, playwright: Any | None, context: Any | None, page: Any | None = None) -> None:
    """Attach Playwright handles so session shutdown can close Chromium too."""
    with _sessions_lock:
        session = _active_sessions.get(session_id)
        if session is None:
            raise RuntimeError(f"Remote browser session not found: {session_id}")
        session.playwright = playwright
        session.browser_context = context
        session.page = page


def disassociate_record(record_id: int) -> None:
    """取消 record 与 session 的关联（record 完成/取消时调用）。"""
    with _sessions_lock:
        _record_to_session.pop(record_id, None)


def keep_session_alive(session_id: str) -> None:
    """标记 session 为"保持存活"，context manager exit 时不自动 stop。"""
    with _sessions_lock:
        _session_keep_alive.add(session_id)


def active_remote_browser_sessions() -> list[RemoteBrowserSession]:
    with _sessions_lock:
        return list(_active_sessions.values())


def remote_browser_runtime_status() -> dict[str, object]:
    settings = get_settings()
    required = {
        "xvfb": _resolve_command(settings.publish_xvfb_path),
        "x11vnc": _resolve_command(settings.publish_x11vnc_path),
        "websockify": _resolve_command(settings.publish_websockify_path),
    }
    novnc_web_dir = settings.publish_novnc_web_dir
    novnc_web_ready = True
    if novnc_web_dir:
        novnc_web_ready = Path(novnc_web_dir).exists()
    return {
        "enabled": True,
        "ready": all(required.values()) and novnc_web_ready,
        "active_sessions": len(active_remote_browser_sessions()),
        "tools": {name: bool(path) for name, path in required.items()},
        "novnc_web_ready": novnc_web_ready,
    }


@contextmanager
def managed_remote_browser_session(account_key: str) -> Iterator[RemoteBrowserSession | None]:
    """
    上下文管理器：进入时启动远程浏览器 session（Xvfb + x11vnc + websockify），
    正常情况下退出时自动停机清理。

    特殊情况：如果 session 被 keep_session_alive() 标记（waiting_user_input 场景），
    则 exit 时不 stop，由调用方（cancel_task / resolve_user_input_record）显式清理。
    """
    session = start_remote_browser_session(account_key)
    try:
        yield session
    finally:
        with _sessions_lock:
            keep = session.id not in _session_keep_alive
        if keep:
            stop_remote_browser_session(session.id)


def start_remote_browser_session(account_key: str) -> RemoteBrowserSession:
    settings = get_settings()

    xvfb = _require_command(settings.publish_xvfb_path, "Xvfb")
    x11vnc = _require_command(settings.publish_x11vnc_path, "x11vnc")
    websockify = _require_command(settings.publish_websockify_path, "websockify")
    if settings.publish_novnc_web_dir and not Path(settings.publish_novnc_web_dir).exists():
        raise RuntimeError(f"noVNC web dir not found: {settings.publish_novnc_web_dir}")

    display_number, vnc_port, novnc_port = _reserve_numbers()
    safe_account_key = re.sub(r"[^a-zA-Z0-9_-]+", "-", account_key).strip("-") or "account"
    session_id = uuid.uuid4().hex[:12]
    log_dir = get_data_dir() / "logs" / "browser-sessions" / f"{safe_account_key}-{session_id}"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        _release_reserved_numbers(display_number, vnc_port, novnc_port)
        raise

    session = RemoteBrowserSession(
        id=session_id,
        account_key=account_key,
        display_number=display_number,
        display=f":{display_number}",
        vnc_port=vnc_port,
        novnc_port=novnc_port,
        novnc_url=_novnc_url(settings.publish_remote_browser_host, novnc_port),
        log_dir=log_dir,
    )

    try:
        session.processes.append(
            _spawn(
                "xvfb",
                [
                    xvfb,
                    session.display,
                    "-screen",
                    "0",
                    "1440x900x24",
                    "-ac",
                    "+extension",
                    "GLX",
                    "+render",
                    "-noreset",
                ],
                log_dir,
            )
        )
        _wait_for_x_display(session.display_number, settings.publish_remote_browser_start_timeout_seconds)

        session.processes.append(
            _spawn(
                "x11vnc",
                [
                    x11vnc,
                    "-display",
                    session.display,
                    "-localhost",
                    "-forever",
                    "-shared",
                    "-nopw",
                    "-rfbport",
                    str(session.vnc_port),
                ],
                log_dir,
            )
        )
        _wait_for_port("127.0.0.1", session.vnc_port, settings.publish_remote_browser_start_timeout_seconds)

        websockify_command = [websockify]
        if settings.publish_novnc_web_dir:
            websockify_command.append(f"--web={settings.publish_novnc_web_dir}")
        websockify_command.extend(
            [
                f"{settings.publish_remote_browser_host}:{session.novnc_port}",
                f"127.0.0.1:{session.vnc_port}",
            ]
        )
        session.processes.append(_spawn("websockify", websockify_command, log_dir))
        _wait_for_port(
            settings.publish_remote_browser_host,
            session.novnc_port,
            settings.publish_remote_browser_start_timeout_seconds,
        )

        with _sessions_lock:
            _active_sessions[session.id] = session
            _reserved_displays.discard(session.display_number)
            _reserved_vnc_ports.discard(session.vnc_port)
            _reserved_novnc_ports.discard(session.novnc_port)
        return session
    except Exception:
        _stop_session_processes(session)
        _release_reserved_numbers(display_number, vnc_port, novnc_port)
        raise


def stop_remote_browser_session(session_id: str) -> None:
    with _sessions_lock:
        session = _active_sessions.pop(session_id, None)
        _session_keep_alive.discard(session_id)
        stale_records = [rid for rid, sid in _record_to_session.items() if sid == session_id]
        for rid in stale_records:
            _record_to_session.pop(rid, None)
    if session is not None:
        _close_browser_handles(session)
        _stop_session_processes(session)


def _reserve_numbers() -> tuple[int, int, int]:
    settings = get_settings()
    with _sessions_lock:
        used_displays = {session.display_number for session in _active_sessions.values()} | _reserved_displays
        used_vnc_ports = {session.vnc_port for session in _active_sessions.values()} | _reserved_vnc_ports
        used_novnc_ports = {session.novnc_port for session in _active_sessions.values()} | _reserved_novnc_ports

        display_number = _find_display_number(settings.publish_remote_browser_display_base, used_displays)
        vnc_port = _find_free_port(
            "127.0.0.1",
            settings.publish_remote_browser_vnc_base_port,
            used_vnc_ports,
        )
        novnc_port = _find_free_port(
            settings.publish_remote_browser_host,
            settings.publish_remote_browser_novnc_base_port,
            used_novnc_ports,
        )
        _reserved_displays.add(display_number)
        _reserved_vnc_ports.add(vnc_port)
        _reserved_novnc_ports.add(novnc_port)
        return display_number, vnc_port, novnc_port


def _release_reserved_numbers(display_number: int, vnc_port: int, novnc_port: int) -> None:
    with _sessions_lock:
        _reserved_displays.discard(display_number)
        _reserved_vnc_ports.discard(vnc_port)
        _reserved_novnc_ports.discard(novnc_port)


def _find_display_number(base: int, used: set[int]) -> int:
    for display_number in range(base, base + 1000):
        if display_number in used:
            continue
        socket_path = Path(f"/tmp/.X11-unix/X{display_number}")
        if socket_path.exists():
            continue
        return display_number
    raise ValueError("No free X display number available")


def _find_free_port(host: str, base: int, used: set[int]) -> int:
    for port in range(base, base + 1000):
        if port in used:
            continue
        if _port_available(host, port):
            return port
    raise ValueError(f"No free TCP port available from {base}")


def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _spawn(name: str, command: list[str], log_dir: Path) -> ManagedProcess:
    log_handle = (log_dir / f"{name}.log").open("ab")
    try:
        process = subprocess.Popen(command, stdout=log_handle, stderr=subprocess.STDOUT)
    except Exception:
        log_handle.close()
        raise
    return ManagedProcess(name=name, process=process, log_handle=log_handle)


def _stop_session_processes(session: RemoteBrowserSession) -> None:
    for managed in reversed(session.processes):
        process = managed.process
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        finally:
            try:
                managed.log_handle.close()
            except Exception:
                pass


def _close_browser_handles(session: RemoteBrowserSession) -> None:
    with session.operation_lock:
        if session.browser_context is not None:
            try:
                session.browser_context.close()
            except Exception:
                pass
        if session.playwright is not None:
            try:
                session.playwright.stop()
            except Exception:
                pass
        session.browser_context = None
        session.playwright = None
        session.page = None


def _wait_for_x_display(display_number: int, timeout_seconds: float) -> None:
    socket_path = Path(f"/tmp/.X11-unix/X{display_number}")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if socket_path.exists():
            return
        time.sleep(0.1)
    raise ValueError(f"Xvfb display did not become ready: :{display_number}")


def _wait_for_port(host: str, port: int, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            try:
                sock.connect((host, port))
                return
            except OSError:
                time.sleep(0.1)
    raise ValueError(f"Port did not become ready: {host}:{port}")


def _require_command(command: str, label: str) -> str:
    resolved = _resolve_command(command)
    if not resolved:
        raise ValueError(f"{label} command not found: {command}")
    return resolved


def _resolve_command(command: str | None) -> str | None:
    if not command:
        return None
    path = Path(command)
    if path.is_absolute():
        return str(path) if path.exists() else None
    return shutil.which(command)


def _start_idle_cleanup() -> None:
    """启动后台线程，定期清理超时的 keep-alive session。"""
    global _idle_cleanup_thread
    if _idle_cleanup_thread is not None and _idle_cleanup_thread.is_alive():
        return

    def _cleanup_loop():
        timeout = lambda: get_settings().publish_remote_browser_idle_timeout_seconds
        while not _idle_cleanup_stop.is_set():
            _idle_cleanup_stop.wait(30)  # 每 30 秒检查一次
            if _idle_cleanup_stop.is_set():
                break
            try:
                _cleanup_stale_sessions(timeout())
            except Exception:
                pass
            try:
                _cleanup_zombie_sessions()
            except Exception:
                pass

    _idle_cleanup_thread = threading.Thread(target=_cleanup_loop, daemon=True, name="session-idle-cleanup")
    _idle_cleanup_thread.start()


def _cleanup_stale_sessions(idle_timeout: int) -> None:
    """清理超过 idle_timeout 秒未操作的 keep-alive session。"""
    now = time.monotonic()
    stale_ids: list[str] = []
    with _sessions_lock:
        for session_id in list(_session_keep_alive):
            session = _active_sessions.get(session_id)
            if session is None:
                _session_keep_alive.discard(session_id)
                continue
            if now - session.started_at > idle_timeout:
                stale_ids.append(session_id)
                _session_keep_alive.discard(session_id)

    for session_id in stale_ids:
        try:
            stop_remote_browser_session(session_id)
        except Exception:
            _logger.warning("Failed to stop stale session %s", session_id, exc_info=True)
        # 解除所有关联 record 的绑定
        with _sessions_lock:
            stale_records = [rid for rid, sid in _record_to_session.items() if sid == session_id]
            for rid in stale_records:
                _record_to_session.pop(rid, None)


def _cleanup_zombie_sessions() -> None:
    """检测并清理进程已意外退出的僵尸 session。"""
    zombie_ids: list[str] = []
    with _sessions_lock:
        for session_id, session in list(_active_sessions.items()):
            for mp in session.processes:
                if mp.process.poll() is not None:
                    zombie_ids.append(session_id)
                    break

    for session_id in zombie_ids:
        _logger.warning("Zombie session detected (process exited): %s", session_id)
        try:
            stop_remote_browser_session(session_id)
        except Exception:
            _logger.warning("Failed to stop zombie session %s", session_id, exc_info=True)
        with _sessions_lock:
            stale_records = [rid for rid, sid in _record_to_session.items() if sid == session_id]
            for rid in stale_records:
                _record_to_session.pop(rid, None)


def _stop_idle_cleanup() -> None:
    """停止后台清理线程（测试用）。"""
    global _idle_cleanup_thread
    _idle_cleanup_stop.set()
    if _idle_cleanup_thread is not None:
        _idle_cleanup_thread.join(timeout=3)
        _idle_cleanup_thread = None


def _novnc_url(host: str, port: int) -> str:
    return f"http://{host}:{port}/vnc.html?host={host}&port={port}"

def _reset_globals() -> None:
    """Reset all module-level state (for test cleanup)."""
    global _active_sessions, _record_to_session, _session_keep_alive
    global _reserved_displays, _reserved_vnc_ports, _reserved_novnc_ports
    with _sessions_lock:
        _active_sessions.clear()
        _record_to_session.clear()
        _session_keep_alive.clear()
        _reserved_displays.clear()
        _reserved_vnc_ports.clear()
        _reserved_novnc_ports.clear()
