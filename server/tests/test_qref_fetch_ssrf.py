import pytest

from server.app.modules.quality_reference import fetch


@pytest.mark.parametrize("host_ip", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1", "fd00::1"])
def test_private_and_metadata_hosts_blocked(monkeypatch, host_ip):
    monkeypatch.setattr(
        fetch.socket, "getaddrinfo",
        lambda *a, **k: [(None, None, None, "", (host_ip, 0))],
    )
    with pytest.raises(fetch.SsrfBlockedError):
        fetch.assert_public_host("evil.example")


def test_non_http_scheme_blocked():
    with pytest.raises(fetch.SsrfBlockedError):
        fetch.download_image("file:///etc/passwd")


def test_public_host_passes(monkeypatch):
    monkeypatch.setattr(
        fetch.socket, "getaddrinfo",
        lambda *a, **k: [(None, None, None, "", ("93.184.216.34", 0))],
    )
    fetch.assert_public_host("example.com")  # 不抛即通过
