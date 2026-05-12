from __future__ import annotations

import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Iterator

from server.app.core.config import get_settings
from server.app.core.paths import get_data_dir


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


_sessions_lock = threading.Lock()
_active_sessions: dict[str, RemoteBrowserSession] = {}
_reserved_displays: set[int] = set()
_reserved_vnc_ports: set[int] = set()
_reserved_novnc_ports: set[int] = set()

# record_id → session_id mapping for waiting_user_input records
_record_to_session: dict[int, str] = {}
_session_keep_alive: set[str] = set()


def associate_record_with_session(record_id: int, session_id: str) -> None:
    with _sessions_lock:
        _record_to_session[record_id] = session_id


def get_session_for_record(record_id: int) -> RemoteBrowserSession | None:
    session_id = _record_to_session.get(record_id)
    if session_id is None:
        return None
    return get_session(session_id)


def get_session(session_id: str) -> RemoteBrowserSession | None:
    with _sessions_lock:
        return _active_sessions.get(session_id)


def disassociate_record(record_id: int) -> None:
    with _sessions_lock:
        _record_to_session.pop(record_id, None)


def keep_session_alive(session_id: str) -> None:
    with _sessions_lock:
        _session_keep_alive.add(session_id)


def remote_browser_enabled() -> bool:
    return bool(get_settings().publish_remote_browser_enabled)


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
        "enabled": remote_browser_enabled(),
        "ready": all(required.values()) and novnc_web_ready,
        "active_sessions": len(active_remote_browser_sessions()),
        "tools": {name: bool(path) for name, path in required.items()},
        "novnc_web_ready": novnc_web_ready,
    }


@contextmanager
def managed_remote_browser_session(account_key: str) -> Iterator[RemoteBrowserSession | None]:
    if not remote_browser_enabled():
        yield None
        return

    session = start_remote_browser_session(account_key)
    try:
        yield session
    finally:
        if session.id not in _session_keep_alive:
            stop_remote_browser_session(session.id)


def start_remote_browser_session(account_key: str) -> RemoteBrowserSession:
    settings = get_settings()
    _ensure_linux_runtime()

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
    if session is not None:
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
    raise RuntimeError("No free X display number available")


def _find_free_port(host: str, base: int, used: set[int]) -> int:
    for port in range(base, base + 1000):
        if port in used:
            continue
        if _port_available(host, port):
            return port
    raise RuntimeError(f"No free TCP port available from {base}")


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


def _wait_for_x_display(display_number: int, timeout_seconds: float) -> None:
    socket_path = Path(f"/tmp/.X11-unix/X{display_number}")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if socket_path.exists():
            return
        time.sleep(0.1)
    raise RuntimeError(f"Xvfb display did not become ready: :{display_number}")


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
    raise RuntimeError(f"Port did not become ready: {host}:{port}")


def _require_command(command: str, label: str) -> str:
    resolved = _resolve_command(command)
    if not resolved:
        raise RuntimeError(f"{label} command not found: {command}")
    return resolved


def _resolve_command(command: str | None) -> str | None:
    if not command:
        return None
    path = Path(command)
    if path.is_absolute():
        return str(path) if path.exists() else None
    return shutil.which(command)


def _ensure_linux_runtime() -> None:
    if _is_windows_runtime():
        raise RuntimeError("Remote browser sessions require a Linux runtime")


def _is_windows_runtime() -> bool:
    return sys.platform == "win32"


def _novnc_url(host: str, port: int) -> str:
    return f"http://{host}:{port}/vnc.html?host={host}&port={port}"
