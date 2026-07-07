from __future__ import annotations

import server.app.modules.video.binaries as b


def test_ffmpeg_binary_default(monkeypatch):
    monkeypatch.delenv("GEO_FFMPEG_PATH", raising=False)
    assert b.ffmpeg_binary() == "ffmpeg"


def test_ffmpeg_binary_env_override(monkeypatch):
    monkeypatch.setenv("GEO_FFMPEG_PATH", "/opt/bin/ffmpeg")
    assert b.ffmpeg_binary() == "/opt/bin/ffmpeg"


def test_ffprobe_binary_env_override(monkeypatch):
    monkeypatch.setenv("GEO_FFPROBE_PATH", "/opt/bin/ffprobe")
    assert b.ffprobe_binary() == "/opt/bin/ffprobe"


def test_cjk_font_path_env_override(monkeypatch):
    monkeypatch.setenv("GEO_VIDEO_FONT_PATH", "/fonts/my.ttf")
    assert b.cjk_font_path() == "/fonts/my.ttf"
