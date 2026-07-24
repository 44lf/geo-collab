"""ffmpeg / ffprobe / 字体路径解析。集中一处，便于测试与容器外覆盖。"""

from __future__ import annotations

import os

_DEFAULT_CJK_FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"


def ffmpeg_binary() -> str:
    return os.environ.get("GEO_FFMPEG_PATH") or "ffmpeg"


def ffprobe_binary() -> str:
    return os.environ.get("GEO_FFPROBE_PATH") or "ffprobe"


def cjk_font_path() -> str:
    return os.environ.get("GEO_VIDEO_FONT_PATH") or _DEFAULT_CJK_FONT
