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
