"""通用截图下载器：限体积/类型/重定向，best-effort（失败返 None、不抛）。"""

import ipaddress
import logging
import socket
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPHandler, HTTPRedirectHandler, HTTPSHandler, Request, build_opener

logger = logging.getLogger(__name__)

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_MAGIC = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
}
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "image/*"}
_MAX_REDIRECTS = 5


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = build_opener(_NoRedirect, HTTPHandler, HTTPSHandler)


def _opener_open(req: Request, timeout: int):
    return _OPENER.open(req, timeout=timeout)  # noqa: S310


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _validate_http_url(url: str):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("unsupported url scheme")
    if not parsed.hostname:
        raise ValueError("missing url host")
    return parsed


def _ensure_public_host(host: str, port: int | None) -> None:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_blocked_ip(literal):
            raise ValueError("private ip rejected")
        return

    resolved = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not resolved:
        raise ValueError("host resolve failed")
    for item in resolved:
        ip = ipaddress.ip_address(item[4][0])
        if _is_blocked_ip(ip):
            raise ValueError("private resolved ip rejected")


def _urlopen_same_host(url: str, timeout: int):
    """打开 URL，手动跟随同 host 重定向，并在每跳请求前做 SSRF 校验。"""
    current_url = url
    initial = _validate_http_url(current_url)
    initial_host = initial.hostname

    for _ in range(_MAX_REDIRECTS + 1):
        parsed = _validate_http_url(current_url)
        if parsed.hostname != initial_host:
            raise ValueError("cross-host redirect rejected")
        _ensure_public_host(parsed.hostname, parsed.port)

        req = Request(current_url, headers=_HEADERS)
        try:
            return _opener_open(req, timeout=timeout)
        except HTTPError as exc:
            if exc.code not in {301, 302, 303, 307, 308}:
                raise
            location = exc.headers.get("Location") if exc.headers is not None else None
            if not location:
                raise ValueError("redirect without location") from exc
            current_url = urljoin(current_url, location)

    raise ValueError("too many redirects")


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
