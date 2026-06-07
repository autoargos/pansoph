"""S3 object store adapter. Works against SeaweedFS, Garage, or any S3 endpoint.

Uses aioboto3 (install pansoph-infra[s3]). Path-style addressing so it works with
self-hosted gateways that do not support virtual-host buckets.
"""

from __future__ import annotations

from typing import Any


class S3ObjectStore:
    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str) -> None:
        self._endpoint = endpoint
        self._bucket = bucket
        self._access_key = access_key
        self._secret_key = secret_key

    def _session(self) -> Any:
        import aioboto3
        from botocore.config import Config

        session = aioboto3.Session()
        return session.client(
            "s3",
            endpoint_url=self._endpoint,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            config=Config(s3={"addressing_style": "path"}),
        )

    async def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        async with self._session() as s3:
            extra = {"ContentType": content_type} if content_type else {}
            await s3.put_object(Bucket=self._bucket, Key=key, Body=data, **extra)

    async def get(self, key: str) -> bytes | None:
        async with self._session() as s3:
            try:
                resp = await s3.get_object(Bucket=self._bucket, Key=key)
            except s3.exceptions.NoSuchKey:
                return None
            async with resp["Body"] as body:
                return await body.read()

    async def delete(self, key: str) -> None:
        async with self._session() as s3:
            await s3.delete_object(Bucket=self._bucket, Key=key)
