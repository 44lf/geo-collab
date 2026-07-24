"""TTS 引擎包：导入即注册各引擎。"""

from __future__ import annotations

from server.app.modules.video.engines import edge  # noqa: F401  (register edge)
from server.app.modules.video.engines.base import TtsEngine, get_engine, register

__all__ = ["TtsEngine", "get_engine", "register"]
