"""
Blob storage backends behind one tiny interface.

* VercelBlobBackend - production. Talks to the Vercel Blob REST API with the store token.
* LocalBackend      - development/tests. Writes under settings.BLOB_LOCAL_ROOT.

NOTE: the exact REST contract of Vercel Blob (query/header names, API version) is the one
part of this project that could not be verified offline. It is isolated in
VercelBlobBackend; run `python manage.py blob_smoketest` against your real store once,
and adjust `put`/`delete` here if Vercel has changed anything.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


@dataclass(frozen=True)
class StoredBlob:
    key: str   # pathname inside the store
    url: str   # location returned by the store


class BlobBackend(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str) -> StoredBlob: ...

    @abstractmethod
    def open(self, key: str, url: str) -> Iterator[bytes]: ...

    @abstractmethod
    def delete(self, key: str, url: str) -> None: ...


class LocalBackend(BlobBackend):
    def __init__(self):
        self.root = Path(settings.BLOB_LOCAL_ROOT).resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("Invalid storage key.")
        return path

    def put(self, key, data, content_type):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return StoredBlob(key=key, url=path.as_uri())

    def open(self, key, url):
        handle = self._path(key).open("rb")  # raises FileNotFoundError eagerly

        def chunks():
            with handle:
                while chunk := handle.read(64 * 1024):
                    yield chunk

        return chunks()

    def delete(self, key, url):
        self._path(key).unlink(missing_ok=True)


class VercelBlobBackend(BlobBackend):
    TIMEOUT = 30

    def __init__(self):
        if not settings.BLOB_READ_WRITE_TOKEN:
            raise ImproperlyConfigured("BLOB_READ_WRITE_TOKEN is not set.")
        self.base = settings.BLOB_API_URL.rstrip("/")
        self.headers = {
            "authorization": f"Bearer {settings.BLOB_READ_WRITE_TOKEN}",
            "x-api-version": settings.BLOB_API_VERSION,
        }

    def put(self, key, data, content_type):
        resp = requests.put(
            f"{self.base}/", params={"pathname": key}, data=data, timeout=self.TIMEOUT,
            headers={**self.headers, "x-content-type": content_type, "x-vercel-blob-access": "private",
                     "x-add-random-suffix": "0"},
        )
        resp.raise_for_status()
        body = resp.json()
        return StoredBlob(key=body.get("pathname", key), url=body["url"])

    def open(self, key, url):
        resp = requests.get(url, headers=self.headers, stream=True, timeout=self.TIMEOUT)
        resp.raise_for_status()
        return resp.iter_content(chunk_size=64 * 1024)

    def delete(self, key, url):
        resp = requests.post(f"{self.base}/delete", json={"urls": [url]}, headers=self.headers, timeout=self.TIMEOUT)
        resp.raise_for_status()


def get_backend() -> BlobBackend:
    """Resolve the backend at call time so tests can override settings."""
    return VercelBlobBackend() if settings.BLOB_BACKEND == "vercel" else LocalBackend()
