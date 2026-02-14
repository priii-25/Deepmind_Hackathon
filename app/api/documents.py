"""
Document upload and search endpoints.
No RAG. No embeddings. Full-text search.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.auth import AuthenticatedUser
from ..core.dependencies import require_tenant, get_db, get_storage_dep
from ..core.storage import StorageBackend
from ..models.document import Document
from ..services.ocr import extract_text
from ..services import realtime

logger = logging.getLogger(__name__)

documents_router = APIRouter(tags=["documents"])


class DocumentResponse(BaseModel):
    id: str
    title: Optional[str] = None
    filename: Optional[str] = None
    status: str
    s3_url: Optional[str] = None
    file_size_bytes: Optional[int] = None
    char_count: int = 0


class SearchRequest(BaseModel):
    query: str
    limit: int = 5


class SearchResult(BaseModel):
    title: Optional[str] = None
    filename: Optional[str] = None
    snippet: str = ""


@documents_router.post("/documents/upload", response_model=DocumentResponse)
async def upload_document(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    user: AuthenticatedUser = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage_dep),
):
    """Upload a document. Text extracted and stored for full-text search."""

    # Read file
    file_bytes = await file.read()
    filename = file.filename or "document"

    # Create DB record
    doc = Document(
        tenant_id=user.tenant_id,
        title=title or filename,
        filename=filename,
        status="processing",
        file_size_bytes=len(file_bytes),
    )
    db.add(doc)
    await db.flush()

    await realtime.document_processing(user.tenant_id, doc.id, "processing")

    try:
        # Upload to storage
        url = await storage.upload(file_bytes, filename, user.tenant_id, folder="documents")
        doc.s3_url = url

        # Extract text
        full_text, metadata = await extract_text(file_bytes, filename)
        doc.full_text = full_text
        doc.doc_metadata = metadata
        doc.status = "completed"

        await realtime.document_processing(user.tenant_id, doc.id, "completed")

        logger.info(
            "Document uploaded: %s (%d chars, ocr=%s)",
            filename, len(full_text), metadata.get("used_ocr", False),
        )

    except Exception as e:
        logger.error("Document processing failed: %s", e)
        doc.status = "failed"
        doc.doc_metadata = {"error": str(e)}
        await realtime.document_processing(user.tenant_id, doc.id, "failed")

    await db.flush()

    return DocumentResponse(
        id=doc.id,
        title=doc.title,
        filename=doc.filename,
        status=doc.status,
        s3_url=doc.s3_url,
        file_size_bytes=doc.file_size_bytes,
        char_count=len(doc.full_text or ""),
    )


@documents_router.get("/documents", response_model=list[DocumentResponse])
async def list_documents(
    user: AuthenticatedUser = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """List all documents for the current tenant."""
    result = await db.execute(
        select(Document)
        .where(Document.tenant_id == user.tenant_id)
        .order_by(Document.created_at.desc())
    )
    docs = result.scalars().all()

    return [
        DocumentResponse(
            id=d.id,
            title=d.title,
            filename=d.filename,
            status=d.status,
            s3_url=d.s3_url,
            file_size_bytes=d.file_size_bytes,
            char_count=len(d.full_text or ""),
        )
        for d in docs
    ]


@documents_router.post("/documents/search", response_model=list[SearchResult])
async def search_documents(
    request: SearchRequest,
    user: AuthenticatedUser = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Search documents using full-text search."""
    from ..tools.document_search import search_documents as _search

    raw = await _search(
        query=request.query,
        db=db,
        tenant_id=user.tenant_id,
    )

    # The tool returns formatted text — for the API we re-query with LIKE
    result = await db.execute(
        select(Document)
        .where(
            Document.tenant_id == user.tenant_id,
            (Document.full_text.icontains(request.query))
            | (Document.title.icontains(request.query)),
        )
        .limit(request.limit)
    )
    rows = result.scalars().all()

    return [
        SearchResult(
            title=r.title,
            filename=r.filename,
            snippet=(r.full_text or "")[:300],
        )
        for r in rows
    ]


@documents_router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    user: AuthenticatedUser = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Delete a document."""
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.tenant_id == user.tenant_id,
        )
    )
    doc = result.scalar_one_or_none()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    await db.delete(doc)
    return {"status": "deleted", "id": document_id}
