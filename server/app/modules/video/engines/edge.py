"""edge-tts 引擎：微软免费端点，无需 key（真零配置默认引擎）。"""

from __future__ import annotations

import asyncio
import os

from server.app.modules.video.engines.base import register

_DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


def _synthesize_bytes(text: str, voice: str) -> bytes:
    """调 edge-tts 合成 mp3。edge_tts.Communicate 是 async，这里包一层同步执行。

    单独抽出便于测试 monkeypatch（不打真实网络）。
    """
    import edge_tts  # 懒导入：模块加载期不拉依赖

    async def _run() -> bytes:
        chunks: list[bytes] = []
        communicate = edge_tts.Communicate(text, voice)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)

    return asyncio.run(_run())


class EdgeTtsEngine:
    code = "edge"

    def synthesize(self, text: str) -> bytes:
        voice = os.environ.get("GEO_VIDEO_TTS_VOICE") or _DEFAULT_VOICE
        return _synthesize_bytes(text, voice)


register(EdgeTtsEngine())
