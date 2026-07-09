from __future__ import annotations

import pytest

from server.app.modules.video import engines
from server.app.shared.errors import ClientError


def test_default_engine_is_edge():
    eng = engines.get_engine(None)
    assert eng.code == "edge"


def test_unknown_engine_raises():
    with pytest.raises(ClientError):
        engines.get_engine("nope-not-real")


def test_edge_synthesize_mocked(monkeypatch):
    # 不打真实网络：mock EdgeTtsEngine 内部的合成实现，只验证契约（返回 bytes）
    from server.app.modules.video.engines import edge

    monkeypatch.setattr(edge, "_synthesize_bytes", lambda text, voice: b"ID3fake-mp3")
    eng = engines.get_engine("edge")
    out = eng.synthesize("你好世界")
    assert isinstance(out, bytes)
    assert out == b"ID3fake-mp3"
