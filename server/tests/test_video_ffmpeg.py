from __future__ import annotations

import pytest

from server.app.modules.video import ffmpeg_compose as fc
from server.app.shared.errors import ClientError


def test_wrap_subtitle_breaks_by_chars():
    assert fc.wrap_subtitle("一二三四五六七八九十", max_chars=4) == "一二三四\n五六七八\n九十"


def test_wrap_subtitle_short_unchanged():
    assert fc.wrap_subtitle("短句", max_chars=14) == "短句"


def test_build_shot_command_has_core_flags():
    cmd = fc.build_shot_command(
        image_path="/tmp/a.jpg",
        audio_path="/tmp/a.mp3",
        out_path="/tmp/a.mp4",
        duration=3.0,
        subtitle="字幕",
        width=1080,
        height=1920,
        font_path="/f.ttf",
    )
    assert cmd[0].endswith("ffmpeg")
    assert "/tmp/a.jpg" in cmd
    assert "/tmp/a.mp3" in cmd
    assert cmd[-1] == "/tmp/a.mp4"
    # 滤镜串里应包含缩放/zoompan/drawtext 关键片段
    vf = " ".join(cmd)
    assert "zoompan" in vf
    assert "drawtext" in vf
    assert "1080" in vf and "1920" in vf


def test_build_concat_command():
    cmd = fc.build_concat_command(list_file="/tmp/list.txt", out_path="/tmp/out.mp4")
    assert "concat" in cmd
    assert "/tmp/list.txt" in cmd
    assert cmd[-1] == "/tmp/out.mp4"


def test_run_raises_on_nonzero(monkeypatch):
    class _Proc:
        returncode = 1
        stderr = b"boom"

    monkeypatch.setattr(fc.subprocess, "run", lambda *a, **k: _Proc())
    with pytest.raises(ClientError):
        fc.run(["ffmpeg", "-x"])
