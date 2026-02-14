"""
File upload API — handles product image uploads for agents (e.g. Vera).

POST /v1/upload — Upload a file, returns a file ID and local URL.
GET  /v1/upload/{file_id} — Serve a previously uploaded file.
"""

import base64
import logging
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..core.auth import AuthenticatedUser
from ..core.dependencies import require_tenant
from ..core.storage import get_storage, LocalStorage

logger = logging.getLogger(__name__)

upload_router = APIRouter(tags=["upload"])


class UploadResponse(BaseModel):
    file_id: str
    url: str
    filename: str


class Base64UploadRequest(BaseModel):
    """Accept base64-encoded file data (from the chat UI drop zone)."""
    data: str  # data:image/png;base64,... or raw base64
    filename: str = "upload.png"


@upload_router.post("/upload", response_model=UploadResponse)
async def upload_file(
    request: Base64UploadRequest,
    user: AuthenticatedUser = Depends(require_tenant),
):
    """Upload a file via base64. Returns file_id for use in chat messages."""
    try:
        # Strip data URL prefix if present
        raw = request.data
        if "," in raw:
            raw = raw.split(",", 1)[1]

        file_bytes = base64.b64decode(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 data")

    if len(file_bytes) > 20 * 1024 * 1024:  # 20MB limit
        raise HTTPException(status_code=400, detail="File too large (max 20MB)")

    storage = get_storage()
    path = await storage.upload(
        file_bytes=file_bytes,
        filename=request.filename,
        tenant_id=user.tenant_id,
        folder="uploads",
    )

    # Extract the file ID from the stored path
    file_id = Path(path).stem  # e.g. "a1b2c3d4e5f6"

    # Build URL — for local storage, serve via our endpoint
    if isinstance(storage, LocalStorage):
        url = f"/v1/upload/{file_id}"
    else:
        url = path  # S3 returns full URL

    logger.info("Uploaded file: %s (%d bytes) → %s", request.filename, len(file_bytes), file_id)

    return UploadResponse(file_id=file_id, url=url, filename=request.filename)


@upload_router.get("/upload/{file_id}")
async def serve_file(file_id: str):
    """Serve a locally stored uploaded file."""
    storage = get_storage()
    if not isinstance(storage, LocalStorage):
        raise HTTPException(status_code=404, detail="Direct file serving only in local mode")

    # Search for the file in all tenant upload directories
    base = Path(storage.base_path)
    for path in base.rglob(f"{file_id}.*"):
        ct = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        return FileResponse(path, media_type=ct)

    # Also check without extension
    for path in base.rglob(f"{file_id}"):
        ct = mimetypes.guess_type(str(path))[0] or "image/png"
        return FileResponse(path, media_type=ct)

    raise HTTPException(status_code=404, detail="File not found")
