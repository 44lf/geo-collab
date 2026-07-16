"""外链图下载 + SSRF 校验。

SSRF 档位（见 plan Global Constraints）：scheme 白名单 + 解析所有 A/AAAA、任一命中
私网/环回/link-local/保留/元数据即拒 + 逐跳重定向重校验 + max_bytes + 短超时。
IP 钉连接抗 DNS 重绑定属未来工作（运营喂真 URL、威胁低）。
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

from server.app.shared.errors import ClientError


class ImageFetchError(ClientError):
    """图片下载失败（超时/超限/网络错/非图）。worker 计 skipped、剔除该图节点。"""


class SsrfBlockedError(ImageFetchError):
    """目标解析到私网/环回/元数据地址，或 scheme 不允许 → 拒绝。"""


# 显式拦截列表：某些地址段在 ipaddress 的 is_private/is_reserved/... 标志位上不必然为
# True（且随 Python 版本可能变化，is_global 更不可靠），需要单独兜底。
_EXTRA_BLOCKED_NETS = (
    ipaddress.ip_network("100.64.0.0/10"),  # RFC 6598 CGN / shared address space
)


def assert_public_host(host: str) -> None:
    """解析 host 的所有地址，任一为私网/环回/link-local/保留/元数据即拒。"""
    if not host:
        raise SsrfBlockedError("empty host")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise ImageFetchError(f"DNS 解析失败: {host}: {exc}") from exc
    for info in infos:
        addr = info[4][0]
        ip = ipaddress.ip_address(addr)
        if ip.version == 6 and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local  # 含 169.254.169.254 云元数据
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
            or any(ip in net for net in _EXTRA_BLOCKED_NETS)
        ):
            raise SsrfBlockedError(f"目标地址不允许（私网/环回/元数据）: {host} → {addr}")


def download_image(
    url: str,
    *,
    timeout_connect: float = 5.0,
    timeout_read: float = 15.0,
    max_bytes: int = 20 * 1024 * 1024,
    max_redirects: int = 3,
) -> tuple[bytes, str]:
    """下载一张外链图，返回 (data, mime)。手动跟随重定向、逐跳重校验 host。"""
    import httpx

    timeout = httpx.Timeout(
        connect=timeout_connect, read=timeout_read, write=timeout_read, pool=timeout_connect
    )
    current = url
    with httpx.Client(follow_redirects=False, timeout=timeout) as client:
        for _ in range(max_redirects + 1):
            parsed = urlparse(current)
            if parsed.scheme not in ("http", "https"):
                raise SsrfBlockedError(f"scheme 不允许: {parsed.scheme or '(none)'}")
            assert_public_host(parsed.hostname or "")
            try:
                with client.stream("GET", current) as resp:
                    if resp.is_redirect:
                        loc = resp.headers.get("location")
                        if not loc:
                            raise ImageFetchError("重定向缺 Location")
                        current = urljoin(current, loc)
                        continue
                    resp.raise_for_status()
                    chunks = bytearray()
                    for chunk in resp.iter_bytes():
                        chunks += chunk
                        if len(chunks) > max_bytes:
                            raise ImageFetchError(f"图片超过 {max_bytes} 字节上限")
                    data = bytes(chunks)
            except httpx.HTTPError as exc:
                raise ImageFetchError(f"下载失败: {exc}") from exc
            mime = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            if not mime.startswith("image/"):
                mime = _sniff_mime(data)
                if mime is None:
                    raise ImageFetchError(
                        f"非图片内容: content-type={resp.headers.get('content-type')}"
                    )
            return data, mime
    raise ImageFetchError(f"重定向超过 {max_redirects} 跳")


def _sniff_mime(data: bytes) -> str | None:
    """content-type 缺失/非 image 时按魔数兜底判 mime。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None
