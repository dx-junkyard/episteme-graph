"""MinIO storage manager for Episteme Graph."""

from __future__ import annotations

import io
from datetime import timedelta
from functools import lru_cache

from minio import Minio

from core.config import get_settings

BUCKETS = ("raw-papers", "raw-texts", "extracted-structures", "figure-images")


class StorageManager:
    """Thin wrapper around the MinIO Python client."""

    def __init__(self) -> None:
        settings = get_settings()
        endpoint = settings.minio_endpoint
        access_key = settings.minio_access_key
        secret_key = settings.minio_secret_key
        self._public_endpoint = settings.minio_public_endpoint

        self.client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=False)
        self._ensure_buckets()

    # ------------------------------------------------------------------
    # Bucket helpers
    # ------------------------------------------------------------------

    def _ensure_buckets(self) -> None:
        """Create the required buckets if they do not exist."""
        for bucket in BUCKETS:
            if not self.client.bucket_exists(bucket):
                self.client.make_bucket(bucket)

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def upload_pdf(self, bucket: str, object_name: str, data: bytes) -> str:
        """Upload a PDF (as *bytes*) to MinIO and return the object name."""
        self.client.put_object(
            bucket,
            object_name,
            io.BytesIO(data),
            length=len(data),
            content_type="application/pdf",
        )
        return object_name

    def upload_bytes(
        self,
        bucket: str,
        object_name: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload arbitrary bytes to MinIO and return the object name."""
        self.client.put_object(
            bucket,
            object_name,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
        return object_name

    def get_object(self, bucket: str, object_name: str) -> bytes:
        """Download an object from MinIO and return its bytes."""
        response = self.client.get_object(bucket, object_name)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    # ------------------------------------------------------------------
    # Pre-signed URL
    # ------------------------------------------------------------------
    def presigned_url(
        self,
        bucket: str,
        object_name: str,
        expires: timedelta = timedelta(hours=1),
    ) -> str:
        # MinIOに対して、署名計算時に使用する Host ヘッダーを強制する。
        url = self.client.presigned_get_object(
            bucket,
            object_name,
            expires=expires,
            extra_query_params={"host": self._public_endpoint}
        )

        # URLのドメイン部分を内部エンドポイントから公開エンドポイントに置換する
        internal_endpoint = get_settings().minio_endpoint
        if internal_endpoint != self._public_endpoint:
             url = url.replace(internal_endpoint, self._public_endpoint, 1)

        return url


    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_objects(self, bucket: str, prefix: str = "") -> list[str]:
        """Return a list of object names under *prefix* in *bucket*."""
        return [
            obj.object_name
            for obj in self.client.list_objects(bucket, prefix=prefix, recursive=True)
        ]


@lru_cache(maxsize=1)
def get_storage_client() -> StorageManager:
    """Return a cached StorageManager instance."""
    return StorageManager()
