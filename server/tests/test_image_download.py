from email.message import Message
from urllib.error import HTTPError

from server.app.shared import image_download


def test_download_rejects_oversize(monkeypatch):
    class FakeResp:
        headers = {"Content-Type": "image/jpeg", "Content-Length": str(999_999_999)}

        def read(self, n=-1):
            return b"\xff\xd8\xff" + b"0" * 100

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(image_download, "_urlopen_same_host", lambda *a, **k: FakeResp())
    assert image_download.download_image("http://x/big.jpg", max_bytes=1000) is None


def test_download_rejects_non_image(monkeypatch):
    class FakeResp:
        headers = {"Content-Type": "text/html"}

        def read(self, n=-1):
            return b"<html>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(image_download, "_urlopen_same_host", lambda *a, **k: FakeResp())
    assert image_download.download_image("http://x/p.html") is None


def test_download_ok_jpeg(monkeypatch):
    class FakeResp:
        headers = {"Content-Type": "image/jpeg"}

        def read(self, n=-1):
            return b"\xff\xd8\xff\xe0" + b"payload"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(image_download, "_urlopen_same_host", lambda *a, **k: FakeResp())
    out = image_download.download_image("http://x/ok.jpg")
    assert out is not None and out[1] == "image/jpeg" and out[0].startswith(b"\xff\xd8")


def test_download_rejects_file_scheme_before_open(monkeypatch):
    opened: list[str] = []

    def fake_open(req, timeout):
        opened.append(getattr(req, "full_url", str(req)))
        raise AssertionError("network open should not be called")

    monkeypatch.setattr(image_download, "_opener_open", fake_open, raising=False)
    monkeypatch.setattr(image_download, "urlopen", fake_open, raising=False)

    assert image_download.download_image("file:///etc/passwd") is None
    assert opened == []


def test_download_rejects_private_ip_before_open(monkeypatch):
    opened: list[str] = []

    def fake_open(req, timeout):
        opened.append(getattr(req, "full_url", str(req)))
        raise AssertionError("network open should not be called")

    monkeypatch.setattr(image_download, "_opener_open", fake_open, raising=False)
    monkeypatch.setattr(image_download, "urlopen", fake_open, raising=False)

    assert image_download.download_image("http://127.0.0.1/x.jpg") is None
    assert opened == []


def test_download_rejects_rebound_redirect_before_second_open(monkeypatch):
    opened: list[str] = []
    resolved = ["93.184.216.34", "127.0.0.1"]

    def fake_getaddrinfo(host, port, *args, **kwargs):
        ip = resolved.pop(0)
        return [(None, None, None, None, (ip, port or 80))]

    def fake_open(req, timeout):
        opened.append(req.full_url)
        headers = Message()
        headers["Location"] = "http://ok.example/next.jpg"
        raise HTTPError(req.full_url, 302, "Found", headers, None)

    monkeypatch.setattr(image_download.socket, "getaddrinfo", fake_getaddrinfo, raising=False)
    monkeypatch.setattr(image_download, "_opener_open", fake_open, raising=False)
    monkeypatch.setattr(
        image_download,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("old urlopen path")),
        raising=False,
    )

    assert image_download.download_image("http://ok.example/start.jpg") is None
    assert opened == ["http://ok.example/start.jpg"]
