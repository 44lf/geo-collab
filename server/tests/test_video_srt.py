from __future__ import annotations

from server.app.modules.video.srt import build_srt


def test_build_srt_two_cues():
    out = build_srt([("第一段", 2.5), ("第二段", 3.0)])
    assert out == (
        "1\n"
        "00:00:00,000 --> 00:00:02,500\n"
        "第一段\n"
        "\n"
        "2\n"
        "00:00:02,500 --> 00:00:05,500\n"
        "第二段\n"
    )


def test_build_srt_empty():
    assert build_srt([]) == ""


def test_build_srt_hour_rollover():
    out = build_srt([("x", 3661.0)])  # 1h 1m 1s
    assert "00:00:00,000 --> 01:01:01,000" in out
