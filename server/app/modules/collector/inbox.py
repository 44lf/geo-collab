"""MinIO Inbox adapter for short-lived Collector uploads and completion HEAD checks."""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from server.app.modules.collector.transfer_service import InboxObjectMetadata


class MinioCollectorInbox:
    def __init__(self, *, client, bucket: str):
        if not bucket or bucket != bucket.strip():
            raise ValueError("Collector Inbox bucket must be non-empty trimmed text")
        self._client = client
        self._bucket = bucket

    def authorize_put(
        self,
        *,
        object_key: str,
        size_bytes: int,
        sha256: str,
        expires_in: timedelta,
    ) -> str:
        if size_bytes <= 0 or len(sha256) != 64:
            raise ValueError("invalid Collector Inbox object declaration")
        return self._client.presigned_put_object(
            self._bucket,
            object_key,
            expires=expires_in,
        )

    def stat_object(self, *, object_key: str) -> InboxObjectMetadata:
        stat = self._client.stat_object(self._bucket, object_key)
        metadata = stat.metadata or {}
        sha256 = (
            metadata.get("x-amz-meta-sha256")
            or metadata.get("X-Amz-Meta-Sha256")
            or metadata.get("sha256")
            or ""
        )
        return InboxObjectMetadata(
            object_key=object_key,
            size_bytes=int(stat.size),
            sha256=str(sha256),
        )

    def download_to(
        self,
        *,
        object_key: str,
        destination: Path,
        expected_size: int,
    ) -> Path:
        """Stream one fixed Inbox key to a new local file with a hard byte bound."""

        if expected_size <= 0 or destination.exists():
            raise ValueError("invalid Collector Inbox download declaration")
        destination.parent.mkdir(parents=True, exist_ok=True)
        response = self._client.get_object(self._bucket, object_key)
        try:
            with destination.open("xb") as output:
                remaining = expected_size
                while remaining:
                    chunk = response.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    output.write(chunk)
                    remaining -= len(chunk)
                if remaining or response.read(1):
                    raise OSError("Collector Inbox object size changed during download")
                output.flush()
                os.fsync(output.fileno())
            return destination
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        finally:
            response.close()
            response.release_conn()


def _minio_client():
    import os

    import certifi
    from minio import Minio
    from urllib3 import PoolManager, Retry
    from urllib3.util import Timeout

    from server.app.core.config import get_settings

    settings = get_settings()
    http_client = PoolManager(
        timeout=Timeout(connect=5, read=30),
        maxsize=10,
        cert_reqs="CERT_REQUIRED",
        ca_certs=os.environ.get("SSL_CERT_FILE") or certifi.where(),
        retries=Retry(
            total=1,
            read=False,
            backoff_factor=0.2,
            status_forcelist=[500, 502, 503, 504],
        ),
    )
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
        http_client=http_client,
    )


def get_collector_inbox() -> MinioCollectorInbox:
    from server.app.core.config import get_settings

    settings = get_settings()
    return MinioCollectorInbox(
        client=_minio_client(),
        bucket=settings.collector_inbox_bucket,
    )
