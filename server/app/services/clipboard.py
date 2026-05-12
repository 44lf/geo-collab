"""Windows 剪贴板操作（CF_HDROP 格式），用于正文图片粘贴。"""
from __future__ import annotations

import ctypes
import struct
import sys
import time
from ctypes import wintypes
from pathlib import Path


def set_clipboard_files(paths: list[Path]) -> None:
    """将文件路径写入 Windows 剪贴板（CF_HDROP），模拟资源管理器复制文件。"""
    if sys.platform != "win32":
        raise OSError("Clipboard file paste only supported on Windows")

    absolute_paths = [str(path.resolve()) for path in paths]
    if not absolute_paths:
        raise OSError("Clipboard file list is empty")

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL

    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL

    cf_hdrop = 15
    preferred_drop_effect = user32.RegisterClipboardFormatW("Preferred DropEffect")
    gmem_moveable = 0x0002
    gmem_zeroinit = 0x0040
    payload = build_hdrop_payload(absolute_paths)
    drop_effect_payload = struct.pack("<I", 1)

    handle = kernel32.GlobalAlloc(gmem_moveable | gmem_zeroinit, len(payload))
    if not handle:
        raise OSError("Clipboard memory allocation failed")
    drop_effect_handle = kernel32.GlobalAlloc(gmem_moveable | gmem_zeroinit, len(drop_effect_payload))
    if not drop_effect_handle:
        kernel32.GlobalFree(handle)
        raise OSError("Clipboard drop effect memory allocation failed")

    locked = kernel32.GlobalLock(handle)
    if not locked:
        kernel32.GlobalFree(handle)
        kernel32.GlobalFree(drop_effect_handle)
        raise OSError("Clipboard memory lock failed")

    try:
        ctypes.memmove(locked, payload, len(payload))
    finally:
        kernel32.GlobalUnlock(handle)

    locked = kernel32.GlobalLock(drop_effect_handle)
    if not locked:
        kernel32.GlobalFree(handle)
        kernel32.GlobalFree(drop_effect_handle)
        raise OSError("Clipboard drop effect memory lock failed")

    try:
        ctypes.memmove(locked, drop_effect_payload, len(drop_effect_payload))
    finally:
        kernel32.GlobalUnlock(drop_effect_handle)

    opened = False
    try:
        for _ in range(10):
            if user32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.05)
        if not opened:
            raise OSError("Cannot open Windows clipboard")

        if not user32.EmptyClipboard():
            raise OSError("Cannot empty Windows clipboard")
        if not user32.SetClipboardData(cf_hdrop, handle):
            raise OSError("Cannot write files to Windows clipboard")
        handle = None  # clipboard owns it now
        if not preferred_drop_effect or not user32.SetClipboardData(preferred_drop_effect, drop_effect_handle):
            raise OSError("Cannot write clipboard drop effect flag")
        drop_effect_handle = None
    finally:
        if opened:
            user32.CloseClipboard()
        if handle:
            kernel32.GlobalFree(handle)
        if drop_effect_handle:
            kernel32.GlobalFree(drop_effect_handle)


def build_hdrop_payload(absolute_paths: list[str]) -> bytes:
    """构建 DROPFILES + 文件路径列表的二进制结构。"""
    dropfiles_header = struct.pack("<IiiII", 20, 0, 0, 0, 1)
    file_list = ("\0".join(absolute_paths) + "\0\0").encode("utf-16le")
    return dropfiles_header + file_list
