"""SRT 字幕构建：镜头文案 + 时长 → 标准 SRT。纯函数。"""

from __future__ import annotations


def _fmt_ts(seconds: float) -> str:
    ms_total = int(round(seconds * 1000))
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(cues: list[tuple[str, float]]) -> str:
    """cues: [(text, duration_seconds), ...]，按顺序累加时间轴生成 SRT。"""
    lines: list[str] = []
    cursor = 0.0
    for idx, (text, dur) in enumerate(cues, start=1):
        start = cursor
        end = cursor + dur
        cursor = end
        lines.append(str(idx))
        lines.append(f"{_fmt_ts(start)} --> {_fmt_ts(end)}")
        lines.append(text)
        lines.append("")  # cue 之间空行
    return "\n".join(lines)
