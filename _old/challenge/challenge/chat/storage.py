import os
from functools import lru_cache
from typing import Any

from chainlit.data.storage_clients.base import BaseStorageClient


@lru_cache
def get_storage_client() -> BaseStorageClient:
    """Client to persist chat artifacts and elements which are not stored in the db"""
    return LocalStorage()


class LocalStorage(BaseStorageClient):
    """Minimal storage client for local development."""

    def __init__(self) -> None:
        self.storage_dir = "./public/upload"
        os.makedirs(self.storage_dir, exist_ok=True)

    async def upload_file(
        self,
        object_key: str,
        data: bytes | str,
        mime: str = "application/octet-stream",
        overwrite: bool = True,
        content_disposition: str | None = None,
    ) -> dict[str, Any]:
        """Upload a file to local storage."""
        file_path = os.path.join(self.storage_dir, object_key)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        if isinstance(data, str):
            data = data.encode("utf-8")

        with open(file_path, "wb") as file:
            file.write(data)

        url = f"{file_path[1:]}"
        return {"object_key": object_key, "url": url}

    async def delete_file(self, object_key: str) -> bool:
        """Delete a file from local storage."""
        file_path = os.path.join(self.storage_dir, object_key)
        if os.path.exists(file_path):
            os.remove(file_path)
            return True
        return False

    async def get_read_url(self, object_key: str) -> str:
        """Get a read URL for a file in local storage."""
        return f"{self.storage_dir[1:]}/{object_key}"

    async def close(self) -> None:
        """Noop to close connection to local storage"""
        pass
