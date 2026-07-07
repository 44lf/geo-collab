"""TTS 引擎注册表（可插拔，镜像 tasks/drivers）。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from server.app.shared.errors import ClientError

_DEFAULT_CODE = "edge"
_REGISTRY: dict[str, TtsEngine] = {}


@runtime_checkable
class TtsEngine(Protocol):
    code: str

    def synthesize(self, text: str) -> bytes:
        """把文本合成为 mp3 字节。失败抛异常（由调用方翻译）。"""
        ...


def register(engine: TtsEngine) -> None:
    _REGISTRY[engine.code] = engine


def get_engine(code: str | None) -> TtsEngine:
    resolved = code or _DEFAULT_CODE
    engine = _REGISTRY.get(resolved)
    if engine is None:
        raise ClientError(f"未知 TTS 引擎: {resolved}（可用: {sorted(_REGISTRY)}）")
    return engine
