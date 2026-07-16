import httpx
import pytest

from server.app.modules.quality_reference import fetch

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.parametrize(
    "host_ip",
    [
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.1",
        "169.254.169.254",
        "::1",
        "fd00::1",
        "100.64.0.1",
    ],
)
def test_private_and_metadata_hosts_blocked(monkeypatch, host_ip):
    monkeypatch.setattr(
        fetch.socket,
        "getaddrinfo",
        lambda *a, **k: [(None, None, None, "", (host_ip, 0))],
    )
    with pytest.raises(fetch.SsrfBlockedError):
        fetch.assert_public_host("evil.example")


def test_ipv4_mapped_ipv6_metadata_blocked(monkeypatch):
    """::ffff:169.254.169.254 是 IPv4-mapped IPv6，须归一化后按 IPv4 规则拦截。"""
    monkeypatch.setattr(
        fetch.socket,
        "getaddrinfo",
        lambda *a, **k: [(None, None, None, "", ("::ffff:169.254.169.254", 0))],
    )
    with pytest.raises(fetch.SsrfBlockedError):
        fetch.assert_public_host("evil.example")


def test_non_http_scheme_blocked():
    with pytest.raises(fetch.SsrfBlockedError):
        fetch.download_image("file:///etc/passwd")


def test_public_host_passes(monkeypatch):
    monkeypatch.setattr(
        fetch.socket,
        "getaddrinfo",
        lambda *a, **k: [(None, None, None, "", ("93.184.216.34", 0))],
    )
    fetch.assert_public_host("example.com")  # 不抛即通过


def _patch_getaddrinfo_by_host(monkeypatch, mapping, default="93.184.216.34"):
    def fake_getaddrinfo(host, *a, **k):
        ip = mapping.get(host, default)
        return [(None, None, None, "", (ip, 0))]

    monkeypatch.setattr(fetch.socket, "getaddrinfo", fake_getaddrinfo)


def _patch_httpx_client(monkeypatch, handler):
    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", fake_client)


def test_download_image_redirect_to_private_ip_blocked(monkeypatch):
    _patch_getaddrinfo_by_host(
        monkeypatch,
        {"ok.example": "93.184.216.34", "169.254.169.254": "169.254.169.254"},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "ok.example":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/x.png"})
        raise AssertionError(f"unexpected second-hop request to {request.url.host}")

    _patch_httpx_client(monkeypatch, handler)

    with pytest.raises(fetch.SsrfBlockedError):
        fetch.download_image("http://ok.example/a.png")


def test_download_image_byte_cap_abort(monkeypatch):
    _patch_getaddrinfo_by_host(monkeypatch, {"ok.example": "93.184.216.34"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=_TINY_PNG * 1000,
        )

    _patch_httpx_client(monkeypatch, handler)

    with pytest.raises(fetch.ImageFetchError):
        fetch.download_image("http://ok.example/a.png", max_bytes=100)


def test_download_image_success(monkeypatch):
    _patch_getaddrinfo_by_host(monkeypatch, {"ok.example": "93.184.216.34"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "image/png"}, content=_TINY_PNG)

    _patch_httpx_client(monkeypatch, handler)

    data, mime = fetch.download_image("http://ok.example/a.png")
    assert data == _TINY_PNG
    assert mime == "image/png"


def test_download_image_non_image_rejected(monkeypatch):
    _patch_getaddrinfo_by_host(monkeypatch, {"ok.example": "93.184.216.34"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html></html>")

    _patch_httpx_client(monkeypatch, handler)

    with pytest.raises(fetch.ImageFetchError):
        fetch.download_image("http://ok.example/a.png")
