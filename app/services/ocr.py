"""
Text extraction from documents.
- pdfplumber for normal PDFs (free, local)
- AIML OCR (Google Document AI) for scanned PDFs (flagged)
"""

import base64
import logging
import mimetypes
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Optional

import httpx

from ..core.config import get_settings
from ..core.flags import get_flags

logger = logging.getLogger(__name__)


async def extract_text(file_bytes: bytes, filename: str) -> tuple[str, dict]:
    """
    Extract text from a file. Returns (text, metadata).
    Tries pdfplumber first, falls back to OCR if enabled and text is short.
    """
    ext = Path(filename).suffix.lower()
    metadata = {"filename": filename, "used_ocr": False}

    if ext == ".pdf":
        text = _extract_pdf(file_bytes)
        metadata["extractor"] = "pdfplumber"

        # If text is too short, might be a scanned PDF → try OCR
        flags = get_flags()
        if flags.use_ocr and len(text.strip()) < 100:
            ocr_text = await _ocr_extract(file_bytes, filename)
            if ocr_text and len(ocr_text.strip()) > len(text.strip()):
                text = ocr_text
                metadata["used_ocr"] = True
                metadata["extractor"] = "aiml_ocr"

    elif ext == ".docx":
        text = _extract_docx(file_bytes)
        metadata["extractor"] = "python-docx"

    elif ext in (".txt", ".md", ".csv", ".json"):
        text = file_bytes.decode("utf-8", errors="replace")
        metadata["extractor"] = "plaintext"

    else:
        text = ""
        metadata["extractor"] = "unsupported"

    metadata["char_count"] = len(text)
    return text, metadata


def _extract_pdf(file_bytes: bytes) -> str:
    """Extract text from PDF using pdfplumber (local, free)."""
    try:
        import pdfplumber

        pages_text = []
        with pdfplumber.open(BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                # Also try table extraction
                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        for row in table:
                            if row:
                                text += "\n" + " | ".join(
                                    str(cell) if cell else "" for cell in row
                                )
                pages_text.append(text)
        return "\n\n".join(pages_text)
    except Exception as e:
        logger.error("pdfplumber extraction failed: %s", e)
        return ""


def _extract_docx(file_bytes: bytes) -> str:
    """Extract text from DOCX."""
    try:
        import docx

        doc = docx.Document(BytesIO(file_bytes))
        return "\n\n".join(para.text for para in doc.paragraphs if para.text)
    except Exception as e:
        logger.error("DOCX extraction failed: %s", e)
        return ""


async def _ocr_extract(file_bytes: bytes, filename: str) -> str:
    """OCR via AIML API (Google Document AI). Only called if FF_USE_OCR=true."""
    settings = get_settings()
    if not settings.aiml_api_key:
        logger.warning("OCR requested but AIML_API_KEY not set")
        return ""

    try:
        encoded = base64.b64encode(file_bytes).decode("utf-8")
        mime_type = mimetypes.guess_type(filename)[0] or "application/pdf"

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{settings.aiml_base_url.rstrip('/')}/ocr",
                json={
                    "model": settings.aiml_ocr_model,
                    "document": encoded,
                    "mimeType": mime_type,
                },
                headers={
                    "Authorization": f"Bearer {settings.aiml_api_key}",
                    "Content-Type": "application/json",
                },
            )

            if resp.status_code not in (200, 201):
                logger.error("OCR API error: %s %s", resp.status_code, resp.text[:200])
                return ""

            result = resp.json()

            # Try root text field first
            if result.get("text"):
                return result["text"]

            # Try pages fallback
            texts = []
            for page in result.get("pages", []):
                if page.get("markdown"):
                    texts.append(page["markdown"])
                elif page.get("text"):
                    texts.append(page["text"])

            return "\n\n".join(texts)

    except Exception as e:
        logger.error("OCR failed: %s", e)
        return ""
