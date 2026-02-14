"""
File upload API — handles image and video uploads.

POST /v1/upload      — Upload via base64 (images, small files)
POST /v1/upload/file — Upload via multipart form (videos, large files up to 500MB)
GET  /v1/upload/{file_id} — Serve a previously uploaded file
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

# ── Size limits ───────────────────────────────────────────────────────

MAX_BASE64_SIZE = 20 * 1024 * 1024       # 20 MB for base64 uploads (images)
MAX_MULTIPART_SIZE = 500 * 1024 * 1024   # 500 MB for multipart uploads (videos)

# ── Allowed file types ────────────────────────────────────────────────

ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tiff"}
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv", ".m4v", ".3gp"}
ALLOWED_DOC_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".csv", ".xlsx"}
ALLOWED_EXTENSIONS = ALLOWED_IMAGE_EXTENSIONS | ALLOWED_VIDEO_EXTENSIONS | ALLOWED_DOC_EXTENSIONS


# ── Response model ────────────────────────────────────────────────────

class UploadResponse(BaseModel):
    file_id: str
    url: str
    filename: str
    size: int = 0
    content_type: str = ""


# ── POST /v1/upload (base64 — for images and small files) ────────────

class Base64UploadRequest(BaseModel):
    """Accept base64-encoded file data (from the chat UI drop zone)."""
    data: str  # data:image/png;base64,... or raw base64
    filename: str = "upload.png"


@upload_router.post("/upload", response_model=UploadResponse)
async def upload_base64(
    request: Base64UploadRequest,
    user: AuthenticatedUser = Depends(require_tenant),
):
    """
    Upload a file via base64 encoding.

    Best for images and small files (< 20 MB).
    For videos and large files, use POST /v1/upload/file instead.
    """
    try:
        raw = request.data
        if "," in raw:
            raw = raw.split(",", 1)[1]
        file_bytes = base64.b64decode(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 data")

    if len(file_bytes) > MAX_BASE64_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large for base64 upload (max {MAX_BASE64_SIZE // (1024*1024)}MB). "
                   "Use POST /v1/upload/file for larger files.",
        )

    _validate_extension(request.filename)

    storage = get_storage()
    path = await storage.upload(
        file_bytes=file_bytes,
        filename=request.filename,
        tenant_id=user.tenant_id,
        folder="uploads",
    )

    file_id = Path(path).stem
    url = _build_url(storage, file_id, path)
    ct = mimetypes.guess_type(request.filename)[0] or "application/octet-stream"

    logger.info("Uploaded (base64): %s (%d bytes) → %s", request.filename, len(file_bytes), file_id)

    return UploadResponse(
        file_id=file_id, url=url, filename=request.filename,
        size=len(file_bytes), content_type=ct,
    )


# ── POST /v1/upload/file (multipart — for videos and large files) ────

@upload_router.post("/upload/file", response_model=UploadResponse)
async def upload_multipart(
    file: UploadFile = File(..., description="Video or image file to upload"),
    user: AuthenticatedUser = Depends(require_tenant),
):
    """
    Upload a file via multipart form data.

    Supports videos (mp4, mov, avi, mkv, webm, etc.) up to 500 MB
    and images (png, jpg, gif, webp, etc.) up to 500 MB.

    Use this endpoint for video uploads to YouTube.

    Example:
        curl -X POST http://localhost:8000/v1/upload/file -F "file=@video.mp4"
    """
    filename = file.filename or "upload"
    _validate_extension(filename)

    # Read file content in chunks to handle large files
    file_bytes = await file.read()

    if len(file_bytes) > MAX_MULTIPART_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large (max {MAX_MULTIPART_SIZE // (1024*1024)}MB). "
                   "Try compressing the video first.",
        )

    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    storage = get_storage()
    path = await storage.upload(
        file_bytes=file_bytes,
        filename=filename,
        tenant_id=user.tenant_id,
        folder="uploads",
    )

    file_id = Path(path).stem
    url = _build_url(storage, file_id, path)
    ct = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    size_mb = len(file_bytes) / (1024 * 1024)

    logger.info("Uploaded (multipart): %s (%.1f MB, %s) → %s", filename, size_mb, ct, file_id)

    return UploadResponse(
        file_id=file_id, url=url, filename=filename,
        size=len(file_bytes), content_type=ct,
    )


# ── GET /v1/upload/{file_id} — serve uploaded file ───────────────────

@upload_router.get("/upload/{file_id}")
async def serve_file(file_id: str):
    """Serve a locally stored uploaded file (images, videos, documents)."""
    storage = get_storage()
    if not isinstance(storage, LocalStorage):
        raise HTTPException(status_code=404, detail="Direct file serving only in local mode")

    base = Path(storage.base_path)

    # Search for the file in all tenant upload directories
    for path in base.rglob(f"{file_id}.*"):
        ct = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        return FileResponse(path, media_type=ct)

    # Also check without extension
    for path in base.rglob(f"{file_id}"):
        ct = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        return FileResponse(path, media_type=ct)

    raise HTTPException(status_code=404, detail="File not found")


# ── Helpers ───────────────────────────────────────────────────────────

def _validate_extension(filename: str) -> None:
    """Validate file extension against allowed types."""
    ext = Path(filename).suffix.lower()
    if ext and ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{ext}' not allowed. "
                   f"Supported: images ({', '.join(sorted(ALLOWED_IMAGE_EXTENSIONS))}), "
                   f"videos ({', '.join(sorted(ALLOWED_VIDEO_EXTENSIONS))}), "
                   f"documents ({', '.join(sorted(ALLOWED_DOC_EXTENSIONS))})",
        )


def _build_url(storage, file_id: str, path: str) -> str:
    """Build the URL/path for an uploaded file."""
    if isinstance(storage, LocalStorage):
        return f"/v1/upload/{file_id}"
    return path  # S3 returns full URL
