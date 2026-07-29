from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.app.modules.collector.inbox import MinioCollectorInbox


class _FakeMinio:
    def __init__(self):
        self.presign_calls = []
        self.stat_calls = []

    def presigned_put_object(self, bucket, object_key, *, expires):
        self.presign_calls.append((bucket, object_key, expires))
        return "https://inbox.invalid/signed-secret"

    def stat_object(self, bucket, object_key):
        self.stat_calls.append((bucket, object_key))
        return SimpleNamespace(
            size=2048,
            metadata={"x-amz-meta-sha256": "a" * 64},
        )


def test_minio_inbox_issues_fixed_key_presign_and_reads_sha_metadata():
    client = _FakeMinio()
    inbox = MinioCollectorInbox(client=client, bucket="collector-inbox")

    url = inbox.authorize_put(
        object_key="incoming/collector-1/transport-1.tar.zst",
        size_bytes=2048,
        sha256="a" * 64,
        expires_in=timedelta(minutes=5),
    )
    metadata = inbox.stat_object(
        object_key="incoming/collector-1/transport-1.tar.zst",
    )

    assert url == "https://inbox.invalid/signed-secret"
    assert client.presign_calls == [
        (
            "collector-inbox",
            "incoming/collector-1/transport-1.tar.zst",
            timedelta(minutes=5),
        )
    ]
    assert metadata.object_key == "incoming/collector-1/transport-1.tar.zst"
    assert metadata.size_bytes == 2048
    assert metadata.sha256 == "a" * 64
    assert client.stat_calls == [("collector-inbox", "incoming/collector-1/transport-1.tar.zst")]


class _DownloadResponse(BytesIO):
    def __init__(self, value: bytes):
        super().__init__(value)
        self.released = False
        self.bytes_read = 0

    def read(self, size=-1):
        chunk = super().read(size)
        self.bytes_read += len(chunk)
        return chunk

    def release_conn(self):
        self.released = True


def test_download_streams_fixed_key_and_closes_connection(tmp_path: Path):
    response = _DownloadResponse(b"bundle")
    client = _FakeMinio()
    client.get_object = lambda bucket, key: response
    inbox = MinioCollectorInbox(client=client, bucket="collector-inbox")

    path = inbox.download_to(
        object_key="incoming/collector/transport.tar.zst",
        destination=tmp_path / "archive.tar.zst",
        expected_size=6,
    )

    assert path.read_bytes() == b"bundle"
    assert response.closed is True
    assert response.released is True


def test_download_size_mismatch_removes_partial_file(tmp_path: Path):
    client = _FakeMinio()
    client.get_object = lambda bucket, key: _DownloadResponse(b"short")
    inbox = MinioCollectorInbox(client=client, bucket="collector-inbox")
    destination = tmp_path / "archive.tar.zst"

    with pytest.raises(OSError, match="size changed"):
        inbox.download_to(
            object_key="incoming/collector/transport.tar.zst",
            destination=destination,
            expected_size=10,
        )

    assert not destination.exists()


def test_download_stops_after_declared_size_and_removes_oversize_file(tmp_path: Path):
    response = _DownloadResponse(b"declared-plus-attacker-controlled-tail")
    client = _FakeMinio()
    client.get_object = lambda bucket, key: response
    inbox = MinioCollectorInbox(client=client, bucket="collector-inbox")
    destination = tmp_path / "archive.tar.zst"

    with pytest.raises(OSError, match="size changed"):
        inbox.download_to(
            object_key="incoming/collector/transport.tar.zst",
            destination=destination,
            expected_size=len(b"declared"),
        )

    assert response.bytes_read == len(b"declared") + 1
    assert not destination.exists()
