"""ffmpeg 合成：逐镜头渲染 + concat 拼接。命令构建为纯函数，便于测试。

画质细节（zoompan 参数、字幕位置）在容器内 /verify 时按实际观感调，本文件给出可跑骨架。
"""

from __future__ import annotations

import json
import subprocess

from server.app.modules.video.binaries import ffmpeg_binary, ffprobe_binary
from server.app.shared.errors import ClientError


def probe_duration(audio_path: str) -> float:
    cmd = [
        ffprobe_binary(),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        audio_path,
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise ClientError(f"ffprobe 失败: {proc.stderr.decode('utf-8', 'ignore')[:300]}")
    data = json.loads(proc.stdout or b"{}")
    return float(data.get("format", {}).get("duration") or 0.0)


def wrap_subtitle(text: str, max_chars: int = 14) -> str:
    """按字数折行（中文按字符）。返回含 \\n 的多行文本。"""
    if len(text) <= max_chars:
        return text
    lines = [text[i : i + max_chars] for i in range(0, len(text), max_chars)]
    return "\n".join(lines)


def _escape_drawtext(text: str) -> str:
    # drawtext text 需转义特殊字符
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "'").replace("%", "\\%")


def build_shot_command(
    *,
    image_path: str,
    audio_path: str,
    out_path: str,
    duration: float,
    subtitle: str,
    width: int,
    height: int,
    font_path: str,
) -> list[str]:
    """单镜头：图片循环成视频（时长=音频） + 缩放铺满 + Ken Burns + 烧录字幕 + 音频。"""
    wrapped = _escape_drawtext(wrap_subtitle(subtitle))
    fps = 30
    total_frames = max(1, int(round(duration * fps)))
    # 缩放到覆盖画布 → 裁剪 → zoompan 缓慢放大（Ken Burns）→ 底部 drawtext 字幕
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"zoompan=z='min(zoom+0.0006,1.15)':d={total_frames}:s={width}x{height}:fps={fps},"
        f"drawtext=fontfile='{font_path}':text='{wrapped}':"
        f"fontcolor=white:fontsize=54:box=1:boxcolor=black@0.5:boxborderw=16:"
        f"x=(w-text_w)/2:y=h-text_h-160:line_spacing=12"
    )
    return [
        ffmpeg_binary(),
        "-y",
        "-loop",
        "1",
        "-i",
        image_path,
        "-i",
        audio_path,
        "-t",
        f"{duration:.3f}",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        out_path,
    ]


def build_concat_command(*, list_file: str, out_path: str) -> list[str]:
    """concat demuxer 拼接逐镜头 mp4（list_file 为 ffconcat 清单）。"""
    return [
        ffmpeg_binary(),
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        list_file,
        "-c",
        "copy",
        out_path,
    ]


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        stderr = getattr(proc, "stderr", b"") or b""
        raise ClientError(f"ffmpeg 失败: {stderr.decode('utf-8', 'ignore')[:500]}")
