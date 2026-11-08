"""Bounded, sequential image/PDF orchestration shared by HTTP and legacy tools."""

import math
import tempfile
from pathlib import Path
from typing import Any

import pymupdf

from .backends.ovisocr3_backend import OpenAICompatibleOCRBackend
from .core.http_config import RemoteOCRConfig


def error(code: str, message: str) -> dict[str, Any]:
    return {"success": False, "error_code": code, "error": message}


async def process_remote_document(
    source_path: str,
    backend: OpenAICompatibleOCRBackend,
    config: RemoteOCRConfig,
    *,
    mode: str = "markdown",
    pages: list[int] | None = None,
    dpi: int = 200,
    force_ocr: bool = False,
    region: list[int] | None = None,
) -> dict[str, Any]:
    if mode not in {"text", "markdown", "format", "auto"}:
        return error("INVALID_MODE", "Supported modes: text, markdown, format, auto")
    if type(dpi) is not int or not 150 <= dpi <= 300:
        return error("INVALID_DPI", "DPI must be an integer from 150 to 300")
    try:
        path = Path(source_path).expanduser().resolve()
        if config.document_root is not None and not path.is_relative_to(config.document_root.resolve()):
            return error("PATH_NOT_ALLOWED", "Document must be inside OCR_DOCUMENT_ROOT")
        if not path.is_file():
            return error("FILE_NOT_FOUND", "Document does not exist or is not a file")
        if path.stat().st_size > config.max_file_bytes:
            return error("FILE_LIMIT", "Document exceeds OCR_MAX_FILE_BYTES")
        if path.suffix.lower() != ".pdf":
            if pages is not None:
                return error("INVALID_PAGES", "Page selection is only supported for PDFs")
            return await backend.process_image(str(path), mode, region=region)
        if region is not None:
            return error("INVALID_REGION", "Region cropping is only supported for images")
        with pymupdf.open(path) as document:
            if document.needs_pass:
                return error("ENCRYPTED_PDF", "Password-protected PDFs are unsupported")
            if not document.is_pdf:
                return error("INVALID_PDF", "File is not a PDF")
            if pages is not None and (
                not pages or any(type(n) is not int or n < 1 or n > len(document) for n in pages)
            ):
                return error("INVALID_PAGES", "Pages must be nonempty 1-based PDF page numbers")
            if pages is None and len(document) > config.max_pdf_pages:
                return error("PAGE_LIMIT", "Select fewer PDF pages with the pages parameter")
            selected = sorted(set(pages)) if pages is not None else list(range(1, len(document) + 1))
            if not selected or len(selected) > config.max_pdf_pages:
                return error("PAGE_LIMIT", "Selected page count exceeds OCR_MAX_PDF_PAGES or PDF is empty")
            results = []
            sections = []
            remaining = config.max_output_chars
            with tempfile.TemporaryDirectory(prefix="ocr-mcp-pages-") as temporary:
                for number in selected:
                    try:
                        page = document[number - 1]
                        text = page.get_text().strip()
                        # Any raster content may contain text (including scans with digital headers).
                        # OCR the whole page rather than silently dropping that content.
                        use_text = bool(text) and not force_ocr and not page.get_images()
                        if use_text:
                            result = {
                                "success": True,
                                "text": text,
                                "backend": "pymupdf",
                                "confidence": None,
                                "confidence_source": "not_provided",
                                "format": "text",
                            }
                            method = "digital_text"
                        else:
                            method = "ocr"
                            if remaining <= 0:
                                raise ValueError("Document output limit reached")
                            scale = dpi / 72
                            if (
                                math.ceil(page.rect.width * scale) * math.ceil(page.rect.height * scale)
                                > config.max_image_pixels
                            ):
                                raise ValueError("Rendered page exceeds OCR_MAX_IMAGE_PIXELS")
                            image_path = Path(temporary) / "page.png"
                            try:
                                page.get_pixmap(
                                    matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False
                                ).save(image_path)
                                result = await backend.process_image(str(image_path), mode)
                            finally:
                                image_path.unlink(missing_ok=True)
                        if result.get("success") and len(result["text"]) > remaining:
                            result = error("OUTPUT_LIMIT", "Page exceeds remaining OCR_MAX_OUTPUT_CHARS")
                        if result.get("success"):
                            remaining -= len(result["text"])
                    except Exception:
                        # No raw exception text: provider data and local paths must not leak.
                        method = "ocr"
                        result = error(
                            "PAGE_PROCESSING_FAILED", "Could not extract or render page within configured limits"
                        )
                    results.append({**result, "page": number, "method": method})
                    body = (
                        result["text"]
                        if result.get("success")
                        else f"[OCR failed: {result.get('error_code', 'UNKNOWN')}]"
                    )
                    sections.append(f"# Page {number}\n\n{body}")
            completed = sum(bool(r.get("success")) for r in results)
            return {
                "success": completed == len(results),
                "partial_success": 0 < completed < len(results),
                "backend": backend.name,
                "format": "text" if mode == "text" else "markdown",
                "text": "\n\n---\n\n".join(sections),
                "pages": results,
                "page_count": len(document),
                "processed_pages": completed,
                "failed_pages": len(results) - completed,
                "confidence": None,
                "confidence_source": "not_provided",
            }
    except (OSError, RuntimeError, ValueError, pymupdf.FileDataError):
        return error("INVALID_DOCUMENT", "Document is unreadable, corrupt or unsupported")
