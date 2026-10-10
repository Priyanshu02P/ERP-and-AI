"""
Blob storage backends behind one tiny interface.

* VercelBlobBackend - production. Talks to the Vercel Blob REST API with the store token.
* LocalBackend      - development/tests. Writes under settings.BLOB_LOCAL_ROOT.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
import vercel
from vercel.blob import AsyncBlobClient
    
from asgiref.sync import async_to_sync

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


@dataclass(frozen=True)
class StoredBlob:
    key: str
    url: str


class BlobBackend(ABC):
    @abstractmethod
    def put(
        self, key: str, data: bytes, content_type: str
    ) -> StoredBlob:
        ...

    @abstractmethod
    def open(self, key: str, url: str) -> Iterator[bytes]:
        ...

    @abstractmethod
    def delete(self, key: str, url: str) -> None:
        ...


class LocalBackend(BlobBackend):
    def __init__(self):
        self.root = Path(settings.BLOB_LOCAL_ROOT).resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()

        if path == self.root or self.root not in path.parents:
            raise ValueError("Invalid storage key.")

        return path

    def put(self, key, data, content_type):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

        return StoredBlob(key=key, url=path.as_uri())

    def open(self, key, url):
        handle = self._path(key).open("rb")

        def chunks():
            with handle:
                while chunk := handle.read(64 * 1024):
                    yield chunk

        return chunks()

    def delete(self, key, url):
        self._path(key).unlink(missing_ok=True)


class VercelBlobBackend(BlobBackend):
    """Synchronous adapter around Vercel's Blob SDK."""

    def __init__(self):
        token = getattr(settings, "BLOB_READ_WRITE_TOKEN", "")

        if not token:
            raise ImproperlyConfigured(
                "BLOB_READ_WRITE_TOKEN is not set."
            )

        try:
            from vercel.blob import BlobClient
        except ImportError as exc:
            raise ImproperlyConfigured(
                "Vercel Blob SDK is missing. Install it with: pip install vercel"
            ) from exc

        self.client = BlobClient(token=token)

        self.async_client = AsyncBlobClient(token=token)

    def put(self, key: str, data: bytes, content_type: str) -> StoredBlob:
        blob = self.client.put(
            key,
            data,
            access="private",
            content_type=content_type,
            add_random_suffix=False,
        )

        return StoredBlob(
            key=blob.pathname,
            url=blob.url,
        )

    def open(self, key: str, url: str) -> Iterator[bytes]:
        async def download_content() -> bytes:
            result = await self.async_client.get(
                url or key,
                access="private",
            )

            if result is None or result.status_code != 200:
                raise FileNotFoundError(f"Blob not found: {key}")

            if result.content is None:
                raise FileNotFoundError(
                    f"Blob has no readable content: {key}"
                )

            return bytes(result.content)

        content = async_to_sync(download_content)()

        def chunks():
            for offset in range(0, len(content), 64 * 1024):
                yield content[offset:offset + 64 * 1024]

        return chunks()

    def delete(self, key: str, url: str) -> None:
        self.client.delete(url or key)


def get_backend() -> BlobBackend:
    """Resolve the backend at call time for test overrides."""
    if settings.BLOB_BACKEND == "vercel":
        return VercelBlobBackend()

    return LocalBackend()
