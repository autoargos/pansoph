"""In-memory object store. For tests and single-process local runs."""

from __future__ import annotations


class MemoryObjectStore:
    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        self._data[key] = data

    async def get(self, key: str) -> bytes | None:
        return self._data.get(key)

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)
