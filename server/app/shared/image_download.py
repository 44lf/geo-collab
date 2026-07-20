"""通用截图下载器：限体积/类型/重定向，best-effort（失败返 None、不抛）。"""

from __future__ import annotations

import logging
from urllib.parse import urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_MAGIC = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
}
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "image/*"}


def _urlopen_same_host(url: str, timeout: int):
    """打开 URL，拒绝跨站重定向（默认 opener 会跟随；这里限制 host 不变）。"""
    req = Request(url, headers=_HEADERS)
    resp = urlopen(req, timeout=timeout)  # noqa: S310
    if urlparse(resp.geturl()).hostname != urlparse(url).hostname:
        resp.close()
        raise ValueError("cross-host redirect rejected")
    return resp


def _sniff(data: bytes, declared: str) -> str | None:
    for magic, mime in _MAGIC.items():
        if data.startswith(magic):
            return mime
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return declared if declared in ALLOWED_MIME else None


def download_image(
    url: str,
    *,
    timeout: int = 10,
    max_bytes: int = 20 * 1024 * 1024,
) -> tuple[bytes, str] | None:
    try:
        with _urlopen_same_host(url, timeout) as resp:
            declared = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
            content_length = resp.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                return None
            data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            return None
        mime = _sniff(data, declared)
        if mime not in ALLOWED_MIME:
            return None
        return data, mime
    except Exception as exc:
        logger.info("download_image 失败 url=%s：%s", url, exc)
        return None
