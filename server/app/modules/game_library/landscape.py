"""游戏库存图竖转横：入库前把竖屏截图用火山方舟 Seedream 4.0 扩成 16:9 横图，供文章配图/封面。

设计要点（best-effort，镜像 shared/baidu.py 的韧性口径，绝不拖垮入库）：
  - 未启用 / 无 key / 非图片 / 网络失败 / 上游非 200 → 原样返回竖图字节，只记日志不抛。
  - 只对严格竖图（高>宽）动手；横图/方图零 API 调用直接透传 → 幂等、可安全重复调用。
  - 转换发生在 store_image_bytes **之前**，故 StockImage 一出生即横图、id 与
    /api/stock-images/{id}/file 地址自始不变，不改写任何已存对象（守住图字节不可变缓存语义）。
  - 走 httpx 直连 Ark /images/generations（与 shared/baidu.py 同为图像路径直连 provider 的先例，
    不套 LiteLLM——那条规矩只针对生文）。
"""

from __future__ import annotations

import base64
import io
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.app.core.config import Settings

logger = logging.getLogger(__name__)

_OUTPAINT_PROMPT = (
    "把这张竖版图片扩展成横版风景图：保持原有主体、构图和画风不变，"
    "自然地向左右两侧补全延展背景，使其成为一张完整协调的横图。"
)


def is_portrait(width: int | None, height: int | None) -> bool:
    """严格竖屏：高 > 宽。缺尺寸 / 方图 / 横图 → False。"""
    if not width or not height:
        return False
    return height > width


def _detect_size(data: bytes) -> tuple[int, int] | None:
    """读图片宽高 (w, h)；非图 / 解析失败 → None。"""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            return int(im.width), int(im.height)
    except Exception:
        return None


def to_landscape_if_portrait(
    data: bytes, mime: str, *, settings: Settings | None = None
) -> tuple[bytes, str]:
    """竖图 → 横图（Seedream 4.0 扩图）；其余情况原样返回 (data, mime)。永不抛。

    settings 仅测试注入用；生产调用不传，走 get_settings()。
    """
    if settings is None:
        from server.app.core.config import get_settings

        settings = get_settings()

    if not settings.game_landscape_enabled:
        return data, mime
    if not settings.game_landscape_api_key:
        logger.warning("游戏库竖转横跳过：未配置 GEO_GAME_LANDSCAPE_API_KEY")
        return data, mime

    size = _detect_size(data)
    if size is None:
        return data, mime  # 非图/解析失败：透传
    if not is_portrait(size[0], size[1]):
        return data, mime  # 横图/方图：零 API 调用透传

    try:
        out = _seedream_edit(data, mime, settings)
    except Exception as exc:  # 韧性：任何异常都不拖垮入库
        logger.warning("游戏库竖转横失败（原样保留竖图）：%s", exc)
        return data, mime
    if not out:
        return data, mime
    logger.info("游戏库竖转横成功 %dx%d → %s", size[0], size[1], settings.game_landscape_size)
    return out  # (横图字节, 嗅探出的 mime)


def _seedream_edit(data: bytes, mime: str, settings: Settings) -> tuple[bytes, str] | None:
    """调 Seedream 4.0 图像编辑把竖图扩成横图，返回 (横图字节, mime)；无结果 / 非图 → None。

    mime 一律按文件头嗅探（不硬编码、不信声明），与 shared/baidu.py 口径一致。异常上抛由调用方兜。
    """
    import httpx

    from server.app.shared.baidu import sniff_image_mime

    b64 = base64.b64encode(data).decode()
    body = {
        "model": settings.game_landscape_model,
        "prompt": _OUTPAINT_PROMPT,
        "image": f"data:{mime};base64,{b64}",
        "size": settings.game_landscape_size,
        "response_format": "b64_json",
        "watermark": False,
    }
    headers = {
        "Authorization": f"Bearer {settings.game_landscape_api_key}",
        "Content-Type": "application/json",
    }
    resp = httpx.post(
        f"{settings.game_landscape_base_url}/images/generations",
        headers=headers,
        json=body,
        timeout=settings.game_landscape_timeout_seconds,
    )
    resp.raise_for_status()
    items = (resp.json() or {}).get("data") or []
    if not items:
        return None
    item = items[0]
    if item.get("b64_json"):
        raw = base64.b64decode(item["b64_json"])
        sniffed = sniff_image_mime(raw)
        return (raw, sniffed) if sniffed else None  # 非图（意外内容）当失败处理
    # 兜底：上游改走 url（即便已声明 b64_json）。复用通用下载器：SSRF 校验 + 体积上限 + 按文件头判 mime。
    url = item.get("url")
    if url:
        from server.app.shared import image_download

        return image_download.download_image(url, timeout=settings.game_landscape_timeout_seconds)
    return None
