"""
File storage abstraction. S3 OR local filesystem. Controlled by FF_USE_S3 flag.
"""

import logging
import os
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from .config import get_settings
from .flags import get_flags

logger = logging.getLogger(__name__)


class StorageBackend(ABC):
    @abstractmethod
    async def upload(
        self, file_bytes: bytes, filename: str, tenant_id: str, folder: str = ""
    ) -> str:
        """Upload file. Returns the URL/path to the stored file."""
        ...

    @abstractmethod
    async def get_url(self, key: str) -> str:
        """Get public/accessible URL for a stored file."""
        ...

    async def read_file(self, file_id: str, tenant_id: str, folder: str = "uploads") -> Optional[tuple[bytes, str]]:
        """Read a file by its ID. Returns (bytes, content_type) or None if not found."""
        return None


class S3Storage(StorageBackend):
    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            import boto3

            settings = get_settings()
            kwargs = {"region_name": settings.aws_region}
            if settings.aws_access_key_id:
                kwargs["aws_access_key_id"] = settings.aws_access_key_id
                kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
            self._client = boto3.client("s3", **kwargs)
        return self._client

    async def upload(
        self, file_bytes: bytes, filename: str, tenant_id: str, folder: str = ""
    ) -> str:
        settings = get_settings()
        ext = Path(filename).suffix
        unique = f"{uuid.uuid4().hex[:12]}{ext}"
        key = f"{tenant_id}/{folder}/{unique}" if folder else f"{tenant_id}/{unique}"
        key = key.strip("/")

        client = self._get_client()
        client.put_object(
            Bucket=settings.s3_bucket_name,
            Key=key,
            Body=file_bytes,
            ContentType=_guess_content_type(filename),
        )

        url = f"https://{settings.s3_bucket_name}.s3.{settings.aws_region}.amazonaws.com/{key}"
        logger.info("Uploaded to S3: %s", key)
        return url

    async def get_url(self, key: str) -> str:
        settings = get_settings()
        return f"https://{settings.s3_bucket_name}.s3.{settings.aws_region}.amazonaws.com/{key}"


class LocalStorage(StorageBackend):
    def __init__(self, base_path: str = "./local_storage"):
        self.base_path = Path(base_path)

    async def upload(
        self, file_bytes: bytes, filename: str, tenant_id: str, folder: str = ""
    ) -> str:
        ext = Path(filename).suffix
        unique = f"{uuid.uuid4().hex[:12]}{ext}"

        dir_path = self.base_path / tenant_id
        if folder:
            dir_path = dir_path / folder
        dir_path.mkdir(parents=True, exist_ok=True)

        file_path = dir_path / unique
        file_path.write_bytes(file_bytes)

        result = str(file_path)
        logger.info("Saved locally: %s", result)
        return result

    async def get_url(self, key: str) -> str:
        return str(self.base_path / key)

    async def read_file(self, file_id: str, tenant_id: str, folder: str = "uploads") -> Optional[tuple[bytes, str]]:
        """Read an uploaded file by its ID (UUID stem). Searches tenant's upload dir."""
        import mimetypes
        search_dir = self.base_path / tenant_id
        if folder:
            search_dir = search_dir / folder

        if not search_dir.exists():
            return None

        # Search for file matching the ID (with any extension)
        for path in search_dir.iterdir():
            if path.stem == file_id:
                ct = mimetypes.guess_type(str(path))[0] or "image/png"
                return path.read_bytes(), ct

        return None


def get_storage() -> StorageBackend:
    """Return the active storage backend based on feature flags."""
    flags = get_flags()
    if flags.use_s3:
        return S3Storage()
    return LocalStorage()


def _guess_content_type(filename: str) -> str:
    import mimetypes
    ct, _ = mimetypes.guess_type(filename)
    return ct or "application/octet-stream"
