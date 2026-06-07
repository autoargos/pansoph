"""S3 object store adapter. Works against SeaweedFS, Garage, or any S3 endpoint.

Uses aioboto3 (install pansoph-infra[s3]). The client wiring lands with agora's
configuration layer; this fixes the adapter shape.
"""

from __future__ import annotations


class S3ObjectStore:
    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str) -> None:
        self._endpoint = endpoint
        self._bucket = bucket
        self._access_key = access_key
        self._secret_key = secret_key

    async def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        raise NotImplementedError("S3 put lands with the agora configuration layer")

    async def get(self, key: str) -> bytes | None:
        raise NotImplementedError("S3 get lands with the agora configuration layer")

    async def delete(self, key: str) -> None:
        raise NotImplementedError("S3 delete lands with the agora configuration layer")
